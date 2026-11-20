# Premium Calculation Documentation Index

This index helps you navigate the comprehensive documentation about how cost-of-capital premiums are calculated and applied in the project finance risk model.

---

## 📖 Documentation Files

### 1. **PREMIUM_ONE_PAGER.md** ⭐ START HERE
**Best for**: Quick understanding, quick reference, on-the-job lookup
- One-page core mechanism
- Three key facts
- Code quick reference
- Common Q&A
- **Read time**: 5 minutes

### 2. **PREMIUM_SUMMARY.txt**
**Best for**: Executive summary, printed reference
- Detailed answer to all three questions
- Technical details section
- Code locations
- Worked example
- **Read time**: 10 minutes

### 3. **PREMIUM_CALCULATION_EXPLANATION.md**
**Best for**: Deep understanding, methodology
- Complete overview of premium architecture
- Step-by-step flow through the system
- Numerical examples with actual values
- Equations and formulas
- Special cases (country premium, caching)
- **Read time**: 15-20 minutes

### 4. **PREMIUM_FLOW_DIAGRAMS.md**
**Best for**: Visual learners, understanding data flow
- ASCII flow diagrams showing data movement
- 5 detailed scenarios with annotations
- Recovery paths illustration
- Code flow with include_premiums flag
- Premium scaling diagram
- **Read time**: 15 minutes

### 5. **PREMIUM_CODE_EXAMPLES.md**
**Best for**: Developers, implementation details
- 10 concrete code examples
- Real calculations with numbers
- Complete working code snippets
- Input/output examples
- Edge cases and variations
- **Read time**: 20-30 minutes

---

## 🎯 Quick Navigation by Question

