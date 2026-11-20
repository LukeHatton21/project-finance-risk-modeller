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
        """Create ProjectInputs from a technology preset, optionally overriding specific parameters.
        
        Parameters
        ----------
        technology : str
            Technology name (e.g., "Onshore wind", "Solar PV", "Offshore wind").
        **overrides
            Parameter overrides to apply to the technology preset.
        
        Returns
        -------
        ProjectInputs
            A new ProjectInputs instance with preset values and applied overrides.
        """
        return cls(technology=technology, **{**TECHNOLOGY_PRESETS[technology], **overrides})

    @property
    def capex_total(self) -> float:
        """Calculate total unrisked capital expenditure in millions of dollars.
        
        Computes capex based on project capacity and per-kW cost.
        
        Returns
        -------
        float
            Total capex in $m.
        """
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
        """Calculate nominal cost of debt as sum of risk-free rate, debt margin, and risk premium.
        
        Returns
        -------
        float
            Cost of debt as a decimal fraction.
        """
        return self.risk_free + self.debt_margin + self.risk_debt_premium

    @property
    def cost_of_equity(self) -> float:
        """Calculate nominal cost of equity as sum of risk-free rate, equity premium, and risk premium.
        
        Returns
        -------
        float
            Cost of equity as a decimal fraction.
        """
        return self.risk_free + self.equity_premium + self.risk_equity_premium

    @property
    def wacc(self) -> float:
        """Calculate nominal post-tax weighted average cost of capital.
        
        Blends cost of debt (after tax) and cost of equity based on gearing ratio.
        
        Returns
        -------
        float
            WACC as a decimal fraction.
        """
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
    """Calculate net present value of cashflows at a given discount rate.
    
    Assumes the first cashflow occurs at t=0 and subsequent cashflows are discounted.
    
    Parameters
    ----------
    rate : float
        Discount rate as a decimal fraction.
    cashflows : np.ndarray
        Array of cashflows over time.
    
    Returns
    -------
    float
        Net present value.
    """
    return float(np.sum(cashflows / (1 + rate) ** np.arange(len(cashflows))))


def irr(cashflows: np.ndarray, lo: float = -0.99, hi: float = 1.0, tol: float = 1e-7) -> float:
    """Calculate internal rate of return using bisection method.
    
    Finds the discount rate where NPV equals zero. Returns NaN if no sign change exists
    in the search range, indicating no IRR within that range.
    
    Parameters
    ----------
    cashflows : np.ndarray
        Array of cashflows over time.
    lo : float, optional
        Lower bound of search range (default: -0.99).
    hi : float, optional
        Upper bound of search range (default: 1.0).
    tol : float, optional
        Convergence tolerance (default: 1e-7).
    
    Returns
    -------
    float
        Internal rate of return as a decimal fraction, or NaN if no IRR found.
    """
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
        """Build a RiskContext object for evaluating risks with project-specific parameters.
        
        Packages key project timing and financial parameters needed for risk evaluation,
        such as construction/operation timeline, capex amount, and PPA terms.
        
        Returns
        -------
        RiskContext
            Context object containing project parameters for risk evaluation.
        """
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
        """Build cost-of-capital assumptions incorporating risk premiums.
        
        Scales risk premiums based on the include_premiums flag and converts basis points
        to decimal fractions.
        
        Parameters
        ----------
        impact : RiskImpact
            Risk impact object containing debt and equity premium adjustments (bps).
        
        Returns
        -------
        CostOfCapital
            Cost of capital structure with risk-adjusted rates.
        """
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
        """Execute the full project finance cashflow model.
        
        Builds a detailed year-by-year cashflow table incorporating:
        - Construction capex (risked and mitigated)
        - Generation with performance degradation (risked by factors)
        - Revenue under PPA and merchant pricing (risked by factors)
        - Operating costs (risked by factors)
        - Debt service with annuity repayment
        - Tax and financial metrics (NPV, IRR, LCOE, DSCR, LLCR)
        - Attribution of risks to individual impact drivers
        
        Returns
        -------
        CashflowResult
            Comprehensive result containing detailed annual cashflow table, 
            financial metrics, risk impacts, and cost of capital.
        """
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


def calculate_equivalent_premium(baseline_fcff: np.ndarray, risked_fcff: np.ndarray, 
                                 baseline_wacc: float) -> float:
    """Calculate the discount-rate premium (bps) equivalent to cashflow degradation.
    
    Finds the premium over baseline WACC that, when applied to baseline cashflows,
    reproduces the NPV of degraded cashflows at baseline WACC.
    
    This converts cashflow-based risk impacts into their equivalent cost-of-capital
    representation, avoiding double-counting by using only ONE mechanism per risk.
    
    Parameters
    ----------
    baseline_fcff : np.ndarray
        Undegraded (baseline) free cash flow per year.
    risked_fcff : np.ndarray
        Degraded (risked) free cash flow per year.
    baseline_wacc : float
        Baseline WACC (decimal, e.g., 0.06 for 6%).
    
    Returns
    -------
    float
        Equivalent premium in basis points (bps), or 0.0 if no premium is needed.
    
    Notes
    -----
    If the target NPV cannot be bracketed, returns 0.0 (no meaningful premium).
    """
    target_npv = npv(baseline_wacc, risked_fcff)
    
    # Search bounds: from baseline to baseline + 500 bps
    lo, hi = baseline_wacc, baseline_wacc + 0.50
    f = lambda r: npv(r, baseline_fcff) - target_npv  # noqa: E731
    
    # Check if solution exists within bounds
    if np.sign(f(lo)) == np.sign(f(hi)):
        return 0.0
    
    # Binary search for the equivalent rate
    for _ in range(100):
        mid = (lo + hi) / 2
        if np.sign(f(mid)) == np.sign(f(lo)):
            lo = mid
        else:
            hi = mid
    
    equivalent_rate = (lo + hi) / 2
    return (equivalent_rate - baseline_wacc) * 1e4  # Convert to bps


