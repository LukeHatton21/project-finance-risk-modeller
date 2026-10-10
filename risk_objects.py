"""Risk objects for the project finance risk modeller.

Each risk translates its inputs into a :class:`RiskImpact` - a set of year-by-year
multipliers/adders on the project cashflow drivers plus a cost-of-capital premium.
New risk categories are added by subclassing :class:`Risk`, declaring ``param_specs``
and decorating the class with :func:`register_risk`; the UI and CSV loader pick
them up automatically.
"""

from __future__ import annotations

import csv
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import pandas as pd

DISCRETE = "discrete"
SYSTEMIC = "systemic"

STRENGTH_LEVELS: dict[str, float] = {
    "Very low": 0.25,
    "Low": 0.5,
    "Medium": 1.0,
    "High": 1.5,
    "Very high": 2.0,
}

DEFAULT_RISK_CSV = Path(__file__).parent / "data" / "default_risks.csv"


# --------------------------------------------------------------------------- #
# Core data structures
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ParamSpec:
    """Describes a risk-specific input so the UI can render it generically."""

    key: str
    label: str
    default: float | int | str
    min_value: float = 0.0
    max_value: float = 100.0
    step: float = 1.0
    unit: str = ""
    help: str = ""
    integer: bool = False
    options: tuple[str, ...] = ()

    def coerce(self, value: Any) -> float | int | str:
        if self.options:
            value = str(value).strip()
            if value not in self.options:
                raise ValueError(f"'{value}' is not a valid option for '{self.key}' {self.options}")
            return value
        number = min(max(float(value), self.min_value), self.max_value)
        return int(round(number)) if self.integer else number


@dataclass(frozen=True)
class RiskContext:
    """Project information a risk needs to express its impact on the timeline."""

    construction_years: int
    lifetime_years: int
    capex_total: float  # unrisked capex, $m
    ppa_price: float  # $/MWh (real, year 0)
    ppa_tenor: int  # operating years
    merchant_price: float  # $/MWh (real, year 0)
    rng: np.random.Generator | None = None
    severity_cv: float = 0.25
    debt_tenor_years: int = 18

    @property
    def n_years(self) -> int:
        return self.construction_years + self.lifetime_years

    @property
    def op_year(self) -> np.ndarray:
        """Operating year index per model year (0 during construction, 1 = first year of operation)."""
        t = np.arange(self.n_years)
        return np.where(t >= self.construction_years, t - self.construction_years + 1, 0)

    @property
    def construction_mask(self) -> np.ndarray:
        return self.op_year == 0

    @property
    def operating_mask(self) -> np.ndarray:
        return self.op_year > 0


@dataclass
class RiskImpact:
    """Year-by-year effect of one or more risks on the cashflow drivers."""

    generation_factor: np.ndarray
    price_factor: np.ndarray
    opex_factor: np.ndarray
    capex_factor: np.ndarray
    extra_cost: np.ndarray  # additional real costs, $m
    premium_debt_bps: float = 0.0
    premium_equity_bps: float = 0.0
    country_premium: bool = False

    @classmethod
    def neutral(cls, n_years: int) -> RiskImpact:
        return cls(
            generation_factor=np.ones(n_years),
            price_factor=np.ones(n_years),
            opex_factor=np.ones(n_years),
            capex_factor=np.ones(n_years),
            extra_cost=np.zeros(n_years),
        )

    @property
    def active(self) -> np.ndarray:
        """Boolean mask of years in which the risk alters the cashflow."""
        tol = 1e-9
        return (
            (np.abs(self.generation_factor - 1) > tol)
            | (np.abs(self.price_factor - 1) > tol)
            | (np.abs(self.opex_factor - 1) > tol)
            | (np.abs(self.capex_factor - 1) > tol)
            | (np.abs(self.extra_cost) > tol)
        )


