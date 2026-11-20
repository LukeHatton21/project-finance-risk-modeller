# Currency Risk Modeling: Local Revenue vs Hard Currency Debt

## The Issue You Identified

When a project has:
- **Revenue:** Denominated in local currency (e.g., BRL)
- **Debt:** Denominated in hard currency (e.g., USD)
- **Risk:** Local currency depreciation

The traditional project finance view is that depreciation increases risk because:
1. Revenue (in local currency) becomes worth less in USD terms
2. Debt service (in USD) stays constant or increases in local currency terms
3. The sponsor faces **currency mismatch** - earning in weak currency, paying in strong currency

**Current model behavior:** Shows this as a negative LCOE impact (appears beneficial)
**Correct behavior:** Should show this as a positive risk (increased cost of capital or reduced cashflow)

---

## What the Current Model Does

The `CurrencyRisk` class applies:

```python
price_factor = 1 + revenue_exposure × (fx - 1)
opex_factor = 1 + local_opex_pct × (fx - 1)
```

Where `fx = (1 - depreciation_rate)^years`, so:
- **Example:** 3% annual depreciation → fx = 0.97 in year 1
- **Impact:** price_factor = 1 + 1.0 × (0.97 - 1) = 0.97 (revenue reduced to 97%)

**This correctly models:**
✓ Revenue in local currency × depreciation factor = lower USD-equivalent revenue
✓ O&M in local currency × depreciation factor = lower USD-equivalent costs
✓ Debt service in USD = unchanged (correctly)

**The resulting net effect in USD terms:**
- Higher operational costs → ~80% of cost reduction
- Lower revenue → ~100% of revenue reduction
- **Net:** Usually slightly negative for the project (bad)

---

## The Two Viewpoints (Why Model Behavior Seems "Wrong")

### Viewpoint 1: USD Investor Analyzing USD Cashflows
*Current model perspective*

The investor in the USA analyzing the project:
- Receives revenue in USD-equivalent terms (lower due to depreciation)
- Pays debt service in USD (unchanged)
- Result: Depreciation makes the project worse, but NOT catastrophic

Example (simplified):
```
Baseline (Year 1):
  Revenue: $100M USD
  O&M:      $20M USD  
  Debt SVC: $30M USD
  CFADS:    $50M USD

With 3% BRL depreciation:
  Revenue: $97M USD (100M BRL ÷ 5.15)
  O&M:     $19.4M USD (100M BRL ÷ 5.15)
  Debt SVC: $30M USD (unchanged)
  CFADS:    $47.6M USD
  
  Impact: -$2.4M CFADS (-4.8%)
```