def cashflow_equivalent_premium(baseline: CashflowResult, cashflow_only: CashflowResult) -> float:
    """Calculate discount-rate uplift (bps) justified by expected cashflow losses alone.
    
    Finds the premium over the baseline WACC that, when applied to unrisked cashflows,
    reproduces the NPV of cashflows impacted by risks. This isolates the pure cashflow
    impact separate from explicit cost-of-capital premiums.
    
    **Used for Attribution Analysis Only:**
    
    This reports what the equivalent premium WOULD have been if risks had no
    explicit market premiums. Useful for understanding whether market premiums
    match the economic impact of cashflow degradation.
    
    Parameters
    ----------
    baseline : CashflowResult
        Baseline (unrisked) cashflow result.
    cashflow_only : CashflowResult
        Cashflow result with risk impacts applied to flows but no cost-of-capital premiums.
    
    Returns
    -------
    float
        Equivalent discount-rate premium in basis points (bps), or NaN if not found.
    """
    base_fcff = baseline.table["fcff"].to_numpy()
    cashflow_only_fcff = cashflow_only.table["fcff"].to_numpy()
    premium_bps = calculate_equivalent_premium(base_fcff, cashflow_only_fcff, baseline.coc.wacc)
    return premium_bps if premium_bps > 0 else float("nan")


def _apply_auto_premiums(inputs: ProjectInputs, risks: Sequence[Risk], impacts: dict[str, RiskImpact], 
                         baseline: CashflowResult) -> None:
    """Auto-calculate and apply equivalent premiums for risks with cashflow impacts but no explicit premiums.
    
    For risks that affect cashflows (e.g., generation_factor, price_factor, opex_factor, capex_factor)
    but don't have explicit market premiums, calculate the equivalent cost-of-capital premium that
    would produce the same NPV impact when applied to baseline cashflows at baseline WACC.
    
    This modifies the impacts dict in-place, adding premium_debt_bps and premium_equity_bps to risks
    that need them.
    
    Parameters
    ----------
    inputs : ProjectInputs
        Base-case project inputs.
    risks : Sequence[Risk]
        List of risks being evaluated.
    impacts : dict[str, RiskImpact]
        Dictionary of risk impacts (modified in-place).
    baseline : CashflowResult
        Baseline (unrisked) cashflow result for premium calculation reference.
    """
    for risk, impact in impacts.items():
        # Only auto-calculate if the risk doesn't already have explicit premiums
        if impact.premium_debt_bps == 0.0 and impact.premium_equity_bps == 0.0:
            # Run the risk in isolation with cashflows but no premiums to calculate equivalent premium
            risk_obj = next(r for r in risks if r.risk_id == risk)
            isolated_cashflow_only = ProjectCashflowModel(
                inputs, [risk_obj], 
                impact_overrides={risk: impact}, 
                include_premiums=False
            ).run()
            
            # Calculate equivalent premium from cashflow degradation
            baseline_fcff = baseline.table["fcff"].to_numpy()
            risked_fcff = isolated_cashflow_only.table["fcff"].to_numpy()
            equiv_prem_bps = calculate_equivalent_premium(baseline_fcff, risked_fcff, baseline.coc.wacc)
            
            # Apply the equivalent premium symmetrically to both debt and equity
            # (unless the premium is negligible)
            if equiv_prem_bps > 0.1:  # Only apply if meaningful (>1 basis point)
                impact.premium_debt_bps = equiv_prem_bps
                impact.premium_equity_bps = equiv_prem_bps


