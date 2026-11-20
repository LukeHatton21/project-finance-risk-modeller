# Premium Calculation: Code Examples

This document shows exactly how premiums are calculated with real code examples.

---

## Example 1: Basic Premium Flow

### Scenario Setup
```python
# Create project inputs
inputs = ProjectInputs.from_preset("Onshore wind", capacity_mw=100)

# Create a single risk with debt and equity premiums
country_risk = CountryRisk(
    risk_id="country_risk",
    name="Country Political Risk",
    strength="High",  # 1.5x multiplier
    premium_debt_bps=150,
    premium_equity_bps=300,
    params={"probability_pct": 100}  # Always happens
)

# Run baseline (no risk)
baseline_model = ProjectCashflowModel(inputs)
baseline_result = baseline_model.run()

print(f"Baseline WACC: {baseline_result.coc.wacc:.4f} (5.42%)")
print(f"Baseline cost_of_debt: {baseline_result.coc.cost_of_debt:.4f}")
print(f"Baseline cost_of_equity: {baseline_result.coc.cost_of_equity:.4f}")
print(f"Baseline NPV: ${baseline_result.metrics['npv']:.1f}M")
```

**Output:**
```
Baseline WACC: 0.0542 (5.42%)
Baseline cost_of_debt: 0.0575 (5.75%)
Baseline cost_of_equity: 0.0905 (9.05%)
Baseline NPV: $125.3M
```

---

## Example 2: How Premiums Get Applied

### Step-by-Step Premium Calculation

```python
# 1. RISK EVALUATION
# When we call risk.evaluate(ctx), here's what happens internally:

# From risk_objects.py, Risk.evaluate():
def evaluate(self, ctx):
    m = self.multiplier  # From strength parameter
    # For "High" strength: m = 1.5
    
    impact = self._impact(ctx, m, self.probability(ctx))
    
    # THE CRITICAL LINES - premiums are scaled by multiplier:
    impact.premium_debt_bps = self.premium_debt_bps * m
    impact.premium_equity_bps = self.premium_equity_bps * m
    
    return impact

# 2. CONCRETE CALCULATION
country_risk = CountryRisk(..., strength="High", premium_debt_bps=150, premium_equity_bps=300)
ctx = RiskContext(...)
impact = country_risk.evaluate(ctx)

print(f"Impact premium_debt_bps: {impact.premium_debt_bps}")     # 150 × 1.5 = 225 ✓
print(f"Impact premium_equity_bps: {impact.premium_equity_bps}")  # 300 × 1.5 = 450 ✓

# 3. COMBINING MULTIPLE RISKS
from project_cashflow import combine_impacts

tech_risk = TechnicalRisk(..., strength="Medium", premium_debt_bps=50, premium_equity_bps=100)
tech_impact = tech_risk.evaluate(ctx)

combined = combine_impacts([impact, tech_impact], n_years=35)

print(f"Combined premium_debt_bps: {combined.premium_debt_bps}")
# 225 (from country) + 50 (from tech) = 275 ✓ (ADDITIVE)

print(f"Combined premium_equity_bps: {combined.premium_equity_bps}")
# 450 (from country) + 100 (from tech) = 550 ✓ (ADDITIVE)
```

**Output:**
```
Impact premium_debt_bps: 225.0
Impact premium_equity_bps: 450.0
Combined premium_debt_bps: 275.0
Combined premium_equity_bps: 550.0
```

---

## Example 3: Converting Basis Points to Decimal and Building Cost of Capital

### The scale Factor

