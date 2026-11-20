# Premium Calculation: One-Page Quick Reference

## The Core Mechanism

```
RISK INPUT (Fixed)
│
├─ premium_debt_bps = 150          (e.g., country risk)
└─ premium_equity_bps = 300
        │
        ▼
MULTIPLY BY STRENGTH
│
├─ Strength = "High" = 1.5× multiplier
├─ Scaled debt premium = 150 × 1.5 = 225 bps
└─ Scaled equity premium = 300 × 1.5 = 450 bps
        │
        ▼
COMBINE MULTIPLE RISKS (Additive)
│
├─ Risk 1: 225 bps debt + 450 bps equity
├─ Risk 2: 50 bps debt + 100 bps equity
├─ Combined: 275 bps debt + 550 bps equity
        │
        ▼
CONVERT TO DECIMAL (× 1e-4)
│
├─ 275 bps × 0.0001 = 0.0275 (2.75%)
└─ 550 bps × 0.0001 = 0.0550 (5.50%)
        │
        ▼
ADD TO COST OF CAPITAL
│
├─ cost_of_debt = 5.75% + 2.75% = 8.50%
├─ cost_of_equity = 8.50% + 5.50% = 14.00%
        │
        ▼
CALCULATE WACC
│
├─ WACC = 70% × 8.50% × 75% + 30% × 14.00%
└─ WACC = 8.63% (up from baseline 5.57%)
        │
        ▼
DISCOUNT CASHFLOWS
│
├─ NPV = Σ [FCFF(t) / (1 + WACC)^t]
├─ Higher WACC = Lower NPV
└─ NPV = $138M (down $44.5M from baseline $182.5M)
```

---

## Three Key Facts

### 1️⃣ Premium is INDEPENDENT of Cashflow Impact
- Premium is a fixed market input (like credit spreads)
- NOT derived from "what WACC increase reproduces cashflow loss"
- Applies UNIFORMLY via discount rate mechanism
- **Result**: Both premium AND cashflow reduce NPV, but separately

### 2️⃣ Total NPV Loss = Cashflow Loss + Premium Loss
```
Example:
  Total loss:      -$44.5M
  = Cashflow loss: -$12.0M (generation↓, revenue↓, capex↑)
  + Premium loss:  -$32.5M (WACC increase from 5.57% → 8.63%)
```

### 3️⃣ To Return to Baseline NPV, Modify One or Both:

| Approach | Action | Recovery | Still Down |
|----------|--------|----------|-----------|
| **Premium Only** | Intervention: `premium_reduction_debt_bps=225` | +$32.5M | $12.0M (cashflow) |
| **Cashflow Only** | Guarantee: `generation_factor=1.0` | +$12.0M | $32.5M (premium) |
| **Both** | Comprehensive mitigation | +$44.5M | $0M ✓ Baseline |

---

## Code Quick Reference

### View Premiums in Risk Object
```python
risk = CountryRisk(..., premium_debt_bps=150, premium_equity_bps=300)
print(f"Premium: {risk.premium_debt_bps} bps debt, {risk.premium_equity_bps} bps equity")
```

### Isolate Premium Impact
```python
# Run with premiums
result_with = ProjectCashflowModel(inputs, risks, include_premiums=True).run()

# Run without premiums (cashflow only)
result_without = ProjectCashflowModel(inputs, risks, include_premiums=False).run()

# Delta is pure premium impact
premium_impact_npv = result_without.metrics['npv'] - result_with.metrics['npv']
print(f"Premium costs ${premium_impact_npv:.1f}M in NPV")
```

### Remove Premiums via Intervention
```python
intervention = Intervention(
    name="Political Risk Insurance",
    affected_risks=["country_risk"],
    premium_reduction_debt_bps=225,  # Remove entire debt premium
    premium_reduction_equity_bps=450,  # Remove entire equity premium
)

analysis = analyse(inputs, risks, interventions=[intervention])
# NPV partially recovered (cashflow loss remains)
```

### View Attribution
```python
analysis = analyse(inputs, risks)
print(analysis.attribution)
# Shows: lcoe_cashflow | lcoe_cost_of_capital | premium_debt_bps | premium_equity_bps
```

---

## The include_premiums Flag Explained

```
include_premiums = True  (default)
├─ scale = 1e-4
├─ Premiums ARE applied
├─ WACC elevated (8.63%)
└─ NPV lower ($138M)

include_premiums = False
├─ scale = 0.0
├─ Premiums are ZEROED OUT
├─ WACC at baseline (5.57%)
└─ NPV higher ($182.5M, but still below if cashflow impact exists)
```