def combine_impacts(impacts: list[RiskImpact], n_years: int) -> RiskImpact:
    """Joint impact: multiplicative on factors, additive on costs and premiums."""
    combined = RiskImpact.neutral(n_years)
    for imp in impacts:
        combined.generation_factor = combined.generation_factor * imp.generation_factor
        combined.price_factor = combined.price_factor * imp.price_factor
        combined.opex_factor = combined.opex_factor * imp.opex_factor
        combined.capex_factor = combined.capex_factor * imp.capex_factor
        combined.extra_cost = combined.extra_cost + imp.extra_cost
        if not imp.country_premium:
            combined.premium_debt_bps += imp.premium_debt_bps
            combined.premium_equity_bps += imp.premium_equity_bps
    country = [imp for imp in impacts if imp.country_premium]
    combined.premium_debt_bps += max((imp.premium_debt_bps for imp in country), default=0)
    combined.premium_equity_bps += max((imp.premium_equity_bps for imp in country), default=0)
    return combined


def _carry_over(duration_years: float, op_year: np.ndarray, start_year: int) -> np.ndarray:
    """Fraction of each operating year covered by an event starting at ``start_year`` lasting ``duration_years``."""
    elapsed = (op_year - start_year).astype(float)
    frac = np.clip(duration_years - elapsed, 0.0, 1.0)
    return np.where((op_year >= start_year) & (op_year > 0), frac, 0.0)


# --------------------------------------------------------------------------- #
# Base class and registry
# --------------------------------------------------------------------------- #
RISK_REGISTRY: dict[str, type[Risk]] = {}


def register_risk(cls: type[Risk]) -> type[Risk]:
    RISK_REGISTRY[cls.risk_type] = cls
    return cls


PROBABILITY_SPEC = ParamSpec(
    "probability_pct", "Probability of occurrence", 30.0, 0, 100, 1, "%",
    "Likelihood the event occurs. Used to probability-weight the impact in the expected case.",
)


class Risk(ABC):
    """Base class for all project risks.

    ``strength`` scales the magnitude of the cashflow impact and the cost-of-capital
    premium (Medium = 1x the specified inputs).
    """

    risk_type: ClassVar[str] = ""
    label: ClassVar[str] = ""
    default_kind: ClassVar[str] = SYSTEMIC
    default_category: ClassVar[str] = "General"
    default_description: ClassVar[str] = ""
    param_specs: ClassVar[tuple[ParamSpec, ...]] = ()
    rating_driven: ClassVar[bool] = False

    def __init__(
        self,
        risk_id: str,
        name: str | None = None,
        *,
        strength: str | float = "Medium",
        params: dict[str, Any] | None = None,
        premium_debt_bps: float = 0.0,
        premium_equity_bps: float = 0.0,
        category: str | None = None,
        description: str | None = None,
    ) -> None:
        self.risk_id = risk_id
        self.name = name or self.label
        self.category = category or self.default_category
        self.description = description or self.default_description
        self.strength = strength
        self.premium_debt_bps = float(premium_debt_bps)
        self.premium_equity_bps = float(premium_equity_bps)
        specs = self.spec_map()
        self.params: dict[str, Any] = {s.key: s.default for s in self.param_specs}
        for key, value in (params or {}).items():
            if key not in specs:
                raise ValueError(f"Unknown parameter '{key}' for risk type '{self.risk_type}'")
            self.params[key] = specs[key].coerce(value)

    @classmethod
    def spec_map(cls) -> dict[str, ParamSpec]:
        return {s.key: s for s in cls.param_specs}

    @property
    def kind(self) -> str:
        return str(self.params.get("kind", self.default_kind))

    @property
    def multiplier(self) -> float:
        if isinstance(self.strength, str):
            return STRENGTH_LEVELS[self.strength]
        return float(self.strength)

    def probability(self, ctx: RiskContext) -> float:
        if self.kind == SYSTEMIC:
            return 1.0
        probability = float(self.params.get("probability_pct", 100.0)) / 100.0
        return float(ctx.rng.random() < probability) if ctx.rng is not None else probability

    def evaluate(self, ctx: RiskContext) -> RiskImpact:
        m = self.multiplier
        if ctx.rng is not None:
            sigma = np.sqrt(np.log1p(ctx.severity_cv ** 2))
            m *= ctx.rng.lognormal(-sigma ** 2 / 2, sigma)
        impact = self._impact(ctx, m, self.probability(ctx))
        impact.premium_debt_bps = self.premium_debt_bps * self.multiplier
        impact.premium_equity_bps = self.premium_equity_bps * self.multiplier
        return impact

    @abstractmethod
    def _impact(self, ctx: RiskContext, m: float, p: float) -> RiskImpact:
        """Return the cashflow impact for strength multiplier ``m`` and probability ``p``."""

    def __repr__(self) -> str:
        return f"{type(self).__name__}(id={self.risk_id!r}, strength={self.strength!r}, params={self.params})"