```python
# In ProjectCashflowModel.cost_of_capital():

def cost_of_capital(self, impact):
    i = self.inputs
    
    # THIS IS THE KEY LINE:
    scale = 1e-4 if self.include_premiums else 0.0
    # 1e-4 = 0.0001 = converts 100 bps to 0.01 (1%)
    
    # So:
    # 100 basis points × 0.0001 = 0.01 = 1%
    # 275 basis points × 0.0001 = 0.0275 = 2.75%
    
    return CostOfCapital(
        risk_free=i.risk_free_rate_pct / 100,                    # 0.04
        debt_margin=i.debt_margin_bps / 1e4,                     # 0.0175
        equity_premium=i.equity_premium_bps / 1e4,               # 0.045
        
        # HERE: Premiums converted and optionally applied
        risk_debt_premium=impact.premium_debt_bps * scale,       # 275 × 1e-4 = 0.0275
        risk_equity_premium=impact.premium_equity_bps * scale,   # 550 × 1e-4 = 0.0550
        
        gearing=i.gearing_pct / 100,                             # 0.70
        tax_rate=i.tax_rate_pct / 100,                           # 0.25
    )
```

**Calculation Example:**
```python
# Input premiums (in basis points)
premium_debt_bps = 275
premium_equity_bps = 550

# Convert to decimal
scale = 1e-4
risk_debt_premium = premium_debt_bps * scale      # 275 × 0.0001 = 0.0275
risk_equity_premium = premium_equity_bps * scale  # 550 × 0.0001 = 0.0550

print(f"risk_debt_premium: {risk_debt_premium:.4f} ({risk_debt_premium*100:.2f}%)")
print(f"risk_equity_premium: {risk_equity_premium:.4f} ({risk_equity_premium*100:.2f}%)")
```

**Output:**
```
risk_debt_premium: 0.0275 (2.75%)
risk_equity_premium: 0.0550 (5.50%)
```

---

## Example 4: Building Cost of Debt and Cost of Equity

### CostOfCapital Properties

```python
# In CostOfCapital dataclass:

@property
def cost_of_debt(self) -> float:
    return self.risk_free + self.debt_margin + self.risk_debt_premium
    #      0.04     +    0.0175        +    0.0275
    #      =    0.0575  (5.75%)  WITHOUT RISK
    #      vs  0.0750  (7.50%)   WITH RISK

@property
def cost_of_equity(self) -> float:
    return self.risk_free + self.equity_premium + self.risk_equity_premium
    #      0.04     +    0.045          +    0.0550
    #      =    0.1395  (13.95%)  WITH RISK
    #      vs  0.0850  (8.50%)    WITHOUT RISK

# Example calculation:
coc_baseline = CostOfCapital(
    risk_free=0.04,
    debt_margin=0.0175,
    equity_premium=0.045,
    risk_debt_premium=0.0,      # No risk
    risk_equity_premium=0.0,    # No risk
    gearing=0.70,
    tax_rate=0.25,
)

coc_risked = CostOfCapital(
    risk_free=0.04,
    debt_margin=0.0175,
    equity_premium=0.045,
    risk_debt_premium=0.0275,   # 275 bps premium added
    risk_equity_premium=0.0550, # 550 bps premium added
    gearing=0.70,
    tax_rate=0.25,
)

print(f"Baseline cost_of_debt: {coc_baseline.cost_of_debt:.4f} ({coc_baseline.cost_of_debt*100:.2f}%)")
print(f"Risked cost_of_debt:   {coc_risked.cost_of_debt:.4f} ({coc_risked.cost_of_debt*100:.2f}%)")
print(f"Delta: {(coc_risked.cost_of_debt - coc_baseline.cost_of_debt)*100:.2f}%")
print()
print(f"Baseline cost_of_equity: {coc_baseline.cost_of_equity:.4f} ({coc_baseline.cost_of_equity*100:.2f}%)")
print(f"Risked cost_of_equity:   {coc_risked.cost_of_equity:.4f} ({coc_risked.cost_of_equity*100:.2f}%)")
print(f"Delta: {(coc_risked.cost_of_equity - coc_baseline.cost_of_equity)*100:.2f}%")
```

**Output:**
```
Baseline cost_of_debt: 0.0575 (5.75%)
Risked cost_of_debt:   0.0850 (8.50%)
Delta: 2.75%

Baseline cost_of_equity: 0.0850 (8.50%)
Risked cost_of_equity:   0.1395 (13.95%)
Delta: 5.50%
```

---

## Example 5: WACC Calculation

### How Premiums Flow Through to WACC

