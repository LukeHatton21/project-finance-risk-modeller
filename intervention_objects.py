"""Targeted, bounded de-risking transformations on existing risk impacts."""

from dataclasses import replace
from typing import ClassVar

import numpy as np

from risk_objects import ParamSpec, Risk, RiskContext, RiskImpact


class Intervention(Risk):
    label = "De-risking intervention"
    default_category = "De-risking"
    param_specs: ClassVar[tuple[ParamSpec, ...]] = (
        ParamSpec("effectiveness_pct", "Targeted loss reduction", 50.0, 0, 100, 5, "%"),
        ParamSpec("debt_relief_pct", "Targeted debt premium reduction", 0.0, 0, 100, 5, "%"),
        ParamSpec("equity_relief_pct", "Targeted equity premium reduction", 0.0, 0, 100, 5, "%"),
        ParamSpec("upfront_fee_pct", "Upfront intervention cost", 0.0, 0, 10, 0.1, "% base capex"),
        ParamSpec("annual_fee_pct", "Annual operating-period fee", 0.0, 0, 5, 0.05, "% base capex / yr"),
    )

    def __init__(self, risk_id: str, name: str | None = None, *, target_ids: tuple[str, ...] = (), **kwargs):
        super().__init__(risk_id, name, **kwargs)
        self.target_ids = tuple(target_ids)

    def eligible(self, risk: Risk) -> bool:
        return True

    def evaluate(self, ctx: RiskContext) -> RiskImpact:
        return self._impact(ctx, self.multiplier, 1.0)

    def mitigate(self, impact: RiskImpact, ctx: RiskContext) -> RiskImpact:
        strength = self.multiplier
        reduction = min(1.0, strength * self.params["effectiveness_pct"] / 100)
        debt = min(1.0, strength * self.params["debt_relief_pct"] / 100)
        equity = min(1.0, strength * self.params["equity_relief_pct"] / 100)
        return replace(
            impact,
            generation_factor=impact.generation_factor + reduction * np.maximum(1 - impact.generation_factor, 0),
            price_factor=impact.price_factor + reduction * np.maximum(1 - impact.price_factor, 0),
            opex_factor=impact.opex_factor - reduction * np.maximum(impact.opex_factor - 1, 0),
            capex_factor=impact.capex_factor - reduction * np.maximum(impact.capex_factor - 1, 0),
            extra_cost=impact.extra_cost - reduction * np.maximum(impact.extra_cost, 0),
            premium_debt_bps=impact.premium_debt_bps * (1 - debt),
            premium_equity_bps=impact.premium_equity_bps * (1 - equity),
        )

    def _impact(self, ctx, m, p):
        impact = RiskImpact.neutral(ctx.n_years)
        impact.capex_factor = np.where(ctx.construction_mask, 1 + m * self.params["upfront_fee_pct"] / 100, 1)
        impact.extra_cost = np.where(ctx.operating_mask, ctx.capex_total * m * self.params["annual_fee_pct"] / 100, 0)
        return impact


class Guarantee(Intervention):
    risk_type = "guarantee"
    label = "General credit guarantee"
    default_description = "Transfers covered non-country credit risk to a guarantor, reducing targeted debt premiums with explicit fees; no physical loss or CFADS support."
    param_specs = (
        ParamSpec("debt_relief_pct", "Covered share of targeted debt premium", 50.0, 0, 100, 5, "%"),
        ParamSpec("upfront_fee_pct", "Upfront guarantee fee", 0.0, 0, 10, 0.1, "% base capex"),
        ParamSpec("annual_fee_pct", "Annual guarantee fee", 0.1, 0, 5, 0.05, "% base capex / yr"),
    )

    def eligible(self, risk: Risk) -> bool:
        return not risk.rating_driven

    def mitigate(self, impact: RiskImpact, ctx: RiskContext) -> RiskImpact:
        coverage = min(1.0, self.multiplier * self.params["debt_relief_pct"] / 100)
        return replace(impact, premium_debt_bps=impact.premium_debt_bps * (1 - coverage))

    def _impact(self, ctx, m, p):
        impact = super()._impact(ctx, m, p)
        impact.extra_cost = np.where(ctx.op_year <= ctx.debt_tenor_years, impact.extra_cost, 0)
        return impact


class CountryRiskGuarantee(Guarantee):
    risk_type = "country_guarantee"
    label = "Political / country risk guarantee"
    default_description = "Transfers covered sovereign/political credit exposure to a guarantor, reducing the rating-driven country debt premium with explicit fees."

    def eligible(self, risk: Risk) -> bool:
        return risk.rating_driven


class TechnicalAssistance(Intervention):
    risk_type = "technical_assistance"
    label = "Project-preparation technical assistance"
    default_description = "Reduces development-stage loss severity through preparation and due diligence; does not change event probability."

    def eligible(self, risk: Risk) -> bool:
        return risk.risk_type in {"grid_connection_delay", "construction_cost_overrun"} or (
            risk.risk_type == "generic" and float(risk.params.get("capex_impact_pct", 0)) > 0
        )

class PolicyReform(Intervention):
    risk_type = "policy_reform"
    label = "Targeted policy reform"
    default_description = "Reduces the selected risk's loss severity, e.g. lost revenue from permitting or grid delay."


INTERVENTION_REGISTRY = {cls.risk_type: cls for cls in (Guarantee, CountryRiskGuarantee, TechnicalAssistance, PolicyReform)}


def apply_interventions(risks: list[Risk], impacts: dict[str, RiskImpact],
                        interventions: list[Intervention], ctx: RiskContext) -> tuple[dict[str, RiskImpact], list[RiskImpact]]:
    effective = dict(impacts)
    fees = []
    lookup = {risk.risk_id: risk for risk in risks}
    if len(lookup) != len(risks):
        raise ValueError("Risk identifiers must be unique")
    for intervention in interventions:
        if not intervention.target_ids:
            raise ValueError(f"{intervention.name}: select at least one target risk")
        for target in intervention.target_ids:
            if target not in lookup or not intervention.eligible(lookup[target]):
                raise ValueError(f"{intervention.name}: ineligible or inactive target {target}")
            original = effective[target]
            mitigated = intervention.mitigate(original, ctx)
            if isinstance(intervention, TechnicalAssistance) and lookup[target].risk_type == "generic":
                mitigated = replace(mitigated, generation_factor=original.generation_factor,
                                    price_factor=original.price_factor, opex_factor=original.opex_factor,
                                    extra_cost=np.where(ctx.construction_mask, mitigated.extra_cost, original.extra_cost))
            effective[target] = mitigated
        fees.append(intervention.evaluate(ctx))
    return effective, fees