# --------------------------------------------------------------------------- #
# Concrete risks
# --------------------------------------------------------------------------- #
def rating_spreads() -> dict[str, tuple[float, float]]:
    with open(Path(__file__).parent / "data" / "sovereign_spreads.csv", newline="", encoding="utf-8") as source:
        return {row["rating"]: (float(row["debt_bps"]), float(row["equity_bps"])) for row in csv.DictReader(source)}


def calibrate_fx(history: pd.DataFrame) -> tuple[float, float]:
    required = {"year", "currency_per_usd"}
    if not required.issubset(history.columns) or len(history) < 3:
        raise ValueError("Provide at least three annual rows with year,currency_per_usd columns.")
    history = history.sort_values("year")
    years = pd.to_numeric(history["year"], errors="raise").to_numpy(dtype=float)
    quotes = pd.to_numeric(history["currency_per_usd"], errors="raise").to_numpy(dtype=float)
    if not np.isfinite(years).all() or not np.isfinite(quotes).all() or (quotes <= 0).any() or not np.all(np.diff(years) == 1):
        raise ValueError("Rates must be positive and finite, with one row per consecutive calendar year.")
    usd_log_returns = -np.diff(np.log(quotes))
    depreciation = 100 * (1 - np.mean(np.exp(usd_log_returns)))
    volatility = 100 * np.std(usd_log_returns, ddof=1)
    if not -10 <= depreciation <= 40 or not 0 <= volatility <= 60:
        raise ValueError("History implies depreciation or volatility outside the model's supported ranges.")
    return float(depreciation), float(volatility)


@register_risk
class SovereignRisk(Risk):
    risk_type = "sovereign"
    label = "Sovereign credit risk"
    default_category = "Country"
    default_description = "Rating-driven country allowance; indicative benchmarks, not live quotes."
    rating_driven = True
    param_specs = (
        ParamSpec("rating", "Sovereign foreign-currency rating (S&P / Fitch scale)", "BBB", options=("AAA", "AA", "A", "BBB", "BB", "B", "CCC", "CC", "C", "D")),
    )

    def evaluate(self, ctx: RiskContext) -> RiskImpact:
        impact = RiskImpact.neutral(ctx.n_years)
        impact.premium_debt_bps, impact.premium_equity_bps = rating_spreads()[self.params["rating"]]
        impact.country_premium = True
        return impact

    def _impact(self, ctx, m, p):
        return RiskImpact.neutral(ctx.n_years)


@register_risk
class PoliticalRisk(SovereignRisk):
    risk_type = "political"
    label = "Political / country risk"
    default_description = "Sovereign rating proxy for political risk; shares the sovereign allowance rather than adding it twice."