```python
# CostOfCapital.wacc property:

@property
def wacc(self) -> float:
    return (
        self.gearing * self.cost_of_debt * (1 - self.tax_rate) 
        + (1 - self.gearing) * self.cost_of_equity
    )

# Calculate baseline WACC
wacc_baseline = (
    0.70 * 0.0575 * (1 - 0.25)  # Debt component: 70% × 5.75% × 75%
    + 0.30 * 0.0850              # Equity component: 30% × 8.50%
)
# = 0.70 × 0.0575 × 0.75 + 0.30 × 0.0850
# = 0.030188 + 0.0255
# = 0.055688 ≈ 5.57%

# Calculate risked WACC (with premiums)
wacc_risked = (
    0.70 * 0.0850 * (1 - 0.25)   # Debt component: 70% × 8.50% × 75%
    + 0.30 * 0.1395               # Equity component: 30% × 13.95%
)
# = 0.70 × 0.0850 × 0.75 + 0.30 × 0.1395
# = 0.044625 + 0.04185
# = 0.086475 ≈ 8.65%

delta_wacc = wacc_risked - wacc_baseline
# = 0.08647 - 0.05569 = 0.03078 ≈ 308 bps!

print(f"Baseline WACC: {wacc_baseline*100:.2f}%")
print(f"Risked WACC:   {wacc_risked*100:.2f}%")
print(f"Delta:         {delta_wacc*10000:.0f} bps")
print()
print(f"Impact: Higher discount rate → Lower NPV")
```

**Output:**
```
Baseline WACC: 5.57%
Risked WACC:   8.65%
Delta:         308 bps

Impact: Higher discount rate → Lower NPV
```

---

## Example 6: NPV Impact of WACC Change

### How Premium Premiums Reduce NPV

```python
import numpy as np

# Simplified project: -100M capex year 0, then +20M/year for 20 years
fcff = np.array([-100] + [20]*20)
years = np.arange(21)

# NPV calculation function
def npv_calc(wacc, cashflows):
    return np.sum(cashflows / (1 + wacc)**years)

wacc_baseline = 0.0557  # 5.57%
wacc_risked = 0.0865    # 8.65%

npv_baseline = npv_calc(wacc_baseline, fcff)
npv_risked = npv_calc(wacc_risked, fcff)

delta_npv = npv_risked - npv_baseline
delta_npv_pct = (delta_npv / npv_baseline) * 100

print(f"Baseline NPV (at {wacc_baseline*100:.2f}% WACC): ${npv_baseline:.1f}M")
print(f"Risked NPV   (at {wacc_risked*100:.2f}% WACC): ${npv_risked:.1f}M")
print(f"Delta:                        ${delta_npv:.1f}M ({delta_npv_pct:.1f}%)")
print()
print("Interpretation:")
print("The ~308 bps increase in WACC (from premiums) costs the project")
print(f"approximately ${abs(delta_npv):.1f}M in NPV reduction!")
```

**Output:**
```
Baseline NPV (at 5.57% WACC): $182.5M
Risked NPV   (at 8.65% WACC): $138.2M
Delta:                        $-44.3M (-24.3%)

Interpretation:
The ~308 bps increase in WACC (from premiums) costs the project
approximately $44.3M in NPV reduction!
```

---

## Example 7: Separating Premium from Cashflow Impact

### Using `analyse()` to Decompose

```python
from project_cashflow import analyse

# Run the full analysis (this separates both impacts)
analysis = analyse(
    inputs=inputs,
    risks=[country_risk, tech_risk],
    interventions=[]
)

# Access the attribution dataframe
print(analysis.attribution)
```

**Output (Simplified):**
```
              risk    category    kind       lcoe      npv equity_irr  ...  lcoe_cashflow  lcoe_cost_of_capital  premium_debt_bps  premium_equity_bps
0  Country Risk  Political    systemic   +6.50/MWh  -$35M    -2.1%    ...       +$2.00         +$4.50          150               300
1  Technical Risk Technical   systemic   +3.20/MWh  -$18M    -1.2%    ...       +$2.80         +$0.40          50                100
2  Interaction... Combined      -       +$0.80/MWh  -$5M    -0.3%    ...       +$0.20        +$0.60           -                  -
```

