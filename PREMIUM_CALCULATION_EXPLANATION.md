# Cost-of-Capital Premium Calculation Explained

## Overview

The project finance model uses **two independent mechanisms** to quantify risk impact:

1. **Cashflow Drivers Impact** - Multiplicative factors and cost adders on generation, revenue, opex, capex
2. **Cost-of-Capital Premium** - Additive basis points added to cost of debt and cost of equity

This document explains how premiums are calculated and applied to arrive at the risked NPV.

---

## Premium Architecture

### 1. Where Premiums Come From

**Source: Risk Objects** (`risk_objects.py`)

Each `Risk` object has two parameters:
```python
premium_debt_bps: float = 0.0        # Additional debt cost in basis points
premium_equity_bps: float = 0.0      # Additional equity cost in basis points
```

These are **fixed inputs** to the risk (defined per risk in the CSV or programmatically).

Example:
- Country Risk: `premium_debt_bps=150, premium_equity_bps=300`
- Technical Risk: `premium_debt_bps=50, premium_equity_bps=100`

### 2. How Premiums Get Into Cost of Capital

**Flow: Risk → RiskImpact → CostOfCapital → WACC → NPV**

```
Step 1: Risk.evaluate(ctx) → RiskImpact
─────────────────────────────────────
    impact.premium_debt_bps = self.premium_debt_bps * self.multiplier
    impact.premium_equity_bps = self.premium_equity_bps * self.multiplier

Step 2: combine_impacts(impacts) → Combined RiskImpact
──────────────────────────────────────────────────────
    For normal risks: additive across risks
    combined.premium_debt_bps += imp.premium_debt_bps
    combined.premium_equity_bps += imp.premium_equity_bps
    
    For country premium (special case): take maximum only
    combined.premium_debt_bps = max([imp.premium_debt_bps for imp in country_risks])

Step 3: ProjectCashflowModel.cost_of_capital(impact) → CostOfCapital
────────────────────────────────────────────────────────────────────
    scale = 1e-4 if include_premiums else 0.0
    
    coc.risk_debt_premium = impact.premium_debt_bps * scale      # Convert bps → decimal
    coc.risk_equity_premium = impact.premium_equity_bps * scale  # e.g., 100 bps → 0.01

Step 4: CostOfCapital properties → Rates
──────────────────────────────────────────
    cost_of_debt = risk_free + debt_margin + risk_debt_premium
                 = 0.04 + 0.0175 + 0.0100  = 0.0675  (6.75%)
    
    cost_of_equity = risk_free + equity_premium + risk_equity_premium
                   = 0.04 + 0.045 + 0.0300  = 0.115  (11.5%)

Step 5: WACC Calculation
───────────────────────
    wacc = gearing * cost_of_debt * (1 - tax_rate) 
         + (1 - gearing) * cost_of_equity
         
         = 0.70 * 0.0675 * 0.75 + 0.30 * 0.115
         = 0.0354 + 0.0345  = 0.0699  (6.99%)

Step 6: NPV Calculation
──────────────────────
    NPV = Σ [FCFF(t) / (1 + WACC)^t]
    
    Higher WACC → Lower NPV (more discounting)
```

---

## Numerical Example: How Premium Reduces NPV

### Baseline (No Risk)
```
Inputs:
  WACC = 6.0%
  FCFF = [−$100m, $20m, $20m, $20m, ...] (capex yr 0, then operations)

Calculation:
  NPV = -100 / 1.06^0 + 20 / 1.06^1 + 20 / 1.06^2 + 20 / 1.06^3 + ...
      = -100 + 18.87 + 17.80 + 16.79 + ...
      = +$150m (NPV)
```