@register_risk
class CurrencyRisk(Risk):
    risk_type = "currency"
    label = "Currency depreciation vs USD"
    default_category = "Currency"
    default_description = "Local revenue and O&M translated to USD; debt service remains USD-denominated."
    param_specs = (
        ParamSpec("currency", "Local currency", "BRL", options=("USD", "EUR", "GBP", "BRL", "INR", "ZAR", "MXN", "TRY", "IDR", "JPY")),
        ParamSpec("annual_depreciation_pct", "Annual local-currency depreciation vs USD", 3.0, -10, 40, 0.5, "%", "Scenario assumption, not a historical estimate. Positive means local currency loses USD value."),
        ParamSpec("volatility_pct", "Annual FX volatility", 10.0, 0, 60, 0.5, "%"),
        ParamSpec("local_revenue_pct", "Revenue denominated in local currency", 100.0, 0, 100, 5, "%"),
        ParamSpec("local_opex_pct", "O&M denominated in local currency", 80.0, 0, 100, 5, "%"),
        ParamSpec("hedged_pct", "Local revenue hedged to USD", 0.0, 0, 100, 5, "%"),
    )

    def _impact(self, ctx, m, p):
        impact = RiskImpact.neutral(ctx.n_years)
        if self.params["currency"] == "USD":
            return impact
        years = np.arange(ctx.n_years)
        depreciation = np.clip(m * self.params["annual_depreciation_pct"] / 100, -0.95, 0.95)
        if ctx.rng is None:
            fx = (1 - depreciation) ** years
        else:
            volatility = self.params["volatility_pct"] / 100
            increments = np.log1p(-depreciation) - volatility ** 2 / 2 + volatility * ctx.rng.normal(size=ctx.n_years - 1)
            fx = np.exp(np.r_[0, np.cumsum(increments)])
        revenue_exposure = self.params["local_revenue_pct"] / 100 * (1 - self.params["hedged_pct"] / 100)
        impact.price_factor = np.where(ctx.operating_mask, 1 + revenue_exposure * (fx - 1), 1)
        impact.opex_factor = np.where(ctx.operating_mask, 1 + self.params["local_opex_pct"] / 100 * (fx - 1), 1)
        return impact


@register_risk
class GridConnectionDelay(Risk):
    risk_type = "grid_connection_delay"
    label = "Grid connection delay"
    default_kind = DISCRETE
    default_category = "Development & grid"
    default_description = "Commercial operation is delayed waiting for grid connection; revenue is lost while holding costs accrue."
    param_specs = (
        ParamSpec("avg_delay_months", "Average delay", 12, 0, 48, 1, "months", "Expected delay to grid connection if the risk occurs.", integer=True),
        PROBABILITY_SPEC,
        ParamSpec("holding_cost_pct_capex", "Holding cost during delay", 1.0, 0, 5, 0.1, "% capex / yr", "Standby O&M, insurance, security and developer costs while awaiting connection."),
    )

    def _impact(self, ctx, m, p):
        imp = RiskImpact.neutral(ctx.n_years)
        delay_years = p * m * self.params["avg_delay_months"] / 12
        lost = _carry_over(delay_years, ctx.op_year, 1)
        imp.generation_factor = 1 - lost
        imp.extra_cost = lost * self.params["holding_cost_pct_capex"] / 100 * ctx.capex_total
        return imp


@register_risk
class ConstructionCostOverrun(Risk):
    risk_type = "construction_cost_overrun"
    label = "Construction cost overrun"
    default_kind = DISCRETE
    default_category = "Construction"
    default_description = "Capex exceeds budget (contractor claims, commodity prices, design changes)."
    param_specs = (
        ParamSpec("overrun_pct", "Cost overrun if it occurs", 15.0, 0, 60, 1, "% capex"),
        PROBABILITY_SPEC,
    )

    def _impact(self, ctx, m, p):
        imp = RiskImpact.neutral(ctx.n_years)
        overrun = p * m * self.params["overrun_pct"] / 100
        imp.capex_factor = np.where(ctx.construction_mask, 1 + overrun, 1.0)
        return imp


@register_risk
class Curtailment(Risk):
    risk_type = "curtailment"
    label = "Grid curtailment"
    default_kind = SYSTEMIC
    default_category = "Market & grid"
    default_description = "Output is curtailed by the system operator; grows as renewable penetration rises."
    param_specs = (
        ParamSpec("initial_pct", "Initial curtailment", 2.0, 0, 20, 0.5, "% output"),
        ParamSpec("annual_increase_pp", "Annual increase", 0.3, 0, 2, 0.05, "pp / yr"),
        ParamSpec("max_pct", "Curtailment cap", 12.0, 0, 50, 1, "% output"),
    )

    def _impact(self, ctx, m, p):
        imp = RiskImpact.neutral(ctx.n_years)
        o = ctx.op_year
        level = np.minimum(self.params["max_pct"], self.params["initial_pct"] + self.params["annual_increase_pp"] * (o - 1))
        curtail = np.clip(m * level / 100, 0, 1)
        imp.generation_factor = np.where(ctx.operating_mask, 1 - curtail, 1.0)
        return imp