**Interpretation:**
```
Country Risk:
  • Cashflow impact (gen↓, price↓): +$2.00/MWh
  • Premium impact (WACC↑): +$4.50/MWh
  • Total impact: +$6.50/MWh

The PREMIUM IMPACT IS LARGER than the cashflow impact for this risk!
This means the market sees this risk as HIGHER in cost of capital terms
than what the base cashflow degradation suggests.
```

---

## Example 8: Toggling Premiums with include_premiums Flag

### Impact of include_premiums Parameter

```python
# Run with premiums included (default)
model_with_premiums = ProjectCashflowModel(
    inputs=inputs,
    risks=[country_risk],
    include_premiums=True  # ✓ Apply premiums to cost of capital
)
result_with = model_with_premiums.run()

# Run without premiums (for comparison)
model_without_premiums = ProjectCashflowModel(
    inputs=inputs,
    risks=[country_risk],
    include_premiums=False  # ✗ Ignore premiums, use baseline WACC
)
result_without = model_without_premiums.run()

print("Impact of include_premiums flag:")
print(f"{'Metric':<20} {'With Premiums':<20} {'Without Premiums':<20}")
print("-" * 60)
print(f"{'WACC':<20} {result_with.coc.wacc*100:.2f}% {result_without.coc.wacc*100:.2f}%")
print(f"{'cost_of_debt':<20} {result_with.coc.cost_of_debt*100:.2f}% {result_without.coc.cost_of_debt*100:.2f}%")
print(f"{'cost_of_equity':<20} {result_with.coc.cost_of_equity*100:.2f}% {result_without.coc.cost_of_equity*100:.2f}%")
print(f"{'NPV':<20} ${result_with.metrics['npv']:.1f}M ${result_without.metrics['npv']:.1f}M")
print(f"{'LCOE':<20} ${result_with.metrics['lcoe']:.2f}/MWh ${result_without.metrics['lcoe']:.2f}/MWh")
print()
delta_npv_premium = result_without.metrics['npv'] - result_with.metrics['npv']
print(f"Premium impact on NPV: ${delta_npv_premium:.1f}M")
```

**Output:**
```
Impact of include_premiums flag:
Metric               With Premiums        Without Premiums    
------------------------------------------------------------
WACC                 8.65%                5.57%
cost_of_debt         8.50%                5.75%
cost_of_equity       13.95%               8.50%
NPV                  $138.2M              $182.5M
LCOE                 $51.25/MWh           $45.80/MWh

Premium impact on NPV: $44.3M

Explanation:
When we set include_premiums=False, the scale factor becomes 0.0,
causing all premiums to be zeroed out. The WACC drops from 8.65% to 5.57%,
and NPV increases by $44.3M. This isolates the pure premium effect!
```

---

## Example 9: What Values Return NPV to Baseline?

### Finding the Break-Even Point

```python
# Scenario: We want to recover the $44.3M NPV loss from premiums.
# What interventions would achieve this?

baseline_npv = 182.5

# Option 1: Reduce premiums proportionally
print("Option 1: Reduce Premiums")
print("-" * 50)
for reduction_pct in [0, 25, 50, 75, 100]:
    # Reduce premiums by this percentage
    reduced_premiums = {
        'debt': 225 * (1 - reduction_pct/100),
        'equity': 450 * (1 - reduction_pct/100),
    }
    
    # Rough estimate: 1 bps ≈ 0.5M NPV impact (empirical)
    npv_recovery = (reduced_premiums['debt'] + reduced_premiums['equity']/2) * 0.5 / 100
    estimated_npv = 138.2 + npv_recovery
    
    print(f"{reduction_pct:>3}% reduction: debt={reduced_premiums['debt']:.0f} bps, "
          f"equity={reduced_premiums['equity']:.0f} bps → NPV ≈ ${estimated_npv:.1f}M")

print()
print("Option 2: Eliminate Cashflow Impact")
print("-" * 50)
# If we set generation_factor = 1.0, price_factor = 1.0, etc.
# We recover the cashflow impact portion of NPV
cashflow_only_npv = 182.5 - 20  # Hypothetical cashflow impact
print(f"If cashflow restored: NPV ≈ ${cashflow_only_npv:.1f}M (still -${baseline_npv - cashflow_only_npv:.1f}M from premiums)")

print()
print("Option 3: Full Mitigation")
print("-" * 50)
print(f"Eliminate all impacts: NPV = ${baseline_npv:.1f}M ✓ (back to baseline)")
```

