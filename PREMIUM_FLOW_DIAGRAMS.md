# Premium Calculation Flow Diagram

## 1. Data Flow: Risk → Premium → WACC → NPV

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        RISK DEFINITION (Input)                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Risk Object:                                                               │
│  ├─ risk_id: "country_risk"                                                │
│  ├─ premium_debt_bps: 150                                                  │
│  ├─ premium_equity_bps: 300                                                │
│  └─ strength: "High" (1.5x multiplier)                                     │
│                                                                              │
└────────────────────────────┬────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                   Risk.evaluate(ctx) → RiskImpact                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  RiskImpact:                                                                │
│  ├─ generation_factor: [1.0, ..., 0.90, 0.90, ...]  (5% loss in ops)      │
│  ├─ price_factor: [1.0, ..., 0.95, 0.95, ...]       (5% lower PPA price)  │
│  ├─ opex_factor: [1.0, ..., 1.05, 1.05, ...]        (5% higher opex)      │
│  ├─ capex_factor: [1.10, 1.10, 1.0, ...]            (10% capex overrun)   │
│  ├─ extra_cost: [$0, $0, $0.5m, ...]                (contingency costs)    │
│  ├─ premium_debt_bps: 150 × 1.5 = 225 ✓             (strength multiplied)  │
│  └─ premium_equity_bps: 300 × 1.5 = 450 ✓                                  │
│                                                                              │
└────────────────────────────┬────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│          combine_impacts([impact1, impact2, ...]) → Combined Impact         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Combining Multiple Risks:                                                 │
│  ├─ Factors: MULTIPLY across risks                                         │
│  │  combined_gen = 0.95 × 0.98 × 1.00 = 0.9310  (all risks compounded)   │
│  │                                                                           │
│  └─ Premiums: ADD across risks (except country=MAX)                        │
│     combined_premium_debt = 225 + 50 + 25 = 300 bps                        │
│     combined_premium_equity = 450 + 100 + 50 = 600 bps                     │
│                                                                              │
└────────────────────────────┬────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│          cost_of_capital(impact) → CostOfCapital                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Conversion (bps → decimal): scale = 1e-4                                  │
│  ┌────────────────────────────────────────────────────────────┐            │
│  │ premium_debt_bps = 300 bps                                 │            │
│  │ premium_equity_bps = 600 bps                               │            │
│  │                                                             │            │
│  │ risk_debt_premium = 300 × 1e-4 = 0.0300 (3.00%)           │            │
│  │ risk_equity_premium = 600 × 1e-4 = 0.0600 (6.00%)         │            │
│  └────────────────────────────────────────────────────────────┘            │
│                                                                              │
│  Build Cost of Capital:                                                    │
│  ├─ cost_of_debt = r_free + margin + risk_premium                          │
│  │                = 0.03 + 0.0175 + 0.03 = 0.0775 (7.75%)                 │
│  │                                                                           │
│  └─ cost_of_equity = r_free + equity_prem + risk_premium                   │
│                     = 0.03 + 0.045 + 0.06 = 0.135 (13.5%)                 │
│                                                                              │
│  Premiums have been successfully added to costs of capital! ✓               │
│                                                                              │
└────────────────────────────┬────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                   WACC Calculation (Post-Tax)                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  WACC = gearing × kd × (1 - tax_rate) + (1 - gearing) × ke                │
│       = 0.70 × 7.75% × (1 - 0.25) + 0.30 × 13.5%                          │
│       = 0.70 × 7.75% × 0.75 + 0.30 × 13.5%                                │
│       = 4.07% + 4.05%                                                      │
│       = 8.12% (RISKED WACC, includes premiums)                             │
│                                                                              │
│  Comparison:                                                                │
│  Baseline WACC (no premiums):  5.75%                                       │
│  Risked WACC (with premiums):  8.12%                                       │
│  Premium Impact:               +237 bps ⚠️ Significant cost increase!      │
│                                                                              │
└────────────────────────────┬────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                     NPV Calculation                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Baseline (No Risk):                                                       │
│  ────────────────────────────                                              │
│  NPV = -$100M/(1.0575)⁰ + $20M/(1.0575)¹ + $20M/(1.0575)² + ...           │
│      = -$100M + $18.92M + $17.90M + ...                                    │
│      = +$125M ✓                                                             │
│                                                                              │
│  Risked (With Premiums + Cashflow Impact):                                 │
│  ────────────────────────────────────────────                              │
│  FCFF_risked = [-$110M, $19M, $18.5M, ...]  (capex higher, revenues lower)│
│  NPV = -$110M/(1.0812)⁰ + $19M/(1.0812)¹ + $18.5M/(1.0812)² + ...        │
│      = -$110M + $17.57M + $15.86M + ...                                    │
│      = +$105M ✗ (Down $20M)                                                │
│                                                                              │
│  Impact Decomposition:                                                      │
│  • Premium impact (WACC rise):     -$8M   (more discounting)               │
│  • Cashflow impact (less FCFF):    -$12M  (lower generation/price/capex)   │
│  • Total impact:                   -$20M  ⚠️                               │
│                                                                              │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Attribution Analysis: Separating Premium from Cashflow Impact