@register_risk
class ResourceShortfall(Risk):
    risk_type = "resource_shortfall"
    label = "Resource / yield shortfall"
    default_kind = SYSTEMIC
    default_category = "Resource"
    default_description = "Energy yield systematically below the P50 forecast (resource assessment error, wake/soiling losses)."
    param_specs = (
        ParamSpec("yield_bias_pct", "Yield below P50", 4.0, 0, 20, 0.5, "% output"),
    )

    def _impact(self, ctx, m, p):
        imp = RiskImpact.neutral(ctx.n_years)
        imp.generation_factor = np.where(ctx.operating_mask, 1 - min(1.0, m * self.params["yield_bias_pct"] / 100), 1.0)
        return imp


@register_risk
class MerchantPriceRisk(Risk):
    risk_type = "merchant_price"
    label = "Merchant price & cannibalisation"
    default_kind = SYSTEMIC
    default_category = "Market"
    default_description = "After PPA expiry, captured prices fall below forecast and decline further as capture rates erode."
    param_specs = (
        ParamSpec("price_shortfall_pct", "Price below forecast", 15.0, 0, 60, 1, "%"),
        ParamSpec("capture_decline_pp", "Capture-rate erosion", 1.0, 0, 5, 0.1, "pp / yr", "Additional annual decline in captured price after PPA expiry."),
    )

    def _impact(self, ctx, m, p):
        imp = RiskImpact.neutral(ctx.n_years)
        years_merchant = ctx.op_year - ctx.ppa_tenor
        merchant = years_merchant > 0
        level = (1 - m * self.params["price_shortfall_pct"] / 100) * (
            1 - m * self.params["capture_decline_pp"] / 100 * (years_merchant - 1)
        )
        imp.price_factor = np.where(merchant, np.clip(level, 0, None), 1.0)
        return imp


@register_risk
class AcceleratedDegradation(Risk):
    risk_type = "accelerated_degradation"
    label = "Accelerated degradation"
    default_kind = SYSTEMIC
    default_category = "Technology"
    default_description = "Equipment performance degrades faster than the manufacturer warranty assumes."
    param_specs = (
        ParamSpec("extra_degradation_pct", "Extra degradation", 0.3, 0, 2, 0.05, "% / yr"),
    )

    def _impact(self, ctx, m, p):
        imp = RiskImpact.neutral(ctx.n_years)
        rate = min(1.0, m * self.params["extra_degradation_pct"] / 100)
        imp.generation_factor = np.where(ctx.operating_mask, (1 - rate) ** np.maximum(ctx.op_year - 1, 0), 1.0)
        return imp


@register_risk
class EquipmentFailure(Risk):
    risk_type = "equipment_failure"
    label = "Major equipment failure"
    default_kind = DISCRETE
    default_category = "Operations"
    default_description = "Serial defect or major component failure causing an outage and repair costs."
    param_specs = (
        ParamSpec("event_year", "Operating year of failure", 8, 1, 40, 1, "yr", integer=True),
        ParamSpec("outage_months", "Outage duration", 4, 0, 24, 1, "months", integer=True),
        ParamSpec("repair_cost_pct_capex", "Repair cost", 4.0, 0, 30, 0.5, "% capex"),
        ParamSpec("insurance_recovery_pct", "Insurance recovery", 50.0, 0, 100, 5, "%"),
        PROBABILITY_SPEC,
    )

    def _impact(self, ctx, m, p):
        imp = RiskImpact.neutral(ctx.n_years)
        year = self.params["event_year"]
        lost = _carry_over(m * self.params["outage_months"] / 12, ctx.op_year, year)
        imp.generation_factor = 1 - p * lost
        net_cost = m * self.params["repair_cost_pct_capex"] / 100 * ctx.capex_total * (1 - self.params["insurance_recovery_pct"] / 100)
        imp.extra_cost = np.where(ctx.op_year == year, p * net_cost, 0.0)
        return imp