### With Risk + Cost-of-Capital Premium
```
Inputs:
  Risk adds: premium_debt_bps = 100, premium_equity_bps = 200
  
Adjusted WACC:
  Risk premiums in decimal: debt = 0.01, equity = 0.02
  
  cost_of_debt = 3.0% + 1.75% + 1.00% = 5.75%
  cost_of_equity = 3.0% + 4.5% + 2.00% = 9.5%
  
  WACC = 0.70 * 5.75% * 0.75 + 0.30 * 9.5%
       = 3.03% + 2.85% = 5.88%  →  Originally 6.0% (less risk here)
  
  Actually, let's assume risk increases WACC to 7.0%

Risked NPV:
  NPV = -100 / 1.07^0 + 20 / 1.07^1 + 20 / 1.07^2 + 20 / 1.07^3 + ...
      = -100 + 18.69 + 17.47 + 16.33 + ...
      = +$135m (NPV)
  
Impact of Premium Alone:
  ΔNPV = $135m - $150m = -$15m
  
  → The 100 bps premium costs $15m in NPV
```

---

## Decomposing Risk Impact on NPV

The model attributes NPV impact to **two independent sources**:

### Attribution (via `analyse()` function)

For each risk, the model calculates:

```python
1. Cashflow Impact Only (include_premiums=False)
   ─────────────────────────────────────────────
   Run the model with:
   - Risk factors applied to generation, revenue, opex, capex
   - NO cost-of-capital premiums
   - Use BASELINE WACC for discounting
   
   Result: alone_cf
   ΔLCOE_cashflow = alone_cf.lcoe - baseline.lcoe

2. Full Impact (include_premiums=True)
   ───────────────────────────────────
   Run the model with:
   - Risk factors applied to cashflows
   - Risk premiums ADDED to WACC
   
   Result: alone
   ΔLCOE_total = alone.lcoe - baseline.lcoe

3. Premium Impact (Residual)
   ──────────────────────────
   ΔLCOE_premium = ΔLCOE_total - ΔLCOE_cashflow
   
   This is the pure effect of raising discount rate
```

### Example Attribution Output

| Risk | LCOE Impact | Cashflow | Premium | Premium (bps) |
|------|------------|----------|---------|---------------|
| Country Risk | +$5.00/MWh | +$2.50 | +$2.50 | 150 debt / 300 equity |
| Technical Risk | +$8.00/MWh | +$7.00 | +$1.00 | 50 debt / 100 equity |
| Combined | +$12.50/MWh | +$9.00 | +$3.50 | (additive) |

---

## What Would Need to Change to Return to Baseline NPV?

To arrive back at the **baseline NPV** after a risk applies a negative impact:

### Option 1: Eliminate Cashflow Degradation
```
If risk causes generation_factor = 0.95 (5% loss)
Then set: generation_factor = 1.00 (no loss)

Result: Cashflow component returns to baseline
Still have premium impact on WACC
Partial recovery of NPV
```

### Option 2: Eliminate Cost-of-Capital Premium
```
Set: premium_debt_bps = 0 and premium_equity_bps = 0
Or:  set include_premiums = False

Result: Use baseline WACC for discounting
Still have cashflow degradation
Partial recovery of NPV
```

### Option 3: Full Mitigation (Both)
```
Eliminate both:
  1. Cashflow factors return to 1.0 (no generation/revenue loss)
  2. Premiums return to 0 bps (no WACC increase)

Result: Full return to baseline NPV
Example via interventions:
  - Insurance → Eliminates premium (transfers risk to insurer)
  - Performance guarantee → Guarantees generation_factor
  - Capex guarantee → Guarantees capex_factor
```

---

## Key Equations

### Cost of Debt with Premium
```
kd = r_free + margin + premium_debt_bps * 1e-4

where:
  r_free = risk-free rate
  margin = credit spread
  premium_debt_bps = risk premium in basis points
  1e-4 = conversion factor (100 bps = 1%)
```

### Cost of Equity with Premium
```
ke = r_free + equity_premium + premium_equity_bps * 1e-4
```