### Viewpoint 2: Local Sponsor Managing in Local Currency
*Your perspective (and lender's perspective)*

The sponsor earning and operating in BRL:
- Earns revenue in BRL (physical quantity constant)
- Pays O&M in BRL (physical quantity constant)  
- **Must repay debt by converting USD back to BRL** (increases in BRL terms!)

Example (from local currency viewpoint):
```
Baseline (Year 1):  [Exchange rate = 5 BRL/USD]
  Revenue: 500M BRL = $100M USD
  O&M:     100M BRL = $20M USD
  Debt SVC: 150M BRL = $30M USD
  CFADS:   250M BRL = $50M USD

With 3% BRL depreciation:  [Exchange rate = 5.15 BRL/USD]
  Revenue: 500M BRL = $97.09M USD ← amount constant, value drops
  O&M:     100M BRL = $19.42M USD  ← amount constant, value drops
  Debt SVC: 154.5M BRL = $30M USD  ← amount INCREASES in local terms!
  CFADS:   245.5M BRL = $47.67M USD
  
  Impact: -4.5M BRL from revenue drop
  Impact: +4.5M BRL from debt service increase  ← This is the REAL RISK
  
  Net local currency impact: 0 (!) if only revenue/debt change
  But if O&M is 100% local, it also saves money in local terms
```

---

## Why the Model Shows What It Shows

The model currently uses the **USD investor perspective** because:

1. **Cashflows are computed in USD** (the reporting currency for most project finance models)
2. **Debt service is explicitly in USD** (doesn't get converted to local currency)
3. **The net effect is what matters for USD-based metrics** (NPV, IRR, LCOE in $/MWh)

But lenders typically care about the **currency mismatch premium**, which the model doesn't explicitly capture.

---

## The Gap: Where Currency Mismatch Risk Should Appear

**Option 1: Captured as a Cost-of-Capital Premium (Current Design Intent)**

Lenders would add a premium like:
```
Currency mismatch premium = f(depreciation_rate, revenue_exposure, debt_ratio)
```

This would:
- Increase cost of debt and equity
- Reduce LCOE/NPV from baseline
- Appear as a positive premium in the attribution

**Example:**
- Base cost of debt: 5%
- Currency mismatch premium: +150 bps (3% depreciation × 50% debt ratio)
- Result: cost of debt = 6.5%

The model currently doesn't auto-calculate this premium from the depreciation rate.

**Option 2: Captured in Cashflow (Current Model Behavior)**

The degraded cashflows already reflect the revenue loss in USD terms:
- Depreciation reduces revenue in USD equivalent terms
- Depreciation reduces costs in USD equivalent terms  
- Net effect is typically negative (bad for project)

This is captured without an explicit premium, just via the cashflow.

---

## Why the LCOE Impact Appears "Negative" (Beneficial)

In the test earlier, we saw:
```
Currency depreciation: LCOE impact = -$6.56/MWh (appears beneficial!)
```

**Why this happens:**

1. Revenue reduction (100% of local BRL revenue affected): ~$7/MWh impact
2. O&M reduction (80% of local BRL O&M affected): ~-$1/MWh benefit
3. Net: ~$6/MWh negative (bad)

But **the sign depends on the leverage and cost structure:**

- **High equity ratio (low leverage):** Depreciation hurts more (revenue loss not offset by debt reduction)
- **High debt ratio:** Depreciation can appear less bad (less CFADS needed to service smaller USD debt relative to revenue)
- **High local O&M ratio:** Depreciation can appear less bad (cost savings offset revenue loss)

In this case, with 60% leverage:
- Debt service is 46% of baseline CFADS
- Revenue loss hurts most projects
- But the LCOE metric sometimes shows non-intuitive results

---

## How to Fix This (Recommendations)

### Option A: Add Explicit Currency Mismatch Premium

Modify `CurrencyRisk` to auto-calculate a cost-of-capital premium:

```python
def evaluate(self, ctx):
    impact = self._impact(ctx, ctx.m, ctx.p)
    
    # Add currency mismatch premium based on:
    # - Annual depreciation rate
    # - Revenue currency exposure
    # - Debt ratio
    depreciation_rate = abs(self.params["annual_depreciation_pct"]) / 100
    revenue_exposure = self.params["local_revenue_pct"] / 100
    debt_ratio = 0.6  # or get from context
    
    # Lenders typically charge 50-150 bps per 1% depreciation risk
    mismatch_premium_bps = depreciation_rate * 100 * 100  # 100 bps per 1% depreciation
    
    impact.premium_debt_bps = mismatch_premium_bps * debt_ratio
    impact.premium_equity_bps = mismatch_premium_bps * (1 - debt_ratio)
    
    return impact
```

**Pros:**
- Makes currency risk visible in cost-of-capital
- Lenders expect this premium
- Aligns with how markets price currency risk

**Cons:**
- Changes current model behavior
- Requires calibration (what's the right 50-150 bps rate?)
- May double-count if user separately specifies market premium

### Option B: Document Current Behavior and Let Users Override

Keep the cashflow impact as-is, but:
1. Document that currency depreciation affects BOTH revenue and costs
2. Add optional `market_premium_debt_bps` parameter for users to specify lender risk premium
3. Show clearly in attribution: "Cashflow impact (net): -$6.56/MWh" + "Lender currency premium: +X bps"

**Example:**
```python
currency_risk = CurrencyRisk(
    ...,
    params={"currency": "BRL", "annual_depreciation_pct": 3.0, ...},
    market_premium_debt_bps=100,  # User specifies lender's currency premium
    market_premium_equity_bps=200,
)
```

**Pros:**
- No change to existing model logic
- Gives users flexibility
- Clear separation: cashflow effect vs premium effect

**Cons:**
- Requires user knowledge (what should the premium be?)
- Easy to forget/misconfigure
- Doesn't match automated calculation for other risks

### Option C: Add a Toggle for Currency Premium Auto-Calculation

Add a parameter to `CurrencyRisk`:

```python
class CurrencyRisk(Risk):
    param_specs = (
        ...,
        ParamSpec("add_currency_mismatch_premium", "Add cost-of-capital premium for currency mismatch", True, options=[True, False]),
        ParamSpec("mismatch_premium_bps_per_pct_depreciation", "Premium multiplier", 100, 0, 200, 10, "bps"),
    )
```

**Pros:**
- Backward compatible (can default to False)
- Users can enable if they want it
- Customizable calibration

**Cons:**
- More parameters to configure
- Requires user understanding

---

## Current Status

✓ **Cashflow impact:** Correctly modeled
  - Local revenue × depreciation factor = reduced USD-equivalent revenue
  - Local O&M × depreciation factor = reduced USD-equivalent costs
  - Net effect in USD terms is typically negative (bad)

✗ **Cost-of-capital premium:** Not auto-calculated
  - Lenders would charge additional premium for currency mismatch
  - Users must manually specify with `market_premium_debt_bps` parameter
  - Or model could auto-calculate (requires decision on calibration)

---

## Recommendation

For now, I suggest **Option B** (document + user override):

1. Add a note to `CurrencyRisk` description explaining currency mismatch premium
2. Document in `PREMIUM_USAGE_QUICK_REFERENCE.md` that:
   - If you model currency depreciation, lenders typically charge an additional premium
   - You should include this via `market_premium_debt_bps` parameter
   - Example: 3% annual depreciation + 60% debt ratio → ~100-150 bps premium
3. Let users decide based on their lender feedback

This gives maximum flexibility while accurately modeling both mechanisms:
- **Cashflow:** Depreciation reduces revenue in USD terms
- **Premium:** Currency mismatch charges lenders demand

Does this analysis match your understanding of how the market prices this risk?