@register_risk
class RegulatoryChange(Risk):
    risk_type = "regulatory_change"
    label = "Regulatory / policy change"
    default_kind = DISCRETE
    default_category = "Policy"
    default_description = "Retroactive subsidy cut, windfall tax or network-charge change reducing net revenue."
    param_specs = (
        ParamSpec("event_year", "Operating year of change", 10, 1, 40, 1, "yr", integer=True),
        ParamSpec("revenue_haircut_pct", "Revenue haircut", 10.0, 0, 50, 1, "%"),
        PROBABILITY_SPEC,
    )

    def _impact(self, ctx, m, p):
        imp = RiskImpact.neutral(ctx.n_years)
        haircut = min(1.0, p * m * self.params["revenue_haircut_pct"] / 100)
        imp.price_factor = np.where(ctx.op_year >= self.params["event_year"], 1 - haircut, 1.0)
        return imp


@register_risk
class OfftakerDefault(Risk):
    risk_type = "offtaker_default"
    label = "Offtaker default"
    default_kind = DISCRETE
    default_category = "Counterparty"
    default_description = "PPA counterparty defaults; output is resold at a discount to merchant prices for the rest of the PPA term."
    param_specs = (
        ParamSpec("default_year", "Operating year of default", 6, 1, 40, 1, "yr", integer=True),
        ParamSpec("settlement_gap_months", "Months unsold before re-contracting", 3, 0, 12, 1, "months", integer=True),
        ParamSpec("replacement_discount_pct", "Discount to merchant price", 10.0, 0, 50, 1, "%"),
        ParamSpec("probability_pct", "Probability of default", 10.0, 0, 100, 1, "%"),
    )

    def _impact(self, ctx, m, p):
        imp = RiskImpact.neutral(ctx.n_years)
        o = ctx.op_year
        year = self.params["default_year"]
        in_window = (o >= year) & (o <= ctx.ppa_tenor)
        ratio = ctx.merchant_price * (1 - min(1.0, m * self.params["replacement_discount_pct"] / 100)) / max(ctx.ppa_price, 1e-9)
        gap = min(1.0, m * self.params["settlement_gap_months"] / 12)
        stressed = np.where(o == year, (1 - gap) * ratio, ratio)
        imp.price_factor = np.where(in_window, 1 - p * (1 - stressed), 1.0)
        return imp


@register_risk
class OpexInflation(Risk):
    risk_type = "opex_inflation"
    label = "O&M cost inflation"
    default_kind = SYSTEMIC
    default_category = "Operations"
    default_description = "O&M, insurance and land costs escalate faster than the base-case indexation."
    param_specs = (
        ParamSpec("excess_inflation_pct", "Excess cost inflation", 1.0, 0, 5, 0.1, "% / yr"),
    )

    def _impact(self, ctx, m, p):
        imp = RiskImpact.neutral(ctx.n_years)
        rate = m * self.params["excess_inflation_pct"] / 100
        imp.opex_factor = np.where(ctx.operating_mask, (1 + rate) ** np.maximum(ctx.op_year - 1, 0), 1.0)
        return imp


