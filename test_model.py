import unittest

import numpy as np
import pandas as pd

from project_cashflow import ProjectCashflowModel, ProjectInputs, analyse, simulate
from intervention_objects import Guarantee, CountryRiskGuarantee, PolicyReform, TechnicalAssistance, apply_interventions
from risk_objects import (
    CurrencyRisk, ConstructionCostOverrun, PoliticalRisk, RiskContext,
    SovereignRisk, GridConnectionDelay, GenericRisk, calibrate_fx, combine_impacts, load_risk_library,
)


class RiskModelTests(unittest.TestCase):
    def setUp(self):
        self.inputs = ProjectInputs()
        self.context = RiskContext(2, 25, 145, 65, 15, 55)

    def test_ratings_ignore_strength_and_share_country_allowance(self):
        low = SovereignRisk("sovereign", params={"rating": "BBB"}, strength="Very low").evaluate(self.context)
        high = SovereignRisk("sovereign", params={"rating": "BBB"}, strength="Very high").evaluate(self.context)
        self.assertEqual(low.premium_debt_bps, high.premium_debt_bps)
        political = PoliticalRisk("political", params={"rating": "BB"}).evaluate(self.context)
        self.assertGreater(political.premium_debt_bps, high.premium_debt_bps)
        joint = combine_impacts([high, political], self.context.n_years)
        self.assertEqual(joint.premium_debt_bps, political.premium_debt_bps)
        self.assertEqual(joint.premium_equity_bps, political.premium_equity_bps)

    def test_fx_usd_exposure_and_hedging(self):
        base = ProjectCashflowModel(self.inputs).run()
        fx = ProjectCashflowModel(self.inputs, [CurrencyRisk("fx")]).run()
        self.assertLess(fx.metrics["npv"], base.metrics["npv"])
        self.assertLess(fx.metrics["min_dscr"], base.metrics["min_dscr"])
        np.testing.assert_allclose(fx.table.debt_service, base.table.debt_service)
        for params in ({"currency": "USD"}, {"local_revenue_pct": 0, "local_opex_pct": 0},
                       {"hedged_pct": 100, "local_opex_pct": 0}):
            result = ProjectCashflowModel(self.inputs, [CurrencyRisk("fx", params=params)]).run()
            np.testing.assert_allclose(result.table.fcff, base.table.fcff)

    def test_history_quote_direction_and_validation(self):
        dep, vol = calibrate_fx(pd.DataFrame({"year": [2023, 2024, 2025], "currency_per_usd": [1, 1.1, 1.21]}))
        self.assertAlmostEqual(dep, 100 / 11)
        self.assertAlmostEqual(vol, 0)
        for rates in ([1, 0, 2], [1, np.nan, 2]):
            with self.assertRaises(ValueError):
                calibrate_fx(pd.DataFrame({"year": [2023, 2024, 2025], "currency_per_usd": rates}))
        with self.assertRaises(ValueError):
            calibrate_fx(pd.DataFrame({"year": [2023, 2023, 2025], "currency_per_usd": [1, 1, 1]}))

    def test_simulation_repeatability_and_exceedance(self):
        risks = [cfg.build() for cfg in load_risk_library() if cfg.enabled]
        first = simulate(self.inputs, risks, draws=100, exceedance_pct=90)
        second = simulate(self.inputs, risks, draws=100, exceedance_pct=90)
        np.testing.assert_array_equal(first.annual_fcff, second.annual_fcff)
        quantiles = first.percentiles()
        self.assertLessEqual(quantiles.loc["P95", "npv"], quantiles.loc["P90", "npv"])
        self.assertLessEqual(quantiles.loc["P90", "npv"], quantiles.loc["P50", "npv"])
        self.assertGreaterEqual(quantiles.loc["P95", "lcoe"], quantiles.loc["P90", "lcoe"])
        self.assertAlmostEqual(first.selected.risked.metrics["npv"], np.sort(first.metrics.npv)[round(.1 * 99)])
        self.assertTrue(any(np.allclose(first.selected.risked.table.fcff, path) for path in first.annual_fcff))

    def test_event_probability_endpoints_and_no_risks(self):
        for probability in (0, 100):
            risk = ConstructionCostOverrun("cost", params={"probability_pct": probability})
            sampled = risk.evaluate(RiskContext(2, 25, 145, 65, 15, 55, rng=np.random.default_rng(42), severity_cv=0))
            expected = risk.evaluate(self.context)
            np.testing.assert_allclose(sampled.capex_factor, expected.capex_factor)
        result = simulate(self.inputs, [], draws=10)
        self.assertEqual(result.metrics.npv.nunique(), 1)
        self.assertEqual(len(load_risk_library()), 14)

    def test_zero_and_full_intervention_effectiveness(self):
        risk = GridConnectionDelay("grid", premium_debt_bps=25)
        before = analyse(self.inputs, [risk])
        zero = PolicyReform("policy", target_ids=("grid",), params={"effectiveness_pct": 0})
        after = analyse(self.inputs, [risk], interventions=[zero])
        np.testing.assert_allclose(after.risked.table.fcff, before.risked.table.fcff)
        full = PolicyReform("policy", target_ids=("grid",), params={"effectiveness_pct": 100})
        after = analyse(self.inputs, [risk], interventions=[full])
        np.testing.assert_allclose(after.risked.table.fcff, after.baseline.table.fcff)
        self.assertEqual(after.risked.impact.premium_debt_bps, 25)

    def test_stacking_and_target_validation(self):
        risk = GridConnectionDelay("grid")
        raw = risk.evaluate(self.context)
        policies = [PolicyReform(name, target_ids=("grid",)) for name in ("one", "two")]
        effective, _ = apply_interventions([risk], {"grid": raw}, policies, self.context)
        np.testing.assert_allclose(1 - effective["grid"].generation_factor, .25 * (1 - raw.generation_factor))
        for target in ((), ("missing",)):
            with self.assertRaises(ValueError):
                apply_interventions([risk], {"grid": raw}, [PolicyReform("bad", target_ids=target)], self.context)
        sovereign = SovereignRisk("country")
        with self.assertRaises(ValueError):
            apply_interventions([sovereign], {"country": sovereign.evaluate(self.context)},
                                [TechnicalAssistance("ta", target_ids=("country",))], self.context)

    def test_guarantee_financing_only_and_fee_timing(self):
        risk = GridConnectionDelay("grid", premium_debt_bps=100)
        guarantee = Guarantee("guarantee", target_ids=("grid",), params={"annual_fee_pct": 0})
        result = analyse(self.inputs, [risk], interventions=[guarantee])
        np.testing.assert_allclose(result.risked.table.generation_mwh, result.unmitigated.table.generation_mwh)
        np.testing.assert_allclose(result.risked.table.fcff, result.unmitigated.table.fcff)
        self.assertLess(result.risked.coc.cost_of_debt, result.unmitigated.coc.cost_of_debt)
        self.assertGreater(result.risked.metrics["equity_npv"], result.unmitigated.metrics["equity_npv"])
        self.assertGreaterEqual(result.risked.coc.cost_of_debt, result.baseline.coc.cost_of_debt)
        paid = Guarantee("paid", target_ids=("grid",), params={"annual_fee_pct": .1, "upfront_fee_pct": 1})
        fees = paid.evaluate(self.context)
        self.assertTrue(np.all(fees.extra_cost[self.context.op_year > 18] == 0))
        stochastic = paid.evaluate(RiskContext(2, 25, 145, 65, 15, 55, rng=np.random.default_rng(42)))
        np.testing.assert_array_equal(stochastic.extra_cost, fees.extra_cost)
        result = analyse(self.inputs, [risk], interventions=[paid])
        self.assertAlmostEqual(result.risked.table.intervention_capex.sum(), 1.45)

    def test_country_guarantee_eligibility_and_shared_allowance(self):
        sovereign = SovereignRisk("sovereign", params={"rating": "BBB"})
        political = PoliticalRisk("political", params={"rating": "BBB"})
        grid = GridConnectionDelay("grid", premium_debt_bps=25)
        general = Guarantee("general", target_ids=("grid",), params={"annual_fee_pct": 0})
        self.assertTrue(general.eligible(grid))
        self.assertFalse(general.eligible(sovereign))
        country = CountryRiskGuarantee("country", target_ids=("sovereign", "political"),
                                       params={"debt_relief_pct": 50, "annual_fee_pct": 0})
        self.assertTrue(country.eligible(sovereign))
        self.assertTrue(country.eligible(political))
        self.assertFalse(country.eligible(grid))
        result = analyse(self.inputs, [sovereign, political, grid], interventions=[general, country])
        self.assertAlmostEqual(result.risked.impact.premium_debt_bps, 75 + 12.5)
        self.assertEqual(result.risked.impact.premium_equity_bps, result.unmitigated.impact.premium_equity_bps)
        np.testing.assert_allclose(result.risked.table.fcff, result.unmitigated.table.fcff)
        partial = CountryRiskGuarantee("partial", target_ids=("sovereign",), params={"annual_fee_pct": 0})
        result = analyse(self.inputs, [sovereign, political], interventions=[partial])
        self.assertEqual(result.risked.impact.premium_debt_bps, 150)
        with self.assertRaises(ValueError):
            analyse(self.inputs, [grid], interventions=[CountryRiskGuarantee("bad", target_ids=("grid",))])
        with self.assertRaises(ValueError):
            analyse(self.inputs, [sovereign], interventions=[Guarantee("bad", target_ids=("sovereign",))])

    def test_paired_intervention_simulation_and_attribution(self):
        risk = GridConnectionDelay("grid", premium_debt_bps=25)
        reform = PolicyReform("policy", target_ids=("grid",))
        paired = simulate(self.inputs, [risk], draws=60, interventions=[reform])
        original = simulate(self.inputs, [risk], draws=60)
        np.testing.assert_array_equal(paired.unmitigated_metrics.npv, original.metrics.npv)
        self.assertTrue(np.all(paired.metrics.npv >= original.metrics.npv - 1e-9))
        selected = paired.selected
        self.assertAlmostEqual(selected.attribution.npv.sum(), selected.risked.metrics["npv"] - selected.baseline.metrics["npv"])
        intervention = selected.intervention_attribution.iloc[0]
        self.assertAlmostEqual(intervention.lcoe, intervention.lcoe_cashflow + intervention.lcoe_cost_of_capital)
        np.testing.assert_allclose(selected.risked.table.fcff, selected.risked.table.ebitda - selected.risked.table.unlevered_tax - selected.risked.table.capex)

    def test_assistance_only_targets_preparation_components(self):
        risk = GenericRisk("mixed", params={"capex_impact_pct": 10, "generation_impact_pct": 20})
        ta = TechnicalAssistance("ta", target_ids=("mixed",))
        raw = risk.evaluate(self.context)
        effective, _ = apply_interventions([risk], {"mixed": raw}, [ta], self.context)
        np.testing.assert_array_equal(effective["mixed"].generation_factor, raw.generation_factor)
        self.assertTrue(np.all(effective["mixed"].capex_factor <= raw.capex_factor))


if __name__ == "__main__":
    unittest.main()