### Post-Tax WACC
```
WACC = gearing * kd * (1 - tax_rate) + (1 - gearing) * ke

Higher WACC means:
  - Lower discount factors: 1/(1+WACC)^t
  - Lower present values
  - Lower NPV (projects worth less)
```

### NPV with Risk
```
NPV_risked = Σ [FCFF_risked(t) / (1 + WACC_risked)^t]

Where FCFF_risked includes:
  - Reduced generation (generation_factor < 1)
  - Reduced revenue (price_factor < 1)
  - Increased opex (opex_factor > 1)
  - Increased capex (capex_factor > 1)
  
And WACC_risked includes:
  - Elevated discount rate from premiums
```

---

## Special Case: Cashflow-Equivalent Premium

The function `cashflow_equivalent_premium()` answers:

> **What discount-rate premium (applied to baseline unrisked cashflows) reproduces the NPV of risked cashflows?**

```python
target = NPV(baseline_WACC, risked_FCFF_with_no_premiums)
equivalent = find_rate_r such that NPV(r, baseline_FCFF) = target

equivalent_premium_bps = (r - baseline_WACC) * 10000
```

**Usage**: Isolates the "pure cashflow risk" as a discount-rate equivalent.

**Example**:
```
Scenario A: Risk causes 10% revenue loss
            Baseline WACC: 6.0%
            Equivalent premium: +50 bps
            
Interpretation: The 10% revenue loss is equivalent to 
                paying a 50 bps higher discount rate
                on baseline unrisked cashflows.
```

---

## Modifying Premiums: Practical Approaches

### 1. Direct Risk Parameter Adjustment
```python
# Increase premium via strength parameter
risk = CountryRisk(
    ...,
    strength="High",  # 1.5x multiplier
    premium_debt_bps=150,  # Will be multiplied by 1.5 → 225 bps
)
```

### 2. Via Interventions (De-risking)
```python
# Example: Political Risk Insurance
intervention = Intervention(
    name="Political Risk Insurance",
    affected_risks=["country_risk"],
    premium_reduction_debt_bps=150,  # Eliminate all debt premium
    premium_reduction_equity_bps=300,  # Eliminate all equity premium
)
```

### 3. Set Premiums to Zero
```python
# Remove all premiums for sensitivity analysis
risk.premium_debt_bps = 0.0
risk.premium_equity_bps = 0.0
```

### 4. Use `include_premiums=False` Flag
```python
model = ProjectCashflowModel(
    inputs,
    risks,
    include_premiums=False  # Ignore all premiums
)
result_no_premium = model.run()
# WACC uses baseline rates only
# See pure cashflow impact
```

---

## Summary

| Aspect | Detail |
|--------|--------|
| **Source** | Risk.premium_debt_bps / Risk.premium_equity_bps |
| **Units** | Basis points (1 bp = 0.01%) |
| **Conversion** | × 1e-4 to get decimal (100 bp = 0.01) |
| **Application** | Added to cost of debt and cost of equity |
| **Effect** | Increases WACC → Lowers NPV (more discounting) |
| **Mechanism** | Reflects market's increased return requirement for risk |
| **Attribution** | Separated from cashflow impact via dual runs |
| **Elimination** | Interventions remove premiums → NPV recovers partially |

---

## Code References

**Risk Definition** (`risk_objects.py`):
- Lines 105-106: RiskImpact premium fields
- Lines 241-242: Premium scaling by multiplier
- Lines 142-146: combine_impacts adds/max premiums

**Premium Application** (`project_cashflow.py`):
- Lines 290-297: cost_of_capital() converts bps → decimal
- Lines 90-105: CostOfCapital properties compute cost of debt/equity/WACC
- Lines 480-520: analyse() function separates impacts

**Premium Filtering**:
- Line 290: `scale = 1e-4 if self.include_premiums else 0.0`
  - Set scale=0 → Premiums ignored → Baseline WACC used
  - Set scale=1e-4 → Premiums applied → Risked WACC used
