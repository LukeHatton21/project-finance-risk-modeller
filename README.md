# Renewables Project Finance Risk Modeller

Streamlit app for exploring how discrete (event) and systemic risks affect the cashflow, LCOE, NPV, IRR, DSCR
and cost of capital of a renewable energy project.

- `project_cashflow.py` – `ProjectInputs` and `ProjectCashflowModel` (annual project-finance model) plus `analyse()` for
  baseline / risked / per-risk attribution runs.
- `risk_objects.py` – `Risk` base class and risk categories. Add a category by subclassing `Risk`, declaring
  `param_specs` and decorating with `@register_risk`.
- `data/default_risks.csv` – default risk library (strength, premiums and `key=value;...` parameters). Rows with
  `risk_type=generic` create new risks without code.

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