@register_risk
class GenericRisk(Risk):
    """Configurable risk used for custom user-defined risks and CSV-only categories."""

    risk_type = "generic"
    label = "Custom risk"
    default_kind = DISCRETE
    default_category = "Custom"
    default_description = "User-defined risk."
    param_specs = (
        ParamSpec("kind", "Risk type", DISCRETE, options=(DISCRETE, SYSTEMIC), help="Discrete risks are probability-weighted; systemic risks always apply."),
        ParamSpec("probability_pct", "Probability of occurrence", 50.0, 0, 100, 1, "%", "Ignored for systemic risks."),
        ParamSpec("capex_impact_pct", "Capex increase", 0.0, 0, 100, 1, "% capex", "Applied during construction."),
        ParamSpec("start_year", "Start (operating year)", 1, 1, 40, 1, "yr", integer=True),
        ParamSpec("duration_years", "Duration", 0, 0, 40, 1, "yrs", "0 = until end of life.", integer=True),
        ParamSpec("generation_impact_pct", "Generation reduction", 0.0, 0, 100, 1, "%"),
        ParamSpec("price_impact_pct", "Price reduction", 0.0, 0, 100, 1, "%"),
        ParamSpec("opex_impact_pct", "O&M cost increase", 0.0, 0, 200, 1, "%"),
        ParamSpec("one_off_cost_pct_capex", "One-off cost in start year", 0.0, 0, 50, 0.5, "% capex"),
    )

    def _impact(self, ctx, m, p):
        imp = RiskImpact.neutral(ctx.n_years)
        o = ctx.op_year
        start, duration = self.params["start_year"], self.params["duration_years"]
        end = ctx.lifetime_years if duration == 0 else start + duration - 1
        window = (o >= start) & (o <= end)
        s = p * m / 100
        imp.capex_factor = np.where(ctx.construction_mask, 1 + s * self.params["capex_impact_pct"], 1.0)
        imp.generation_factor = np.where(window, 1 - min(1.0, s * self.params["generation_impact_pct"]), 1.0)
        imp.price_factor = np.where(window, 1 - min(1.0, s * self.params["price_impact_pct"]), 1.0)
        imp.opex_factor = np.where(window, 1 + s * self.params["opex_impact_pct"], 1.0)
        imp.extra_cost = np.where(o == start, s * self.params["one_off_cost_pct_capex"] * ctx.capex_total, 0.0)
        return imp


# --------------------------------------------------------------------------- #
# Configuration / CSV loading
# --------------------------------------------------------------------------- #
@dataclass
class RiskConfig:
    """Serializable description of a risk (one row of the risk library CSV)."""

    risk_id: str
    risk_type: str
    name: str
    category: str = ""
    enabled: bool = False
    strength: str = "Medium"
    premium_debt_bps: float = 0.0
    premium_equity_bps: float = 0.0
    params: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    custom: bool = False

    @property
    def risk_class(self) -> type[Risk]:
        if self.risk_type not in RISK_REGISTRY:
            raise ValueError(f"Unknown risk_type '{self.risk_type}'. Available: {sorted(RISK_REGISTRY)}")
        return RISK_REGISTRY[self.risk_type]

    def build(self, **overrides: Any) -> Risk:
        """Instantiate the risk, optionally overriding strength/params/premiums from the UI."""
        kwargs: dict[str, Any] = dict(
            strength=self.strength,
            params=self.params,
            premium_debt_bps=self.premium_debt_bps,
            premium_equity_bps=self.premium_equity_bps,
            category=self.category or None,
            description=self.description or None,
        )
        kwargs.update(overrides)
        return self.risk_class(self.risk_id, self.name, **kwargs)


def parse_parameters(text: str) -> dict[str, str]:
    """Parse ``"key=value;key=value"`` into a dict."""
    params: dict[str, str] = {}
    for item in (text or "").split(";"):
        if item.strip():
            key, _, value = item.partition("=")
            params[key.strip()] = value.strip()
    return params


def _as_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def load_risk_library(path: str | Path = DEFAULT_RISK_CSV) -> list[RiskConfig]:
    """Read the default risk library; each row is validated by instantiating the risk."""
    configs: list[RiskConfig] = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            cfg = RiskConfig(
                risk_id=row["risk_id"].strip(),
                risk_type=row["risk_type"].strip(),
                name=row["name"].strip(),
                category=row.get("category", "").strip(),
                enabled=_as_bool(row.get("enabled", "")),
                strength=row.get("strength", "Medium").strip() or "Medium",
                premium_debt_bps=float(row.get("premium_debt_bps") or 0),
                premium_equity_bps=float(row.get("premium_equity_bps") or 0),
                params=parse_parameters(row.get("parameters", "")),
                description=row.get("description", "").strip(),
            )
            if cfg.strength not in STRENGTH_LEVELS:
                raise ValueError(f"Risk '{cfg.risk_id}': strength must be one of {list(STRENGTH_LEVELS)}")
            cfg.params = cfg.build().params  # validate & coerce
            configs.append(cfg)
    return configs