```
┌──────────────────────────────────────────────────────────────────────────┐
│                   SCENARIO ANALYSIS (analyse function)                   │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  Three Parallel Runs for Each Risk:                                     │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │ Run A: BASELINE (No Risks)                                      │    │
│  ├─────────────────────────────────────────────────────────────────┤    │
│  │ include_premiums = True (irrelevant, no risks)                  │    │
│  │ FCFF = baseline unrisked cashflows                              │    │
│  │ WACC = 5.75% (risk-free + baselines)                            │    │
│  │ LCOE = $45/MWh                                                  │    │
│  │ NPV = $125M                                                     │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                           │                                              │
│                           ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │ Run B: WITH RISK (Full Impact)                                 │    │
│  ├─────────────────────────────────────────────────────────────────┤    │
│  │ include_premiums = True ✓                                       │    │
│  │ FCFF = risked cashflows (gen↓, rev↓, opex↑, capex↑)            │    │
│  │ WACC = 8.12% (includes 300bps debt + 600bps equity premiums)    │    │
│  │ LCOE = $52/MWh ← Reflects both effects                          │    │
│  │ NPV = $105M                                                     │    │
│  │                                                                 │    │
│  │ Delta: ΔLCOE_TOTAL = $52 - $45 = +$7/MWh                       │    │
│  │        ΔNPV_TOTAL = $105M - $125M = -$20M                      │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                           │                                              │
│                           ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │ Run C: CASHFLOW ONLY (No Premiums)                             │    │
│  ├─────────────────────────────────────────────────────────────────┤    │
│  │ include_premiums = False ✗                                      │    │
│  │ FCFF = risked cashflows (gen↓, rev↓, opex↑, capex↑)            │    │
│  │ WACC = 5.75% (baseline, premiums ignored!)                      │    │
│  │ LCOE = $49/MWh ← Only cashflow effect                           │    │
│  │ NPV = $113M                                                     │    │
│  │                                                                 │    │
│  │ Delta: ΔLCOE_CASHFLOW = $49 - $45 = +$4/MWh                    │    │
│  │        ΔNPV_CASHFLOW = $113M - $125M = -$12M                   │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                           │
│  ATTRIBUTION (Residual Calculation):                                     │
│  ───────────────────────────────────                                     │
│  ΔLCOE_PREMIUM = ΔLCOE_TOTAL - ΔLCOE_CASHFLOW                            │
│               = ($52 - $45) - ($49 - $45)                               │
│               = $7 - $4 = +$3/MWh ← Pure premium effect                 │
│               = 300bps debt × 0.70 + 600bps equity × 0.30                │
│                 × (1 - tax) + other terms... ✓                           │
│                                                                           │
│  ΔNPV_PREMIUM = ΔNPV_TOTAL - ΔNPV_CASHFLOW                              │
│              = (-$20M) - (-$12M) = -$8M ← Premium costs $8M in NPV      │
│                                                                           │
│  SUMMARY TABLE:                                                          │
│  ┌─────────────────┬─────────────┬──────────────┬──────────┐            │
│  │ Component       │ LCOE Impact │ NPV Impact   │ Premium  │            │
│  ├─────────────────┼─────────────┼──────────────┼──────────┤            │
│  │ Cashflow Only   │ +$4/MWh     │ -$12M        │ None     │            │
│  │ Premium Only    │ +$3/MWh     │ -$8M         │ 300bp/600bp
│  │ Total           │ +$7/MWh     │ -$20M        │ Combined │            │
│  └─────────────────┴─────────────┴──────────────┴──────────┘            │
│                                                                           │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 3. What Would Return NPV to Baseline?

```
┌─────────────────────────────────────────────────────────────────────────┐
│          RECOVERY PATHS: Returning to Baseline NPV of $125M             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Current State (Risked):                                                │
│  NPV = $105M (down $20M from baseline)                                  │
│                                                                          │
│  Recovery Options:                                                       │
│                                                                          │
│  ┌────────────────────────────────────────────────────────┐             │
│  │ Option 1: Eliminate Premium Only                       │             │
│  ├────────────────────────────────────────────────────────┤             │
│  │ Actions:                                               │             │
│  │ • premium_debt_bps = 0                                 │             │
│  │ • premium_equity_bps = 0                               │             │
│  │                                                        │             │
│  │ OR                                                     │             │
│  │                                                        │             │
│  │ • include_premiums = False                             │             │
│  │                                                        │             │
│  │ Result:                                                │             │
│  │ NPV recovers by $8M → $113M (not baseline!)            │             │
│  │ Still down $12M (cashflow impact remains)              │             │
│  │                                                        │             │
│  │ Mechanism: WACC drops from 8.12% → 5.75%              │             │
│  │            Discount factors get smaller (less harsh)   │             │
│  └────────────────────────────────────────────────────────┘             │
│                                                                          │
│  ┌────────────────────────────────────────────────────────┐             │
│  │ Option 2: Eliminate Cashflow Impact Only               │             │
│  ├────────────────────────────────────────────────────────┤             │
│  │ Actions:                                               │             │
│  │ • generation_factor = 1.0  (restore to no loss)        │             │
│  │ • price_factor = 1.0                                   │             │
│  │ • opex_factor = 1.0                                    │             │
│  │ • capex_factor = 1.0                                   │             │
│  │ • extra_cost = $0                                      │             │
│  │                                                        │             │
│  │ Mechanism: Performance guarantee, insurance, etc.      │             │
│  │                                                        │             │
│  │ Result:                                                │             │
│  │ NPV recovers by $12M → $117M (not baseline!)           │             │
│  │ Still down $8M (premium impact remains)                │             │
│  │                                                        │             │
│  │ Mechanism: FCFF returns to baseline, but WACC still    │             │
│  │            elevated at 8.12%                           │             │
│  └────────────────────────────────────────────────────────┘             │
│                                                                          │
│  ┌────────────────────────────────────────────────────────┐             │
│  │ Option 3: FULL MITIGATION (Both)                       │             │
│  ├────────────────────────────────────────────────────────┤             │
│  │ Actions:                                               │             │
│  │ • Eliminate all cashflow impacts (insurance)           │             │
│  │ • Transfer risk to insurer (premium removed)           │             │
│  │                                                        │             │
│  │ OR                                                     │             │
│  │                                                        │             │
│  │ • Capex guarantee → capex_factor = 1.0, premium↓ 50bps│             │
│  │ • Revenue protection → price_factor = 1.0, premium↓   │             │
│  │ • Operational insurance → gen_factor ~1.0, premium↓   │             │
│  │                                                        │             │
│  │ Result:                                                │             │
│  │ NPV recovers to $125M ✓ (BASELINE ACHIEVED!)           │             │
│  │                                                        │             │
│  │ Mechanism: Both FCFF and WACC return to baseline       │             │
│  └────────────────────────────────────────────────────────┘             │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Code Flow: include_premiums Flag