**Used in `analyse()`**:
- `risked = ProjectCashflowModel(..., include_premiums=True)`  → Full impact
- `cashflow_only = ProjectCashflowModel(..., include_premiums=False)` → Cashflow only
- Difference reveals pure premium impact

---

## Conversion Reminder

| Basis Points | Decimal | Percentage |
|--------------|---------|-----------|
| 100 bps | × 1e-4 = 0.01 | 1% |
| 275 bps | × 1e-4 = 0.0275 | 2.75% |
| 550 bps | × 1e-4 = 0.0550 | 5.50% |
| 1000 bps | × 1e-4 = 0.10 | 10% |

---

## Where Does Premium Come From?

**Sources of premium_bps values:**

1. **Risk CSV Input** (`data/default_risks.csv`)
   - Each risk type has default premiums
   - Example: CountryRisk → premium_debt_bps=150, premium_equity_bps=300

2. **Credit Rating Lookup** (for rating-driven risks)
   - Sovereign spreads from `data/sovereign_spreads.csv`
   - Example: Rating "BBB" → premium_debt_bps=200, premium_equity_bps=400

3. **Manual Input** (programmatic)
   ```python
   risk = CountryRisk(..., premium_debt_bps=250, premium_equity_bps=500)
   ```

4. **Strength Scaling** (after input)
   - Base premium multiplied by strength multiplier
   - "Low" (0.5×), "Medium" (1.0×), "High" (1.5×), etc.

---

## Common Questions Answered

**Q: If I set premium_debt_bps=0, does NPV return to baseline?**
A: Only if there's NO cashflow impact. Premium is only 50% of the typical NPV loss.

**Q: Can premiums be negative?**
A: Technically yes (would reduce WACC), but rarely used. Reflects risk transfer/mitigation.

**Q: Do premiums change during project operation?**
A: No, they're applied uniformly year-by-year. WACC is constant throughout.

**Q: Why are premiums for equity higher than debt?**
A: Equity investors demand higher returns for bearing residual risk (after debt is paid).

**Q: If I eliminate premiums but keep cashflow impacts, what percentage of NPV is recovered?**
A: Depends on project structure, but typically:
   - Premium impact: 60-70% of total loss
   - Cashflow impact: 30-40% of total loss

**Q: Can country premium (maximum rule) be applied to other risks?**
A: Currently only for country-level risks (political, regulatory). Normal risks are additive.

---

## Formula Reference

```
Cost of Debt:
  kd = r_free + margin + (premium_debt_bps × 1e-4)
  kd = 3% + 1.75% + 2.75% = 7.50%

Cost of Equity:
  ke = r_free + equity_prem + (premium_equity_bps × 1e-4)
  ke = 3% + 4.5% + 5.50% = 13.00%

Post-Tax WACC:
  WACC = gearing × kd × (1 - tax) + (1 - gearing) × ke
  WACC = 0.70 × 7.50% × 0.75 + 0.30 × 13.00%
  WACC = 3.94% + 3.90% = 7.84%

NPV (risked):
  NPV = Σ(t=0 to n) FCFF(t) / (1 + WACC)^t
  
  ΔNPV_premium = NPV(with premium) - NPV(without premium)
                = approximately -(premium_in_bps × sensitivity)
```

---

## Checklist: To Return NPV to Baseline

- [ ] Understand the total NPV loss (e.g., -$44.5M)
- [ ] Isolate premium impact (use `include_premiums` flag)
- [ ] Isolate cashflow impact (via attribution)
- [ ] Decide: eliminate premium, cashflow, or both?
- [ ] For premium: Create intervention with `premium_reduction_*_bps`
- [ ] For cashflow: Adjust factors (generation_factor, capex_factor, etc.)
- [ ] Re-run model and check NPV recovery
- [ ] Verify premium impact in attribution dataframe

---

## Related Concepts

| Concept | Location | Purpose |
|---------|----------|---------|
| `RiskImpact` | risk_objects.py | Container for all risk effects (factors + premiums) |
| `combine_impacts()` | risk_objects.py | Combines multiple risks (multiplicative factors, additive premiums) |
| `cost_of_capital()` | project_cashflow.py | Converts premiums to cost rates |
| `CostOfCapital.wacc` | project_cashflow.py | Blends cost of debt & equity |
| `analyse()` | project_cashflow.py | Separates premium from cashflow via dual runs |
| `Intervention` | intervention_objects.py | Removes premiums or modifies factors |
| `include_premiums` | ProjectCashflowModel | Flag to toggle premium application |

