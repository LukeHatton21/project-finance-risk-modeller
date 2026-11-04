# Renewables Project Finance Risk Modeller

Streamlit app for exploring how discrete (event) and systemic risks affect the cashflow, LCOE, NPV, IRR, DSCR
and cost of capital of a renewable energy project.

## Methodology

The model is an annual, deterministic project-finance calculator with an optional Monte Carlo wrapper. It compares a
baseline project with a jointly risked case, can isolate cashflow losses from financing-premium effects, and reports
standalone risk contributions. Monetary cashflows are calculated in dollars and displayed in millions unless a metric
is explicitly per MWh or a rate.

### Model timeline and base case

The timeline contains `construction_years + lifetime_years` annual periods. Period zero is the first construction year;
operating year one begins after construction. Construction capex is spread evenly over the construction periods.
Technology presets provide starting assumptions for capex, fixed and variable O&M, capacity factor, degradation,
construction length, and operating life. All of these can be overridden.

For project year `t` and operating year `o`, base generation during operations is

```text
capacity (MW) x 8,760 hours/year x capacity factor x (1 - degradation rate)^(o - 1)
```

Revenue is generation multiplied by the applicable price. The PPA price applies for the first `ppa_tenor` operating
years; the merchant price applies thereafter. Both are escalated by the configured price escalation from project year
zero. Fixed O&M is capacity times fixed O&M per kW-year; variable O&M is generation times variable O&M per MWh. Both
are escalated from project year zero using the O&M escalation assumption. There is no generation or operating revenue
during construction.

### Financing, tax, and cashflows

The baseline cost of debt is the risk-free rate plus the debt margin; the baseline cost of equity is the risk-free rate
plus the equity premium. Risk premiums are added to the relevant rate. The nominal post-tax WACC is

```text
gearing x cost of debt x (1 - tax rate) + (1 - gearing) x cost of equity
```

Total debt is gearing multiplied by risk-adjusted total capex. The model calculates a level-payment annuity at the cost
of debt, with repayment beginning in the first operating year and continuing for the shorter of the debt tenor and
project life. No construction-period debt service is scheduled, and construction interest/IDC is not separately
calculated. Depreciation is straight-line over the selected number of operating years, based on total risk-adjusted
capex.

Taxable profit is floored at zero each year: tax is the tax rate multiplied by the positive amount of EBITDA less
depreciation and interest. The model does not carry tax losses forward. Cash flow available for debt service (CFADS) is
EBITDA less tax. Levered equity cashflow deducts debt service from CFADS and assigns the non-debt share of construction
capex to equity. Unlevered free cashflow (FCFF) is EBITDA less tax calculated without interest, less all capex.

FCFF is discounted at WACC for project NPV; equity cashflow is discounted at the cost of equity for equity NPV. Project
and equity IRRs are computed from their respective undiscounted cashflow series. LCOE is discounted total capex, O&M,
and additional risk/intervention operating costs divided by discounted generation. It is reported in dollars per MWh.

### Risk representation

Each risk evaluates to year-by-year factors for generation, price, O&M, and capex, plus additional costs and debt/equity
premium adders. A neutral factor is 1.0; a factor below 1 reduces the associated driver, while an O&M or capex factor
above 1 increases cost. Risk strength scales impact severity: Very low, Low, Medium, High, and Very high correspond to
0.25x, 0.5x, 1x, 1.5x, and 2x.

Discrete risks have an occurrence probability. Without Monte Carlo sampling, their cashflow impact is evaluated using
that probability as a fractional weight; this is an expected-impact scenario, not a particular event/no-event path.
Configured debt and equity premium adders are scaled by strength, but are not probability-weighted. Systemic risks
always apply and do not use an event probability. The built-in library covers construction cost and grid delays,
curtailment, resource yield, merchant prices, degradation, equipment failure, regulatory changes, offtaker default,
O&M inflation, sovereign and political risk, currency depreciation, and configurable generic risks.

When risks are combined, their generation, price, O&M, and capex factors multiply year by year; additional costs and
ordinary financing premiums add. Country-rating premiums use the largest country premium in the active set rather than
adding sovereign and political proxies together. The rating table in `data/sovereign_spreads.csv` is explicitly
illustrative, editable benchmark data, not live market pricing.