```
                        ProjectCashflowModel.__init__
                        
include_premiums = True (default)              include_premiums = False
        │                                               │
        ▼                                               ▼
   cost_of_capital()                              cost_of_capital()
        │                                               │
        ├─ scale = 1e-4                                 ├─ scale = 0.0
        │                                               │
        ▼                                               ▼
   risk_debt_premium                            risk_debt_premium
   = 300 × 1e-4                                 = 300 × 0.0
   = 0.03 (3%)  ✓                              = 0.0 (0%)  ✗
        │                                               │
        ▼                                               ▼
   cost_of_debt = 3% + 1.75% + 3% = 7.75%     cost_of_debt = 3% + 1.75% + 0% = 4.75%
   cost_of_equity = 3% + 4.5% + 6% = 13.5%    cost_of_equity = 3% + 4.5% + 0% = 7.5%
        │                                               │
        ▼                                               ▼
   WACC = 8.12%  (RISKED)                      WACC = 5.75%  (BASELINE)
        │                                               │
        ▼                                               ▼
   NPV = Σ[FCFF/(1.0812)^t]                    NPV = Σ[FCFF/(1.0575)^t]
   = $105M (lower)                              = $113M (higher)
        │                                               │
        └────────────────┬─────────────────────────────┘
                         ▼
                   DELTA = $8M
                   
           This delta is the premium impact,
           isolated from cashflow impact.
```

---

## 5. Premium Scaling with Strength Multiplier

```
Risk Definition:
  premium_debt_bps = 150 (base)
  strength = "Low" → multiplier = 0.5
  
Evaluation:
  impact.premium_debt_bps = 150 × 0.5 = 75 bps
  
Increasing Strength:
  strength = "Medium" → multiplier = 1.0 → 150 bps
  strength = "High" → multiplier = 1.5 → 225 bps ⚠️ (worst case in this analysis)
  
Interpretation:
  Higher strength = Higher multiplier = Larger premium adjustment
  = Larger increase in WACC
  = Larger reduction in NPV
```

---

## Summary: Premium Mechanism

| Step | Input | Calculation | Output | Impact on NPV |
|------|-------|-------------|--------|---------------|
| 1 | premium_debt_bps | × strength multiplier | scaled premium | ↑ |
| 2 | scaled premium | × 1e-4 (if include_premiums) | decimal rate | ↑ |
| 3 | decimal rate | Add to cost_of_debt | kd | ↑ |
| 4 | kd & cost_of_equity | Blend via WACC formula | WACC | ↑ |
| 5 | WACC | Discount FCFF | NPV | ↓ |

**Result**: Premium → Higher WACC → More Discounting → Lower NPV

**To Return to Baseline**: Remove premiums OR eliminate cashflow impact OR both.