def analyse(inputs: ProjectInputs, risks: Sequence[Risk], *, impact_overrides: dict[str, RiskImpact] | None = None,
            interventions: Sequence[Intervention] = ()) -> ScenarioAnalysis:
    """Run baseline, joint-risk, and per-risk standalone cases with optional interventions.
    
    Performs comprehensive scenario analysis including:
    - Baseline (unrisked) case
    - Unmitigated (risked, pre-intervention) case
    - Risked with interventions applied
    - Per-risk attribution to identify individual risk drivers
    - Intervention impact attribution
    - Auto-calculation of cost-of-capital premiums from cashflow degradation
    
    **Premium Auto-Calculation:**
    
    For risks that affect cashflows (e.g., generation_factor, price_factor, opex_factor,
    capex_factor) but don't have explicit market premiums, the equivalent cost-of-capital
    premium is automatically calculated and applied to both cost of debt and cost of equity.
    This ensures that:
    
    - Currency depreciation risk (reducing revenue in local currency) increases LCOE and WACC
    - Performance degradation risks increase the cost of capital proportionally
    - Financial impact is reflected consistently across both cashflows AND discount rates
    
    The equivalent premium is calculated by finding the discount-rate increase that would,
    when applied to baseline (unrisked) cashflows, produce the same NPV as the risked cashflows
    discounted at the baseline WACC. This avoids double-counting by using ONE mechanism:
    either explicit market premiums (for risks with known capital market pricing) OR 
    auto-calculated premiums (for risks modeled through cashflow degradation).
    
    **Intervention Premium Relief:**
    
    When interventions are applied to risks with auto-calculated premiums, the premium 
    reduction is handled through the auto-recalculation mechanism: interventions first 
    reduce the cashflow degradation, which then naturally reduces the equivalent premium
    when recalculated on the mitigated cashflows.
    
    Parameters
    ----------
    inputs : ProjectInputs
        Base-case project inputs.
    risks : Sequence[Risk]
        List of risks to evaluate.
    impact_overrides : dict[str, RiskImpact], optional
        Override risk impacts (bypasses risk evaluation).
    interventions : Sequence[Intervention], optional
        List of de-risking interventions to apply after risk evaluation.
    
    Returns
    -------
    ScenarioAnalysis
        Attribution dataframe showing impact of each risk and intervention on key metrics,
        including both cashflow impacts and their equivalent cost-of-capital premiums.
    """
    baseline = ProjectCashflowModel(inputs).run()
    # Get raw impacts and calculate auto-premiums BEFORE creating unmitigated model
    temp_unmitigated = ProjectCashflowModel(inputs, risks, impact_overrides=impact_overrides).run()
    raw = temp_unmitigated.raw_risk_impacts
    _apply_auto_premiums(inputs, risks, raw, baseline)
    
    # Now recreate unmitigated model with auto-premiums applied to raw impacts
    unmitigated = ProjectCashflowModel(inputs, risks, impact_overrides=raw).run()
    
    risked = ProjectCashflowModel(inputs, risks, impact_overrides=raw, interventions=interventions).run()
    cashflow_only = ProjectCashflowModel(inputs, risks, impact_overrides=raw, interventions=interventions, include_premiums=False).run()
    for column in ("fcff", "equity_cf", "cfads", "revenue", "capex", "opex", "debt_service"):
        risked.table[f"intervention_delta_{column}"] = risked.table[column] - unmitigated.table[column]

    def delta(result: CashflowResult) -> dict[str, float]:
        """Calculate metric deltas (impact) relative to baseline for attribution metrics.
        
        Parameters
        ----------
        result : CashflowResult
            Cashflow result to compare against baseline.
        
        Returns
        -------
        dict[str, float]
            Dictionary mapping metric names to their changes vs baseline.
        """
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
        """Calculate percentile values for key financial metrics.
        
        Computes P50, P75, P90, P95, P99 for NPV, equity IRR, minimum DSCR, LCOE,
        and lifetime generation from the simulation results.
        
        Returns
        -------
        pd.DataFrame
            Percentile values indexed by percentile label (P50, P75, etc.).
        """
        levels = [50, 75, 90, 95, 99]
        columns = ["npv", "equity_irr", "min_dscr", "lcoe", "lifetime_generation_twh"]
        return pd.DataFrame(
            {column: [self.metrics[column].quantile(level / 100 if column == "lcoe" else 1 - level / 100) for level in levels] for column in columns},
            index=[f"P{level}" for level in levels],
        )


def simulate(inputs: ProjectInputs, risks: Sequence[Risk], *, draws: int = 500,
             exceedance_pct: float = 90, seed: int = 42, severity_cv: float = 0.25,
             interventions: Sequence[Intervention] = ()) -> SimulationAnalysis:
    """Run Monte Carlo simulation with independent risk events and stochastic severities.
    
    For each draw:
    - Independently samples each risk's occurrence and severity
    - Builds lognormal severity distribution with specified coefficient of variation
    - Optionally applies interventions to each path
    - Selects a coherent path (all risks jointly) ranked by NPV exceedance percentile
    
    Parameters
    ----------
    inputs : ProjectInputs
        Base-case project inputs.
    risks : Sequence[Risk]
        List of risks to sample.
    draws : int, optional
        Number of Monte Carlo draws (default: 500).
    exceedance_pct : float, optional
        Percentile rank for path selection (0-100; default: 90).
        0 = best case, 100 = worst case.
    seed : int, optional
        Random seed for reproducibility (default: 42).
    severity_cv : float, optional
        Coefficient of variation for lognormal severity (default: 0.25).
    interventions : Sequence[Intervention], optional
        De-risking interventions to apply to each draw.
    
    Returns
    -------
    SimulationAnalysis
        Metrics for all draws, selected representative path, and unmitigated baseline metrics.
    
    Raises
    ------
    ValueError
        If draws < 2, exceedance_pct not in (0, 100), or severity_cv < 0.
    """
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