Currency risk applies an exchange-rate path to the configured local-currency shares of revenue and O&M. Revenue hedging
reduces the exposed revenue share; debt service remains USD-denominated. Users may enter depreciation and volatility
assumptions or calibrate them from annual historical `year,currency_per_usd` observations. Historical calibration
requires at least three consecutive annual observations and should be treated as an input estimate, not a forecast.

### Interventions

Interventions target selected risks and are applied sequentially. Their effectiveness reduces adverse cashflow impacts
and may provide separate debt- or equity-premium relief; reductions are bounded so they cannot mitigate more than the
targeted loss or premium. Interventions also add their configured upfront construction fee and/or annual operating fee.
Guarantees only transfer eligible financing-premium exposure and charge fees; they do not directly restore generation,
revenue, or CFADS. General guarantees target non-country credit risk, while country guarantees target rating-driven
country risk. Technical assistance is restricted to eligible development/capex risks. Target identifiers must be
unique, active, and eligible for the selected intervention.

### Scenario analysis and metrics

`analyse()` runs a no-risk baseline, a jointly risked case, a cashflow-only case with financing risk premiums excluded,
and one standalone run for each risk. The risk table provides indicative standalone cash impacts against unrisked
drivers; these do not include all tax, financing, or risk-interaction effects. Attribution compares each standalone
result with baseline and reports a separate interaction-effects row so the contributions reconcile to the joint
scenario. A cashflow-equivalent premium expresses the baseline-WACC increase that would produce the cashflow-only
scenario's NPV on baseline FCFF.

Key metrics are calculated as follows:

- **DSCR** is CFADS divided by scheduled interest plus principal in each debt-service year; minimum and average DSCR use
  only years with debt service.
- **LLCR** is the cost-of-debt discounted CFADS over debt-service years divided by outstanding debt at financial close.
- **Debt capacity** is that same discounted CFADS divided by the target DSCR. **Maximum gearing** is debt capacity
  divided by risk-adjusted total capex, bounded between 0% and 100%.
- **Revenue lost PV** is the WACC-discounted difference between base and risked revenue. **Equity payback** is the first
  operating year in which cumulative equity cashflow is nonnegative.

### Monte Carlo analysis

Simulation samples each discrete event as a Bernoulli occurrence and, for risks using the standard evaluator, samples a
mean-one lognormal severity multiplier. Rating-driven sovereign and political premiums are direct rating-table lookups.
Sampled event outcomes and severities feed the annual cashflows; the same sampled impacts are reused when comparing
mitigated and unmitigated cases. The default seed makes a run reproducible. Risk draws are independent; the model does
not estimate correlations between different risks. The FX risk, when stochastic, has a path across years.

The selected scenario is one coherent simulated path ranked by NPV. For example, a P90 NPV selection is the 10th
percentile of NPV (90% exceedance), not a synthetic path assembled from separate metric percentiles. The percentile
table uses lower-tail percentiles for NPV, equity IRR, minimum DSCR, and lifetime generation, and upper-tail
percentiles for LCOE. Simulation percentiles describe the configured assumptions and are not calibrated probabilities
of real-world outcomes.

### Scope and limitations

This is a screening and scenario-comparison model, not a lender's detailed financial model or an investment forecast.
It uses annual periods and simplified tax, debt, and construction schedules. It does not model tax-loss carryforwards,
working capital, refinancing, sculpted debt, construction drawdown/IDC, salvage value, or explicit correlations between
risks. Risk parameters, escalation rates, and premiums are user assumptions; the built-in sovereign spreads are
illustrative. Review and replace assumptions with project-specific and market-supported inputs before relying on
results.

## Code and data

- `project_cashflow.py` contains `ProjectInputs`, `ProjectCashflowModel`, `analyse()`, and `simulate()`.
- `risk_objects.py` defines the risk interface and categories. To add a coded category, subclass `Risk`, declare
  `param_specs`, implement `_impact()`, and register the class with `@register_risk`.
- `intervention_objects.py` defines targeted mitigation and financing interventions.
- `data/default_risks.csv` is the editable default risk library. Rows with `risk_type=generic` define configurable risks
  without adding a new Python class.
- `data/sovereign_spreads.csv` contains the illustrative rating premiums described above.

### How to run it on your own machine

Prerequisite: install `uv` if you don't already have it.

```
$ curl -LsSf https://astral.sh/uv/install.sh | sh
```

1. Sync the dependencies

   ```
   $ uv sync
   ```

2. Run the app

   ```
   $ uv run streamlit run streamlit_app.py
   ```