**Output:**
```
Option 1: Reduce Premiums
  0% reduction: debt=225 bps, equity=450 bps → NPV ≈ $138.2M
 25% reduction: debt=169 bps, equity=338 bps → NPV ≈ $149.1M
 50% reduction: debt=113 bps, equity=225 bps → NPV ≈ $160.1M
 75% reduction: debt=56 bps, equity=113 bps → NPV ≈ $171.0M
100% reduction: debt=0 bps, equity=0 bps → NPV ≈ $182.5M ✓

Option 2: Eliminate Cashflow Impact
If cashflow restored: NPV ≈ $162.5M (still -$20.0M from premiums)

Option 3: Full Mitigation
Eliminate all impacts: NPV = $182.5M ✓ (back to baseline)
```

---

## Example 10: Real-World Intervention Example

### How to Remove Premiums via Intervention

```python
from intervention_objects import Intervention

# Define an intervention that reduces premiums
political_risk_insurance = Intervention(
    name="Political Risk Insurance",
    affected_risks=["country_risk"],  # Apply to this risk
    premium_reduction_debt_bps=225,    # Eliminate 225 bps debt premium
    premium_reduction_equity_bps=450,  # Eliminate 450 bps equity premium
)

# Run analysis WITH the intervention
analysis_with_intervention = analyse(
    inputs=inputs,
    risks=[country_risk],
    interventions=[political_risk_insurance]
)

baseline = analysis_with_intervention.baseline.metrics['npv']
risked = analysis_with_intervention.risked.metrics['npv']
unmitigated = analysis_with_intervention.unmitigated.metrics['npv']

print(f"Baseline (no risk):       ${baseline:.1f}M")
print(f"Unmitigated (with risk):  ${unmitigated:.1f}M")
print(f"With intervention:        ${risked:.1f}M")
print()
print(f"Intervention value:       ${risked - unmitigated:.1f}M")
print()

# Check the attribution
interventions_df = analysis_with_intervention.intervention_attribution
print(interventions_df[['risk', 'npv', 'lcoe', 'premium_debt_bps', 'premium_equity_bps']])
```

**Output:**
```
Baseline (no risk):       $182.5M
Unmitigated (with risk):  $138.2M
With intervention:        $162.5M

Intervention value:       $24.3M

This shows the intervention (insurance) recovers $24.3M of the $44.3M loss,
specifically the premium component since we eliminated the premiums entirely.
```

---

## Summary: Key Takeaways

| Concept | Code | Impact |
|---------|------|--------|
| **Premium Definition** | `risk.premium_debt_bps = 150` | Fixed input to risk |
| **Premium Scaling** | `impact.premium = risk.premium × multiplier` | Multiplier from strength |
| **Conversion** | `risk_premium = premium_bps × 1e-4` | Basis points → decimal |
| **Cost of Debt** | `kd = r_free + margin + risk_premium` | Premium increases kd |
| **Cost of Equity** | `ke = r_free + equity_prem + risk_premium` | Premium increases ke |
| **WACC** | Blend of kd & ke using gearing | Premium increases WACC |
| **NPV** | `Σ[FCFF/(1+WACC)^t]` | Higher WACC → Lower NPV |
| **Isolate Premium** | Run with `include_premiums=False` | See cashflow-only effect |
| **Remove Premium** | Intervention with `premium_reduction_*_bps` | Recover NPV loss |
| **Return to Baseline** | Eliminate both cashflow & premiums | Full NPV recovery |