### Q: "How are premiums calculated?"
1. Start: [PREMIUM_ONE_PAGER.md](PREMIUM_ONE_PAGER.md#the-core-mechanism) - Core mechanism diagram
2. Deepen: [PREMIUM_CALCULATION_EXPLANATION.md](PREMIUM_CALCULATION_EXPLANATION.md#premium-architecture)
3. Code: [PREMIUM_CODE_EXAMPLES.md](PREMIUM_CODE_EXAMPLES.md#example-2-how-premiums-get-applied)

### Q: "Is premium based on NPV change?"
1. Answer: [PREMIUM_SUMMARY.txt](PREMIUM_SUMMARY.txt#question-is-this-based-on-how-the-risked-npv-changes)
2. Visual: [PREMIUM_FLOW_DIAGRAMS.md](PREMIUM_FLOW_DIAGRAMS.md#2-attribution-analysis-separating-premium-from-cashflow-impact)
3. Example: [PREMIUM_CODE_EXAMPLES.md](PREMIUM_CODE_EXAMPLES.md#example-7-separating-premium-from-cashflow-impact)

### Q: "What modifies this to return to baseline NPV?"
1. Options: [PREMIUM_SUMMARY.txt](PREMIUM_SUMMARY.txt#question-what-is-needed-to-modify-this-to-arrive-at-the-baseline-npv)
2. Paths: [PREMIUM_FLOW_DIAGRAMS.md](PREMIUM_FLOW_DIAGRAMS.md#3-what-would-return-npv-to-baseline)
3. Code: [PREMIUM_CODE_EXAMPLES.md](PREMIUM_CODE_EXAMPLES.md#example-9-what-values-return-npv-to-baseline)

### Q: "What's the difference between cashflow and premium impact?"
1. Diagram: [PREMIUM_FLOW_DIAGRAMS.md](PREMIUM_FLOW_DIAGRAMS.md#2-attribution-analysis-separating-premium-from-cashflow-impact)
2. Example: [PREMIUM_CODE_EXAMPLES.md](PREMIUM_CODE_EXAMPLES.md#example-7-separating-premium-from-cashflow-impact)
3. Full Details: [PREMIUM_CALCULATION_EXPLANATION.md](PREMIUM_CALCULATION_EXPLANATION.md#decomposing-risk-impact-on-npv)

### Q: "How do I remove premiums via code?"
1. Quick: [PREMIUM_ONE_PAGER.md](PREMIUM_ONE_PAGER.md#code-quick-reference)
2. Example: [PREMIUM_CODE_EXAMPLES.md](PREMIUM_CODE_EXAMPLES.md#example-8-toggling-premiums-with-include_premiums-flag)
3. Intervention: [PREMIUM_CODE_EXAMPLES.md](PREMIUM_CODE_EXAMPLES.md#example-10-real-world-intervention-example)

---

## 📊 Key Concepts Map

```
premium_debt_bps (input)
    ↓
Risk.evaluate() → multiply by strength
    ↓
RiskImpact.premium_debt_bps (scaled)
    ↓
combine_impacts() → add multiple risks
    ↓
Combined premium_debt_bps
    ↓
CostOfCapital.cost_of_debt ← add to baseline
    ↓
CostOfCapital.wacc ← blend with equity
    ↓
NPV calculation ← use as discount rate
    ↓
↑ WACC → ↓ NPV (more discounting)

---

Premium Impact Isolation:
    include_premiums=True  → Full NPV (with premium effect)
    include_premiums=False → Cashflow-only NPV (premium=0)
    Delta → Pure premium impact on NPV

---

Recovery:
    Intervention → premium_reduction_* → Premiums → 0 → NPV ↑
```

---

## 🔍 Topic Index

### Premium Basics
- Definition: [PREMIUM_ONE_PAGER.md#three-key-facts](PREMIUM_ONE_PAGER.md#three-key-facts)
- Architecture: [PREMIUM_CALCULATION_EXPLANATION.md#premium-architecture](PREMIUM_CALCULATION_EXPLANATION.md#premium-architecture)
- Flow diagram: [PREMIUM_FLOW_DIAGRAMS.md#1-data-flow-risk--premium--wacc--npv](PREMIUM_FLOW_DIAGRAMS.md#1-data-flow-risk--premium--wacc--npv)

### Risk Evaluation
- Strength scaling: [PREMIUM_FLOW_DIAGRAMS.md#5-premium-scaling-with-strength-multiplier](PREMIUM_FLOW_DIAGRAMS.md#5-premium-scaling-with-strength-multiplier)
- Multiple risks: [PREMIUM_CALCULATION_EXPLANATION.md#key-equations](PREMIUM_CALCULATION_EXPLANATION.md#key-equations)
- Code: [PREMIUM_CODE_EXAMPLES.md#example-2-how-premiums-get-applied](PREMIUM_CODE_EXAMPLES.md#example-2-how-premiums-get-applied)

### Conversion & Application
- bps to decimal: [PREMIUM_ONE_PAGER.md#conversion-reminder](PREMIUM_ONE_PAGER.md#conversion-reminder)
- Cost of capital: [PREMIUM_CODE_EXAMPLES.md#example-4-building-cost-of-debt-and-cost-of-equity](PREMIUM_CODE_EXAMPLES.md#example-4-building-cost-of-debt-and-cost-of-equity)
- WACC impact: [PREMIUM_CODE_EXAMPLES.md#example-5-wacc-calculation](PREMIUM_CODE_EXAMPLES.md#example-5-wacc-calculation)

### NPV Impact
- NPV reduction: [PREMIUM_CODE_EXAMPLES.md#example-6-npv-impact-of-wacc-change](PREMIUM_CODE_EXAMPLES.md#example-6-npv-impact-of-wacc-change)
- Full calculation: [PREMIUM_CALCULATION_EXPLANATION.md#numerical-example-how-premium-reduces-npv](PREMIUM_CALCULATION_EXPLANATION.md#numerical-example-how-premium-reduces-npv)
- Independence: [PREMIUM_SUMMARY.txt#question-is-this-based-on-how-the-risked-npv-changes](PREMIUM_SUMMARY.txt#question-is-this-based-on-how-the-risked-npv-changes)

### Separation & Attribution
- Both impacts: [PREMIUM_FLOW_DIAGRAMS.md#2-attribution-analysis-separating-premium-from-cashflow-impact](PREMIUM_FLOW_DIAGRAMS.md#2-attribution-analysis-separating-premium-from-cashflow-impact)
- Code example: [PREMIUM_CODE_EXAMPLES.md#example-7-separating-premium-from-cashflow-impact](PREMIUM_CODE_EXAMPLES.md#example-7-separating-premium-from-cashflow-impact)
- Explanation: [PREMIUM_CALCULATION_EXPLANATION.md#decomposing-risk-impact-on-npv](PREMIUM_CALCULATION_EXPLANATION.md#decomposing-risk-impact-on-npv)

### Recovery & Mitigation
- Three paths: [PREMIUM_SUMMARY.txt#path-1-eliminate-the-premium](PREMIUM_SUMMARY.txt)
- Detailed recovery: [PREMIUM_FLOW_DIAGRAMS.md#3-what-would-return-npv-to-baseline](PREMIUM_FLOW_DIAGRAMS.md#3-what-would-return-npv-to-baseline)
- Code examples: [PREMIUM_CODE_EXAMPLES.md#example-9-what-values-return-npv-to-baseline](PREMIUM_CODE_EXAMPLES.md#example-9-what-values-return-npv-to-baseline)

### include_premiums Flag
- Explanation: [PREMIUM_ONE_PAGER.md#the-include_premiums-flag-explained](PREMIUM_ONE_PAGER.md#the-include_premiums-flag-explained)
- Flow diagram: [PREMIUM_FLOW_DIAGRAMS.md#4-premium-mechanism-include_premiums-flag](PREMIUM_FLOW_DIAGRAMS.md#4-premium-mechanism-include_premiums-flag)
- Code example: [PREMIUM_CODE_EXAMPLES.md#example-8-toggling-premiums-with-include_premiums-flag](PREMIUM_CODE_EXAMPLES.md#example-8-toggling-premiums-with-include_premiums-flag)

### Interventions
- Intervention example: [PREMIUM_CODE_EXAMPLES.md#example-10-real-world-intervention-example](PREMIUM_CODE_EXAMPLES.md#example-10-real-world-intervention-example)
- Code locations: [PREMIUM_SUMMARY.txt#code-locations](PREMIUM_SUMMARY.txt#code-locations)

---

## 🛠️ Implementation Guide

### For Developers
1. Read: [PREMIUM_CODE_EXAMPLES.md](PREMIUM_CODE_EXAMPLES.md) (all 10 examples)
2. Reference: [PREMIUM_ONE_PAGER.md#code-quick-reference](PREMIUM_ONE_PAGER.md#code-quick-reference)
3. Locations: [PREMIUM_SUMMARY.txt#code-locations](PREMIUM_SUMMARY.txt#code-locations)

### For Modelers
1. Read: [PREMIUM_CALCULATION_EXPLANATION.md](PREMIUM_CALCULATION_EXPLANATION.md)
2. Examples: [PREMIUM_CODE_EXAMPLES.md](PREMIUM_CODE_EXAMPLES.md) (worked examples 1, 6, 7)
3. Recovery: [PREMIUM_SUMMARY.txt#path-1-eliminate-the-premium](PREMIUM_SUMMARY.txt#path-1-eliminate-the-premium)

### For Analysts
1. Summary: [PREMIUM_SUMMARY.txt](PREMIUM_SUMMARY.txt)
2. Visual: [PREMIUM_FLOW_DIAGRAMS.md](PREMIUM_FLOW_DIAGRAMS.md#2-attribution-analysis-separating-premium-from-cashflow-impact)
3. Example: [PREMIUM_CODE_EXAMPLES.md#example-7-separating-premium-from-cashflow-impact](PREMIUM_CODE_EXAMPLES.md#example-7-separating-premium-from-cashflow-impact)

### For Project Managers
1. Quick: [PREMIUM_ONE_PAGER.md](PREMIUM_ONE_PAGER.md)
2. Recovery: [PREMIUM_FLOW_DIAGRAMS.md#3-what-would-return-npv-to-baseline](PREMIUM_FLOW_DIAGRAMS.md#3-what-would-return-npv-to-baseline)
3. Example: [PREMIUM_CODE_EXAMPLES.md#example-9-what-values-return-npv-to-baseline](PREMIUM_CODE_EXAMPLES.md#example-9-what-values-return-npv-to-baseline)

---

## 📋 Checklist: Understanding Premiums

- [ ] I understand that premium is a fixed market input (not derived from NPV change)
- [ ] I know the three-step mechanism: Risk input → scale by strength → convert to decimal → add to cost of capital
- [ ] I can explain how premium increases WACC
- [ ] I understand that higher WACC reduces NPV (more discounting)
- [ ] I know the difference between cashflow impact and premium impact
- [ ] I can run the model with `include_premiums=False` to isolate cashflow effects
- [ ] I understand that total NPV loss = cashflow loss + premium loss
- [ ] I know the three paths to recover NPV: eliminate premium, eliminate cashflow, or both
- [ ] I can use interventions to remove premiums via `premium_reduction_*_bps`
- [ ] I understand the `analyse()` function separates the two impacts

---

## 🔗 Code References

| Component | File | Lines |
|-----------|------|-------|
| RiskImpact definition | risk_objects.py | 100-130 |
| Premium scaling | risk_objects.py | 240-242 |
| combine_impacts | risk_objects.py | 139-146 |
| cost_of_capital method | project_cashflow.py | 278-297 |
| CostOfCapital properties | project_cashflow.py | 90-105 |
| WACC calculation | project_cashflow.py | 123-131 |
| analyse function | project_cashflow.py | 486-550 |
| include_premiums flag | project_cashflow.py | 290 |
| Intervention object | intervention_objects.py | 1-150 |

---

## 📞 Quick Answers

**Q: What is premium?**
A: Additive basis points to cost of capital reflecting market's risk view. See [PREMIUM_ONE_PAGER.md#three-key-facts](PREMIUM_ONE_PAGER.md#three-key-facts)

**Q: How is it calculated?**
A: Input × Strength multiplier = Scaled premium. See [PREMIUM_FLOW_DIAGRAMS.md#1-data-flow](PREMIUM_FLOW_DIAGRAMS.md#1-data-flow-risk--premium--wacc--npv)

**Q: Does it depend on NPV?**
A: No, it's independent. Both premium and cashflow reduce NPV separately. See [PREMIUM_SUMMARY.txt#question-is-this-based](PREMIUM_SUMMARY.txt#question-is-this-based-on-how-the-risked-npv-changes)

**Q: How to return to baseline NPV?**
A: Eliminate premium via intervention OR eliminate cashflow OR both. See [PREMIUM_SUMMARY.txt#path-1](PREMIUM_SUMMARY.txt#path-1-eliminate-the-premium)

**Q: What is include_premiums?**
A: Flag to toggle premium application. `True` = apply premiums, `False` = baseline WACC. See [PREMIUM_ONE_PAGER.md#the-include_premiums-flag-explained](PREMIUM_ONE_PAGER.md#the-include_premiums-flag-explained)

---

## 📚 Reading Paths by Time Available

**5 minutes**: [PREMIUM_ONE_PAGER.md](PREMIUM_ONE_PAGER.md)

**15 minutes**: [PREMIUM_SUMMARY.txt](PREMIUM_SUMMARY.txt) + [PREMIUM_ONE_PAGER.md](PREMIUM_ONE_PAGER.md)

**30 minutes**: [PREMIUM_CALCULATION_EXPLANATION.md](PREMIUM_CALCULATION_EXPLANATION.md) + [PREMIUM_FLOW_DIAGRAMS.md](PREMIUM_FLOW_DIAGRAMS.md)

**60 minutes**: All five documents + code examples

---

Last updated: 2026-11-20
Version: 1.0
