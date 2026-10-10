"""Project finance cashflow model for a renewable energy project.

``ProjectCashflowModel`` builds an annual cashflow (construction + operation), applies the
joint impact of any number of :class:`risk_objects.Risk` objects, sizes and services debt,
and returns financial metrics (LCOE, NPV, IRRs, DSCR, LLCR, debt capacity).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
import pandas as pd

from risk_objects import Risk, RiskContext, RiskImpact, combine_impacts
from intervention_objects import Intervention, apply_interventions

HOURS_PER_YEAR = 8760

TECHNOLOGY_PRESETS: dict[str, dict[str, float]] = {
    "Onshore wind": dict(
        capex_per_kw=1450, fixed_opex_per_kw_yr=40, variable_opex_per_mwh=2, capacity_factor_pct=33,
        degradation_pct=0.3, lifetime_years=25, construction_years=2,
    ),
    "Solar PV": dict(
        capex_per_kw=900, fixed_opex_per_kw_yr=15, variable_opex_per_mwh=0, capacity_factor_pct=22,
        degradation_pct=0.5, lifetime_years=30, construction_years=1,
    ),
    "Offshore wind": dict(
        capex_per_kw=3200, fixed_opex_per_kw_yr=80, variable_opex_per_mwh=3, capacity_factor_pct=45,
        degradation_pct=0.3, lifetime_years=30, construction_years=3,
    ),
}


@dataclass
class ProjectInputs:
    """Base-case project inputs. Money in $, outputs reported in $m."""

    technology: str = "Onshore wind"
    capacity_mw: float = 100.0
    capex_per_kw: float = 1450.0
    fixed_opex_per_kw_yr: float = 40.0
    variable_opex_per_mwh: float = 2.0
    capacity_factor_pct: float = 33.0
    degradation_pct: float = 0.3
    lifetime_years: int = 25
    construction_years: int = 2
    start_year: int = 2026
    # Revenue
    ppa_price: float = 65.0
    ppa_tenor: int = 15
    merchant_price: float = 55.0
    price_escalation_pct: float = 2.0
    opex_escalation_pct: float = 2.0
    # Financing
    risk_free_rate_pct: float = 4.0
    debt_margin_bps: float = 175.0
    equity_premium_bps: float = 450.0
    gearing_pct: float = 70.0
    debt_tenor_years: int = 18
    target_dscr: float = 1.30
    tax_rate_pct: float = 25.0
    depreciation_years: int = 20

    @classmethod
    def from_preset(cls, technology: str, **overrides) -> ProjectInputs:
        return cls(technology=technology, **{**TECHNOLOGY_PRESETS[technology], **overrides})

    @property
    def capex_total(self) -> float:
        """Unrisked capex, $m."""
        return self.capacity_mw * 1000 * self.capex_per_kw / 1e6


@dataclass(frozen=True)
class CostOfCapital:
    """Cost of capital build-up: risk-free + baseline project premium + risk premiums (fractions)."""

    risk_free: float
    debt_margin: float
    equity_premium: float
    risk_debt_premium: float
    risk_equity_premium: float
    gearing: float
    tax_rate: float

    @property
    def cost_of_debt(self) -> float:
        return self.risk_free + self.debt_margin + self.risk_debt_premium

    @property
    def cost_of_equity(self) -> float:
        return self.risk_free + self.equity_premium + self.risk_equity_premium

    @property
    def wacc(self) -> float:
        """Nominal post-tax WACC."""
        return self.gearing * self.cost_of_debt * (1 - self.tax_rate) + (1 - self.gearing) * self.cost_of_equity


@dataclass
class CashflowResult:
    inputs: ProjectInputs
    risks: list[Risk]
    coc: CostOfCapital
    impact: RiskImpact
    table: pd.DataFrame
    metrics: dict[str, float]
    risk_impacts: dict[str, RiskImpact] = field(default_factory=dict)
    risk_cash_impact: pd.DataFrame = field(default_factory=pd.DataFrame)
    raw_risk_impacts: dict[str, RiskImpact] = field(default_factory=dict)


def npv(rate: float, cashflows: np.ndarray) -> float:
    """NPV with the first cashflow at t=0."""
    return float(np.sum(cashflows / (1 + rate) ** np.arange(len(cashflows))))


def irr(cashflows: np.ndarray, lo: float = -0.99, hi: float = 1.0, tol: float = 1e-7) -> float:
    """IRR by bisection; NaN when the cashflow has no sign change in the search range."""
    f_lo, f_hi = npv(lo, cashflows), npv(hi, cashflows)
    if np.sign(f_lo) == np.sign(f_hi):
        return float("nan")
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid, cashflows)
        if abs(f_mid) < tol or hi - lo < tol:
            return mid
        if np.sign(f_mid) == np.sign(f_lo):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return (lo + hi) / 2


class ProjectCashflowModel:
    """Annual project-finance cashflow model with pluggable risks.

    Parameters
    ----------
    inputs : base-case project inputs.
    risks : risks applied jointly (factors multiply, costs and premiums add).
    rng : random generator for sampled events and systemic severities; None uses expected impacts.
    include_premiums : if False, risk premiums are excluded from the cost of capital,
        isolating the pure cashflow effect of the risks.
    """

    def __init__(
        self,
        inputs: ProjectInputs,
        risks: Sequence[Risk] = (),
        *,
        rng: np.random.Generator | None = None,
        severity_cv: float = 0.25,
        impact_overrides: dict[str, RiskImpact] | None = None,
        interventions: Sequence[Intervention] = (),
        include_premiums: bool = True,
    ) -> None:
        self.inputs = inputs
        self.risks = list(risks)
        self.rng = rng
        self.severity_cv = severity_cv
        self.impact_overrides = impact_overrides
        self.interventions = list(interventions)
        self.include_premiums = include_premiums

    def context(self) -> RiskContext:
        i = self.inputs
        return RiskContext(
            construction_years=i.construction_years,
            lifetime_years=i.lifetime_years,
            capex_total=i.capex_total,
            ppa_price=i.ppa_price,
            ppa_tenor=i.ppa_tenor,
            merchant_price=i.merchant_price,
            rng=self.rng,
            severity_cv=self.severity_cv,
            debt_tenor_years=min(i.debt_tenor_years, i.lifetime_years),
        )

    def cost_of_capital(self, impact: RiskImpact) -> CostOfCapital:
        i = self.inputs
        scale = 1e-4 if self.include_premiums else 0.0
        return CostOfCapital(
            risk_free=i.risk_free_rate_pct / 100,
            debt_margin=i.debt_margin_bps / 1e4,
            equity_premium=i.equity_premium_bps / 1e4,
            risk_debt_premium=impact.premium_debt_bps * scale,
            risk_equity_premium=impact.premium_equity_bps * scale,
            gearing=i.gearing_pct / 100,
            tax_rate=i.tax_rate_pct / 100,
        )

    def run(self) -> CashflowResult:
        i = self.inputs
        ctx = self.context()
        n, C = ctx.n_years, i.construction_years
        t = np.arange(n)
        o = ctx.op_year
        op = ctx.operating_mask

        raw_risk_impacts = self.impact_overrides if self.impact_overrides is not None else {r.risk_id: r.evaluate(ctx) for r in self.risks}
        risk_impacts, fees = apply_interventions(self.risks, raw_risk_impacts, self.interventions, ctx)
        impact = combine_impacts(list(risk_impacts.values()), n)
        intervention_capex = np.zeros(n)
        intervention_fees = np.zeros(n)
        for fee in fees:
            intervention_capex += fee.capex_factor - 1
            intervention_fees += fee.extra_cost
        impact.capex_factor += intervention_capex
        impact.extra_cost += intervention_fees
        coc = self.cost_of_capital(impact)

        price_esc = (1 + i.price_escalation_pct / 100) ** t
        opex_esc = (1 + i.opex_escalation_pct / 100) ** t

        # Construction
        capex_base = np.where(op, 0.0, i.capex_total / C)
        capex = capex_base * impact.capex_factor
        total_capex = capex.sum()

        # Generation & revenue
        gen_base = np.where(
            op, i.capacity_mw * HOURS_PER_YEAR * i.capacity_factor_pct / 100 * (1 - i.degradation_pct / 100) ** np.maximum(o - 1, 0), 0.0
        )
        gen = gen_base * impact.generation_factor
        price_base = np.where(op, np.where(o <= i.ppa_tenor, i.ppa_price, i.merchant_price) * price_esc, 0.0)
        price = price_base * impact.price_factor
        revenue_base = gen_base * price_base / 1e6
        revenue = gen * price / 1e6

        # Operating costs
        fixed_opex = i.capacity_mw * 1000 * i.fixed_opex_per_kw_yr / 1e6
        opex_base = np.where(op, (fixed_opex + i.variable_opex_per_mwh * gen_base / 1e6) * opex_esc, 0.0)
        opex = np.where(op, (fixed_opex + i.variable_opex_per_mwh * gen / 1e6) * opex_esc * impact.opex_factor, 0.0)
        extra_cost = impact.extra_cost * opex_esc
        ebitda = revenue - opex - extra_cost

        depreciation = np.where(op & (o <= i.depreciation_years), total_capex / max(i.depreciation_years, 1), 0.0)

        # Debt: drawn pro-rata with capex, repaid as an annuity from COD (IDC assumed within capex)
        kd = coc.cost_of_debt
        debt = coc.gearing * total_capex
        tenor = int(min(i.debt_tenor_years, i.lifetime_years))
        annuity = debt * kd / (1 - (1 + kd) ** -tenor) if kd > 0 else debt / tenor
        interest, principal = np.zeros(n), np.zeros(n)
        balance = debt
        for k in range(C, C + tenor):
            interest[k] = balance * kd
            principal[k] = annuity - interest[k]
            balance -= principal[k]
        debt_service = interest + principal

        tax = np.maximum(ebitda - depreciation - interest, 0.0) * coc.tax_rate
        cfads = ebitda - tax
        with np.errstate(divide="ignore", invalid="ignore"):
            dscr = np.where(debt_service > 0, cfads / debt_service, np.nan)
        equity_cf = -capex * (1 - coc.gearing) + cfads - debt_service
        unlevered_tax = np.maximum(ebitda - depreciation, 0.0) * coc.tax_rate
        fcff = -capex + ebitda - unlevered_tax

        disc = (1 + coc.wacc) ** -t.astype(float)
        table = pd.DataFrame(
            {
                "year": i.start_year + t,
                "op_year": o,
                "phase": np.where(op, "Operation", "Construction"),
                "generation_base_mwh": gen_base,
                "generation_mwh": gen,
                "price_base": price_base,
                "price": price,
                "revenue_base": revenue_base,
                "revenue": revenue,
                "revenue_lost": revenue_base - revenue,
                "opex_base": opex_base,
                "opex": opex,
                "risk_extra_cost": extra_cost,
                "capex_base": capex_base,
                "capex": capex,
                "capex_overrun": capex - capex_base,
                "intervention_capex": capex_base * intervention_capex,
                "intervention_fees": intervention_fees * opex_esc,
                "ebitda": ebitda,
                "depreciation": depreciation,
                "interest": interest,
                "principal": principal,
                "debt_service": debt_service,
                "tax": tax,
                "unlevered_tax": unlevered_tax,
                "cfads": cfads,
                "dscr": dscr,
                "fcff": fcff,
                "equity_cf": equity_cf,
                "cum_equity_cf": np.cumsum(equity_cf),
                "discount_factor": disc,
            }
        )

        debt_years = debt_service > 0
        cod_disc = (1 + kd) ** -np.where(debt_years, o, 0).astype(float)
        pv_cfads_debt = float(np.sum(np.where(debt_years, cfads * cod_disc, 0.0)))
        payback = np.flatnonzero(op & (table["cum_equity_cf"].to_numpy() >= 0))
        pv_gen = max(np.sum(gen * disc), 1e-9)
        metrics = {
            "lcoe": float(np.sum((capex + opex + extra_cost) * disc) / pv_gen * 1e6),
            "levelised_revenue": float(np.sum(revenue * disc) / pv_gen * 1e6),
            "npv": float(np.sum(fcff * disc)),
            "equity_npv": npv(coc.cost_of_equity, equity_cf),
            "project_irr": irr(fcff),
            "equity_irr": irr(equity_cf),
            "min_dscr": float(np.nanmin(dscr)) if debt_years.any() else float("nan"),
            "avg_dscr": float(np.nanmean(dscr)) if debt_years.any() else float("nan"),
            "llcr": pv_cfads_debt / debt if debt > 0 else float("nan"),
            "debt": debt,
            "debt_capacity": max(0.0, pv_cfads_debt / i.target_dscr),
            "max_gearing": np.clip(pv_cfads_debt / i.target_dscr / total_capex, 0, 1) if total_capex > 0 else float("nan"),
            "total_capex": total_capex,
            "capex_overrun": float(total_capex - capex_base.sum()),
            "revenue_lost_pv": float(np.sum((revenue_base - revenue) * disc)),
            "lifetime_generation_twh": float(gen.sum() / 1e6),
            "equity_payback_years": float(o[payback[0]]) if payback.size else float("nan"),
            "cost_of_debt": coc.cost_of_debt,
            "cost_of_equity": coc.cost_of_equity,
            "wacc": coc.wacc,
        }

        # Indicative standalone cash impact of each risk on the unrisked drivers ($m, +ve = loss)
        cash_impact = {}
        for r in self.risks:
            ri = risk_impacts[r.risk_id]
            cash_impact[r.name] = (
                revenue_base * (1 - ri.generation_factor * ri.price_factor)
                + opex_base * (ri.opex_factor - 1)
                + ri.extra_cost * opex_esc
                + capex_base * (ri.capex_factor - 1)
            )
        risk_cash_impact = pd.DataFrame(cash_impact, index=i.start_year + t).T

        return CashflowResult(
            inputs=i,
            risks=self.risks,
            coc=coc,
            impact=impact,
            table=table,
            metrics=metrics,
            risk_impacts=risk_impacts,
            risk_cash_impact=risk_cash_impact,
            raw_risk_impacts=raw_risk_impacts,
        )


# --------------------------------------------------------------------------- #
# Scenario analysis
# --------------------------------------------------------------------------- #
ATTRIBUTION_METRICS = ("lcoe", "npv", "equity_irr", "min_dscr", "wacc")


@dataclass
class ScenarioAnalysis:
    baseline: CashflowResult
    risked: CashflowResult
    cashflow_only: CashflowResult  # risks applied to cashflows, no cost-of-capital premium
    attribution: pd.DataFrame
    cashflow_equivalent_premium_bps: float
    unmitigated: CashflowResult | None = None
    interventions: list[Intervention] = field(default_factory=list)
    intervention_attribution: pd.DataFrame = field(default_factory=pd.DataFrame)


def cashflow_equivalent_premium(baseline: CashflowResult, cashflow_only: CashflowResult) -> float:
    """Discount-rate uplift (bps) on the unrisked cashflow that reproduces the risked-cashflow NPV.

    This is the premium over the baseline WACC justified purely by expected cashflow losses.
    """
    base_fcff = baseline.table["fcff"].to_numpy()
    target = npv(baseline.coc.wacc, cashflow_only.table["fcff"].to_numpy())
    lo, hi = baseline.coc.wacc - 0.10, baseline.coc.wacc + 0.50
    f = lambda r: npv(r, base_fcff) - target  # noqa: E731
    if np.sign(f(lo)) == np.sign(f(hi)):
        return float("nan")
    for _ in range(100):
        mid = (lo + hi) / 2
        if np.sign(f(mid)) == np.sign(f(lo)):
            lo = mid
        else:
            hi = mid
    return ((lo + hi) / 2 - baseline.coc.wacc) * 1e4


def analyse(inputs: ProjectInputs, risks: Sequence[Risk], *, impact_overrides: dict[str, RiskImpact] | None = None,
            interventions: Sequence[Intervention] = ()) -> ScenarioAnalysis:
    """Run baseline, joint-risk and per-risk standalone cases and attribute the impacts."""
    baseline = ProjectCashflowModel(inputs).run()
    unmitigated = ProjectCashflowModel(inputs, risks, impact_overrides=impact_overrides).run()
    raw = unmitigated.raw_risk_impacts
    risked = ProjectCashflowModel(inputs, risks, impact_overrides=raw, interventions=interventions).run()
    cashflow_only = ProjectCashflowModel(inputs, risks, impact_overrides=raw, interventions=interventions, include_premiums=False).run()
    for column in ("fcff", "equity_cf", "cfads", "revenue", "capex", "opex", "debt_service"):
        risked.table[f"intervention_delta_{column}"] = risked.table[column] - unmitigated.table[column]

    def delta(result: CashflowResult) -> dict[str, float]:
        return {k: result.metrics[k] - baseline.metrics[k] for k in ATTRIBUTION_METRICS}

    rows = []
    for r in risks:
        standalone = {r.risk_id: raw[r.risk_id]}
        alone = ProjectCashflowModel(inputs, [r], impact_overrides=standalone).run()
        alone_cf = ProjectCashflowModel(inputs, [r], impact_overrides=standalone, include_premiums=False).run()
        d = delta(alone)
        rows.append(
            {
                "risk": r.name,
                "category": r.category,
                "kind": r.kind,
                **d,
                "lcoe_cashflow": alone_cf.metrics["lcoe"] - baseline.metrics["lcoe"],
                "lcoe_cost_of_capital": d["lcoe"] - (alone_cf.metrics["lcoe"] - baseline.metrics["lcoe"]),
                "premium_debt_bps": alone.impact.premium_debt_bps,
                "premium_equity_bps": alone.impact.premium_equity_bps,
            }
        )
    intervention_rows = []
    previous = unmitigated
    previous_cf = ProjectCashflowModel(inputs, risks, impact_overrides=raw, include_premiums=False).run()
    for index, intervention in enumerate(interventions):
        current = ProjectCashflowModel(inputs, risks, impact_overrides=raw, interventions=interventions[:index + 1]).run()
        current_cf = ProjectCashflowModel(inputs, risks, impact_overrides=raw, interventions=interventions[:index + 1], include_premiums=False).run()
        contribution = {key: current.metrics[key] - previous.metrics[key] for key in ATTRIBUTION_METRICS}
        lcoe_cashflow = current_cf.metrics["lcoe"] - previous_cf.metrics["lcoe"]
        record = {"risk": intervention.name, "category": "De-risking", "kind": "intervention", **contribution,
                  "lcoe_cashflow": lcoe_cashflow, "lcoe_cost_of_capital": contribution["lcoe"] - lcoe_cashflow,
                  "premium_debt_bps": current.impact.premium_debt_bps - previous.impact.premium_debt_bps,
                  "premium_equity_bps": current.impact.premium_equity_bps - previous.impact.premium_equity_bps}
        rows.append(record)
        intervention_rows.append(record)
        risked.risk_cash_impact.loc[f"De-risking: {intervention.name}"] = -(current.table.fcff - previous.table.fcff).to_numpy()
        previous = current
        previous_cf = current_cf
    attribution = pd.DataFrame(rows)
    if rows:
        total = delta(risked)
        interaction = {k: total[k] - attribution[k].sum() for k in ATTRIBUTION_METRICS}
        attribution = pd.concat(
            [attribution, pd.DataFrame([{"risk": "Interaction effects", "category": "-", "kind": "-", **interaction}])],
            ignore_index=True,
        )

    return ScenarioAnalysis(
        baseline=baseline,
        risked=risked,
        cashflow_only=cashflow_only,
        attribution=attribution,
        cashflow_equivalent_premium_bps=cashflow_equivalent_premium(baseline, cashflow_only) if risks else 0.0,
        unmitigated=unmitigated,
        interventions=list(interventions),
        intervention_attribution=pd.DataFrame(intervention_rows),
    )


@dataclass
class SimulationAnalysis:
    metrics: pd.DataFrame
    annual_fcff: np.ndarray
    selected: ScenarioAnalysis
    exceedance_pct: float
    seed: int
    unmitigated_metrics: pd.DataFrame = field(default_factory=pd.DataFrame)

    def percentiles(self) -> pd.DataFrame:
        levels = [50, 75, 90, 95, 99]
        columns = ["npv", "equity_irr", "min_dscr", "lcoe", "lifetime_generation_twh"]
        return pd.DataFrame(
            {column: [self.metrics[column].quantile(level / 100 if column == "lcoe" else 1 - level / 100) for level in levels] for column in columns},
            index=[f"P{level}" for level in levels],
        )


def simulate(inputs: ProjectInputs, risks: Sequence[Risk], *, draws: int = 500,
             exceedance_pct: float = 90, seed: int = 42, severity_cv: float = 0.25,
             interventions: Sequence[Intervention] = ()) -> SimulationAnalysis:
    """Independent event draws and lognormal severities; select a coherent path by NPV exceedance rank."""
    if draws < 2 or not 0 < exceedance_pct < 100 or severity_cv < 0:
        raise ValueError("Need >=2 draws, 0<P<100 and nonnegative severity uncertainty")
    generator = np.random.default_rng(seed)
    raw_results = [ProjectCashflowModel(inputs, risks, rng=generator, severity_cv=severity_cv).run() for _ in range(draws)]
    results = [ProjectCashflowModel(inputs, risks, impact_overrides=result.raw_risk_impacts, interventions=interventions).run()
               for result in raw_results] if interventions else raw_results
    metrics = pd.DataFrame([result.metrics for result in results])
    ordered = np.argsort(metrics["npv"].to_numpy(), kind="stable")
    rank = int(round((1 - exceedance_pct / 100) * (draws - 1)))
    chosen = results[int(ordered[rank])]
    return SimulationAnalysis(
        metrics=metrics,
        annual_fcff=np.stack([result.table["fcff"].to_numpy() for result in results]),
        selected=analyse(inputs, risks, impact_overrides=chosen.raw_risk_impacts, interventions=interventions),
        exceedance_pct=exceedance_pct,
        seed=seed,
        unmitigated_metrics=pd.DataFrame([result.metrics for result in raw_results]),
    )
