"""Streamlit UI for exploring how project risks affect a renewables project's cashflow and financing."""

from __future__ import annotations

import io
import math
import json
import hashlib
from html import escape
from textwrap import wrap
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots
from intervention_objects import INTERVENTION_REGISTRY, Intervention

from project_cashflow import TECHNOLOGY_PRESETS, ProjectInputs, ScenarioAnalysis, SimulationAnalysis, analyse, simulate
from risk_objects import (
    DISCRETE,
    STRENGTH_LEVELS,
    SYSTEMIC,
    GenericRisk,
    ParamSpec,
    Risk,
    RiskConfig,
    load_risk_library,
    rating_spreads,
    RISK_REGISTRY,
    calibrate_fx,
)

st.set_page_config(page_title="Renewables Risk & Project Finance Modeller", page_icon=":material/bolt:", layout="wide")

C = {
    "revenue": "#2a9d8f",
    "loss": "#d62828",
    "opex": "#8d99ae",
    "capex": "#264653",
    "tax": "#bfc5d2",
    "debt": "#f4a261",
    "equity": "#457b9d",
    "line": "#1d3557",
    "base": "#6c757d",
}
LOSS_FILL = "rgba(214, 40, 40, 0.18)"
HATCH = dict(shape="/", fgcolor=C["loss"], bgcolor="rgba(214,40,40,0.25)", solidity=0.45)
GREEN_HATCH = dict(shape="/", fgcolor="#16834b", bgcolor="rgba(22,131,75,0.18)", solidity=0.45)
KIND_ICON = {DISCRETE: ":material/flash_on:", SYSTEMIC: ":material/timeline:"}


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #
def _ok(x: float) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def fmt_money(x: float) -> str:
    return f"${x:,.1f}m" if _ok(x) else "n/a"


def fmt_pct(x: float, dp: int = 2) -> str:
    return f"{x * 100:.{dp}f}%" if _ok(x) else "n/a"


def delta_str(new: float, old: float, kind: str) -> str | None:
    if not (_ok(new) and _ok(old)) or abs(new - old) < 1e-9:
        return None
    d = new - old
    match kind:
        case "money":
            return f"{d:+,.1f} $m"
        case "pct":
            return f"{d * 1e4:+,.0f} bps"
        case "pp":
            return f"{d * 100:+.1f}pp"
        case "ratio":
            return f"{d:+.2f}x"
        case "lcoe":
            return f"{d:+.2f} $/MWh"
        case "years":
            return f"{d:+.0f} yrs"
    return f"{d:+.2f}"


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
@st.cache_data
def risk_library() -> list[RiskConfig]:
    return load_risk_library()


@st.cache_data(max_entries=12, show_spinner="Simulating project risks...")
def cached_simulation(inputs: ProjectInputs, settings: list[dict], intervention_settings: list[dict], spreads: dict, draws: int, level: int,
                      seed: int, severity_cv: float) -> SimulationAnalysis:
    risks = [RISK_REGISTRY[setting["risk_type"]](**setting["kwargs"]) for setting in settings]
    interventions = [INTERVENTION_REGISTRY[setting["risk_type"]](**setting["kwargs"]) for setting in intervention_settings]
    return simulate(inputs, risks, draws=draws, exceedance_pct=level, seed=seed, severity_cv=severity_cv, interventions=interventions)


def init_state(library: list[RiskConfig]) -> None:
    ss = st.session_state
    ss.setdefault("custom_risks", [])
    ss.setdefault("custom_counter", 0)
    ss.setdefault("selected_risks", [c.risk_id for c in library if c.enabled])


def _clamp_state(key: str, lo: float, hi: float) -> None:
    """Reset a stored widget value whose valid range depends on other inputs."""
    if key in st.session_state:
        value = st.session_state[key]
        if value < lo or value > hi:
            del st.session_state[key]


def add_custom_risk() -> None:
    ss = st.session_state
    ss.custom_counter += 1
    risk_id = f"custom_{ss.custom_counter}"
    params = {s.key: ss[f"new__{s.key}"] for s in GenericRisk.param_specs if f"new__{s.key}" in ss}
    ss.custom_risks.append(
        RiskConfig(
            risk_id=risk_id,
            risk_type=GenericRisk.risk_type,
            name=ss["new__name"].strip() or f"Custom risk {ss.custom_counter}",
            category=ss["new__category"].strip() or "Custom",
            enabled=True,
            strength=ss["new__strength"],
            premium_debt_bps=ss["new__premium_debt_bps"],
            premium_equity_bps=ss["new__premium_equity_bps"],
            params=params,
            description=ss["new__description"].strip() or "User-defined risk.",
            custom=True,
        )
    )
    ss.selected_risks = [*ss.selected_risks, risk_id]


def remove_custom_risk(risk_id: str) -> None:
    ss = st.session_state
    ss.custom_risks = [c for c in ss.custom_risks if c.risk_id != risk_id]
    ss.selected_risks = [r for r in ss.selected_risks if r != risk_id]


# --------------------------------------------------------------------------- #
# Sidebar: project inputs
# --------------------------------------------------------------------------- #
def project_inputs_sidebar() -> ProjectInputs:
    with st.sidebar:
        st.header("Project inputs")
        tech = st.selectbox(
            "Technology preset", list(TECHNOLOGY_PRESETS),
            help="Loads typical cost and performance defaults. All values can be edited below.",
        )
        d = ProjectInputs.from_preset(tech)

        def k(name: str) -> str:
            return f"{tech}__{name}"

        with st.expander("Technical", icon=":material/settings:", expanded=True):
            capacity = st.number_input("Capacity (MW)", 1.0, 5000.0, d.capacity_mw, 10.0, key=k("cap"))
            cf = st.slider("Capacity factor (%)", 5.0, 70.0, float(d.capacity_factor_pct), 0.5, key=k("cf"))
            lifetime = st.slider("Operating lifetime (years)", 10, 40, int(d.lifetime_years), key=k("life"))
            construction = st.slider("Construction period (years)", 1, 5, int(d.construction_years), key=k("build"))
            degradation = st.slider("Degradation (%/yr)", 0.0, 2.0, float(d.degradation_pct), 0.05, key=k("deg"))
            start_year = st.number_input("Construction start year", 2000, 2060, d.start_year, 1, key=k("start"))

        with st.expander("Costs", icon=":material/payments:"):
            capex = st.number_input("Capex ($/kW)", 100.0, 10000.0, float(d.capex_per_kw), 50.0, key=k("capex"))
            fixed_opex = st.number_input("Fixed O&M ($/kW-yr)", 0.0, 300.0, float(d.fixed_opex_per_kw_yr), 1.0, key=k("fom"))
            var_opex = st.number_input("Variable O&M ($/MWh)", 0.0, 50.0, float(d.variable_opex_per_mwh), 0.5, key=k("vom"))
            opex_esc = st.slider("O&M escalation (%/yr)", 0.0, 6.0, d.opex_escalation_pct, 0.1, key=k("oesc"))

        with st.expander("Revenue", icon=":material/sell:"):
            ppa_price = st.number_input("PPA price ($/MWh)", 0.0, 500.0, d.ppa_price, 1.0, key=k("ppa"))
            _clamp_state(k("tenor"), 0, lifetime)
            ppa_tenor = st.slider("PPA tenor (years)", 0, lifetime, min(d.ppa_tenor, lifetime), key=k("tenor"))
            merchant = st.number_input("Merchant price after PPA ($/MWh)", 0.0, 500.0, d.merchant_price, 1.0, key=k("merch"))
            price_esc = st.slider("Price escalation (%/yr)", 0.0, 6.0, d.price_escalation_pct, 0.1, key=k("pesc"))

        with st.expander("Financing & cost of capital", icon=":material/account_balance:", expanded=True):
            rf = st.slider(
                "Risk-free rate (%)", 0.0, 12.0, d.risk_free_rate_pct, 0.1, key=k("rf"),
                help="Baseline risk-free rate (e.g. long-dated government bond yield). Risk premiums are added on top.",
            )
            c1, c2 = st.columns(2)
            margin = c1.number_input("Base debt margin (bps)", 0, 1500, int(d.debt_margin_bps), 5, key=k("margin"),
                                     help="Margin over risk-free for a de-risked project, before project-specific risk premiums.")
            eq_prem = c2.number_input("Base equity premium (bps)", 0, 3000, int(d.equity_premium_bps), 25, key=k("eqp"),
                                      help="Equity premium over risk-free for a de-risked project.")
            gearing = st.slider("Gearing (% debt)", 0.0, 90.0, d.gearing_pct, 1.0, key=k("gear"))
            _clamp_state(k("tenor_debt"), 1, lifetime)
            debt_tenor = st.slider("Debt tenor (years)", 1, lifetime, min(d.debt_tenor_years, lifetime), key=k("tenor_debt"))
            target_dscr = st.slider("Target DSCR", 1.0, 2.0, d.target_dscr, 0.05, key=k("dscr"),
                                    help="Lender sizing covenant; used for debt capacity and DSCR chart.")
            tax = st.slider("Corporate tax rate (%)", 0.0, 40.0, d.tax_rate_pct, 0.5, key=k("tax"))
            _clamp_state(k("dep"), 1, lifetime)
            dep_years = st.slider("Tax depreciation (years)", 1, lifetime, min(d.depreciation_years, lifetime), key=k("dep"))

    return ProjectInputs(
        technology=tech, capacity_mw=capacity, capex_per_kw=capex, fixed_opex_per_kw_yr=fixed_opex,
        variable_opex_per_mwh=var_opex, capacity_factor_pct=cf, degradation_pct=degradation,
        lifetime_years=lifetime, construction_years=construction, start_year=int(start_year),
        ppa_price=ppa_price, ppa_tenor=ppa_tenor, merchant_price=merchant, price_escalation_pct=price_esc,
        opex_escalation_pct=opex_esc, risk_free_rate_pct=rf, debt_margin_bps=margin, equity_premium_bps=eq_prem,
        gearing_pct=gearing, debt_tenor_years=debt_tenor, target_dscr=target_dscr, tax_rate_pct=tax,
        depreciation_years=dep_years,
    )


# --------------------------------------------------------------------------- #
# Risk register
# --------------------------------------------------------------------------- #
def render_param_inputs(
    specs: tuple[ParamSpec, ...], defaults: dict, prefix: str, lifetime: int, *, dynamic: bool = True
) -> dict:
    """Render widgets for a risk's ParamSpecs. ``dynamic`` hides inputs that do not apply (not usable in forms)."""
    values: dict = {}
    for spec in specs:
        if dynamic and spec.key == "probability_pct" and values.get("kind") == SYSTEMIC:
            continue
        key = f"{prefix}__{spec.key}"
        label = f"{spec.label} ({spec.unit})" if spec.unit else spec.label
        default = defaults.get(spec.key, spec.default)
        help_ = spec.help or None
        if spec.options:
            values[spec.key] = st.selectbox(label, spec.options, index=spec.options.index(default), key=key, help=help_) if len(spec.options) > 3 else st.segmented_control(label, spec.options, default=default, key=key, help=help_, required=True)
        elif spec.integer:
            hi = int(min(spec.max_value, lifetime)) if spec.unit == "yr" else int(spec.max_value)
            _clamp_state(key, int(spec.min_value), hi)
            values[spec.key] = st.slider(label, int(spec.min_value), hi, min(int(default), hi), int(spec.step), key=key, help=help_)
        else:
            values[spec.key] = st.slider(
                label, float(spec.min_value), float(spec.max_value), float(default), float(spec.step), key=key, help=help_
            )
    return values


def strength_help() -> str:
    return "Scales the impact magnitude and the cost-of-capital premium: " + ", ".join(
        f"{k} {v:g}x" for k, v in STRENGTH_LEVELS.items()
    )


def custom_risk_form(lifetime: int) -> None:
    with st.popover("Define custom risk", icon=":material/add_circle:", width="stretch"):
        with st.form("custom_risk_form", clear_on_submit=True, border=False):
            st.markdown("**New custom risk**")
            st.text_input("Name", placeholder="e.g. Permitting challenge", key="new__name")
            c1, c2 = st.columns(2)
            c1.text_input("Category", placeholder="Custom", key="new__category")
            c2.select_slider("Risk strength", list(STRENGTH_LEVELS), value="Medium", key="new__strength", help=strength_help())
            st.text_input("Description", key="new__description")
            render_param_inputs(GenericRisk.param_specs, {}, "new", lifetime, dynamic=False)
            c1, c2 = st.columns(2)
            c1.number_input("Debt premium (bps)", 0, 1000, 15, 5, key="new__premium_debt_bps")
            c2.number_input("Equity premium (bps)", 0, 2000, 50, 5, key="new__premium_equity_bps")
            st.form_submit_button("Add to model", type="primary", on_click=add_custom_risk, width="stretch")


def current_kind(cfg: RiskConfig) -> str:
    return st.session_state.get(f"{cfg.risk_id}__kind") or cfg.build().kind


def risk_editor(cfg: RiskConfig, lifetime: int) -> Risk:
    cls = cfg.risk_class
    prefix = cfg.risk_id
    kind = current_kind(cfg)
    with st.expander(cfg.name, icon=KIND_ICON.get(kind), expanded=False):
        st.caption(f"**{cfg.category or cls.default_category}** · {kind} risk — {cfg.description or cls.default_description}")
        if cls.rating_driven:
            rating = st.session_state["country__rating"]
            risk = cfg.build(params={"rating": rating})
            impact = rating_spreads()[rating]
            st.caption(f"{st.session_state['country__name'] or 'Project country'}: {rating}. Shared country allowance; no severity scaling.")
            debt_col, equity_col = st.columns(2)
            debt_col.metric("Rating debt premium", f"{impact[0]:.0f} bps")
            equity_col.metric("Rating equity premium", f"{impact[1]:.0f} bps")
            return risk
        strength = st.select_slider(
            "Risk strength", list(STRENGTH_LEVELS), value=cfg.strength, key=f"{prefix}__strength", help=strength_help()
        )
        params = render_param_inputs(cls.param_specs, cfg.params, prefix, lifetime)
        if cls.risk_type == "currency":
            st.caption("Inputs are USD-equivalent at construction start. FX translates exposed revenue/O&M; debt stays in USD. Unhedged FX paths are annual and stochastic.")
            history = st.file_uploader("Annual FX history for selected currency (optional)", type="csv", key=f"{prefix}__history",
                                       help="year,currency_per_usd; at least 3 consecutive annual observations. Quote is local currency units per USD.")
            if history is not None and params["currency"] != "USD":
                try:
                    history_table = pd.read_csv(io.BytesIO(history.getvalue()))
                    dep, vol = calibrate_fx(history_table)
                    params.update(annual_depreciation_pct=dep, volatility_pct=vol)
                    st.caption(f"History overrides scenario sliders: depreciation {dep:.2f}%/yr; log-return volatility {vol:.2f}%/yr. Historical estimates are not forecasts.")
                    ordered = history_table.sort_values("year")
                    quotes = pd.to_numeric(ordered["currency_per_usd"])
                    st.line_chart(pd.DataFrame({"Local currency USD value (start=100)": 100 * quotes.iloc[0] / quotes, "USD": 100}, index=ordered.index).set_axis(ordered["year"]))
                except (ValueError, TypeError, pd.errors.ParserError) as error:
                    st.error(str(error))
                    st.caption("Using scenario sliders because the history is invalid.")
        c1, c2 = st.columns(2)
        prem_d = c1.number_input(
            "Debt premium (bps)", 0, 1000, int(cfg.premium_debt_bps), 5, key=f"{prefix}__pd",
            help="Premium at Medium strength added to the cost of debt.",
        )
        prem_e = c2.number_input(
            "Equity premium (bps)", 0, 2000, int(cfg.premium_equity_bps), 5, key=f"{prefix}__pe",
            help="Premium at Medium strength added to the cost of equity.",
        )
        if cfg.custom:
            st.button("Remove risk", icon=":material/delete:", key=f"{prefix}__rm", on_click=remove_custom_risk, args=(cfg.risk_id,))
    return cfg.build(strength=strength, params=params, premium_debt_bps=prem_d, premium_equity_bps=prem_e)


def risk_panel(library: list[RiskConfig], lifetime: int) -> tuple[list[Risk], str, list[RiskConfig]]:
    st.subheader("Risk register")
    configs = {c.risk_id: c for c in [*library, *st.session_state.custom_risks]}
    mode = st.segmented_control(
        "Cashflow case",
        ["Expected", "P50", "P75", "P90", "P95", "P99"],
        default="Expected",
        required=True,
        width="stretch",
        help="Expected uses probability-weighted inputs. P90 selects a complete simulated scenario at 90% NPV exceedance (10th percentile).",
    )
    st.multiselect(
        "Active risks",
        list(configs),
        format_func=lambda rid: f"{configs[rid].name}",
        key="selected_risks",
        placeholder="Select risks to apply",
    )
    custom_risk_form(lifetime)

    selected = [configs[r] for r in st.session_state.selected_risks if r in configs]
    if any(cfg.risk_class.rating_driven for cfg in selected):
        st.text_input("Project country", key="country__name", placeholder="Enter country for the rating below")
        ratings = list(rating_spreads())
        default_rating = next(cfg.build().params["rating"] for cfg in selected if cfg.risk_class.rating_driven)
        st.selectbox("Country sovereign foreign-currency rating", ratings, index=ratings.index(default_rating), key="country__rating",
                     help="Enter the current S&P/Fitch broad rating for your country; no automatic or live country-rating feed. Use the shared allowance for political and sovereign exposure.")
        st.caption("Editable illustrative spreads, not current market quotes. Political and sovereign premiums overlap and are counted once.")
    risks: list[Risk] = []
    if not selected:
        st.info("No risks selected - showing the unrisked base case.", icon=":material/info:")
    with st.container(height=620, border=False):
        for kind, title in ((DISCRETE, "Discrete event risks"), (SYSTEMIC, "Systemic risks")):
            group = [c for c in selected if current_kind(c) == kind]
            if group:
                st.markdown(f"**{title}**")
                for cfg in group:
                    risks.append(risk_editor(cfg, lifetime))
    return risks, mode, selected


# --------------------------------------------------------------------------- #
# Charts
# --------------------------------------------------------------------------- #
def intervention_panel(risks: list[Risk], lifetime: int) -> list[Intervention]:
    st.subheader("De-risking interventions")
    active = st.multiselect("Intervention instruments", list(INTERVENTION_REGISTRY),
                            format_func=lambda key: INTERVENTION_REGISTRY[key].label, key="intervention_types")
    lookup = {risk.risk_id: risk for risk in risks}
    interventions = []
    for kind in active:
        cls = INTERVENTION_REGISTRY[kind]
        probe = cls(f"intervention_{kind}")
        eligible = [risk.risk_id for risk in risks if probe.eligible(risk)]
        with st.expander(cls.label, expanded=True, icon=":material/shield:"):
            st.caption(cls.default_description)
            key = f"intervention_{kind}__targets"
            if key in st.session_state:
                previous = st.session_state[key]
                valid = [target for target in previous if target in eligible]
                if valid != previous:
                    st.session_state[key] = valid
            targets = st.multiselect("Target risks", eligible, format_func=lambda target: lookup[target].name, key=key)
            params = render_param_inputs(cls.param_specs, {}, f"intervention_{kind}", lifetime)
            if targets:
                interventions.append(cls(f"intervention_{kind}", target_ids=tuple(targets), params=params))
            else:
                st.caption("No targets selected; this instrument is not charged or applied.")
            if kind in {"guarantee", "country_guarantee"}:
                st.caption("Coverage applies to the targeted risk premium only; baseline margin remains. Annual fees run over the debt tenor and are quoted against base capex, not outstanding principal. A lender guarantee is not operating cashflow or CFADS support.")
            if kind == "country_guarantee":
                st.caption("Select every covered sovereign/political factor: an unprotected overlapping factor can retain the shared country allowance. Equity premiums remain unchanged. Coverage assumes an effective guarantor; guarantor default and recovery delays are not modelled.")
    return interventions


def _layout(fig: go.Figure, height: int, **kwargs) -> go.Figure:
    layout = dict(
        height=height,
        margin=dict(l=10, r=10, t=40, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        hovermode="x unified",
    )
    fig.update_layout(**{**layout, **kwargs})
    return fig


def cashflow_figure(a: ScenarioAnalysis, view: str, show_timeline: bool) -> go.Figure:
    df, base = a.risked.table, a.baseline.table
    x = df["year"]
    heat = a.risked.risk_cash_impact
    with_timeline = show_timeline and not heat.empty
    rows = 2 if with_timeline else 1
    fig = make_subplots(
        rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.06,
        row_heights=[0.72, 0.28] if with_timeline else [1.0],
    )

    def bar(y, name, color, hatch=False, **kw):
        marker = dict(color=color, pattern=HATCH) if hatch else dict(color=color)
        fig.add_trace(go.Bar(x=x, y=y, name=name, marker=marker, hovertemplate="%{y:,.1f} $m", **kw), row=1, col=1)

    if view == "Project":
        risk_costs = np.maximum(df["opex"] + df["risk_extra_cost"] - df["opex_base"], 0)
        bar(df["revenue"], "Revenue", C["revenue"])
        bar(df["revenue_lost"].clip(lower=0), "Revenue lost to risks", "rgba(0,0,0,0)", hatch=True)
        bar(-(df["opex"] + df["risk_extra_cost"] - risk_costs), "O&M", C["opex"])
        bar(-risk_costs, "Risk-driven costs", "rgba(0,0,0,0)", hatch=True)
        bar(-df["capex_base"], "Capex", C["capex"])
        bar(-(df["capex_overrun"] - df["intervention_capex"]).clip(lower=0), "Capex overrun", "rgba(0,0,0,0)", hatch=True)
        if a.interventions:
            bar(-df["intervention_capex"], "Intervention upfront cost", C["opex"])
        bar(-df["unlevered_tax"], "Unlevered tax", C["tax"])
        net, net_base, net_name = df["fcff"], base["fcff"], "Net project cashflow"
    else:
        cfads_lost = (base["cfads"] - df["cfads"]).clip(lower=0)
        bar(df["cfads"].where(df["op_year"] > 0, 0), "CFADS", C["revenue"])
        bar(cfads_lost.where(df["op_year"] > 0, 0), "CFADS lost to risks", "rgba(0,0,0,0)", hatch=True)
        bar(-df["debt_service"], "Debt service", C["debt"])
        bar(df["equity_cf"].where(df["op_year"] == 0, 0), "Equity contribution", C["equity"])
        net, net_base, net_name = df["equity_cf"], base["equity_cf"], "Net equity cashflow"

    if a.interventions:
        metric = "fcff" if view == "Project" else "equity_cf"
        avoided_loss = df[f"intervention_delta_{metric}"].clip(lower=0)
        fig.add_trace(go.Bar(x=x, y=-avoided_loss, name="De-risking: avoided loss (negative)",
                             offsetgroup="avoided_loss", marker=dict(color="rgba(0,0,0,0)", pattern=GREEN_HATCH),
                             hovertemplate="Avoided loss: %{customdata:,.2f} $m<extra></extra>", customdata=avoided_loss), row=1, col=1)
        fig.add_trace(go.Scatter(x=x, y=a.unmitigated.table[metric], name="Without interventions (same risks)",
                                 mode="lines", line=dict(color=C["loss"], dash="dot")), row=1, col=1)

    fig.add_trace(
        go.Scatter(x=x, y=net_base, name=f"{net_name} (no risk)", mode="lines",
                   line=dict(color=C["base"], dash="dash", width=2), hovertemplate="%{y:,.1f} $m"),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=x, y=net, name=f"{net_name} (risked)", mode="lines+markers", fill="tonexty",
                   fillcolor=LOSS_FILL, line=dict(color=C["line"], width=2.5), marker=dict(size=5),
                   hovertemplate="%{y:,.1f} $m"),
        row=1, col=1,
    )
    cod = df.loc[df["op_year"] == 1, "year"]
    if not cod.empty:
        fig.add_vline(x=float(cod.iloc[0]) - 0.5, line=dict(color=C["base"], dash="dot", width=1), row=1, col=1)
        fig.add_annotation(x=float(cod.iloc[0]) - 0.5, y=1, yref="y domain", text="COD", showarrow=False,
                           xanchor="left", font=dict(size=11, color=C["base"]))
    ppa_end = df.loc[df["op_year"] == a.risked.inputs.ppa_tenor, "year"]
    if a.risked.inputs.ppa_tenor < a.risked.inputs.lifetime_years and not ppa_end.empty:
        fig.add_vline(x=float(ppa_end.iloc[0]) + 0.5, line=dict(color=C["base"], dash="dot", width=1), row=1, col=1)
        fig.add_annotation(x=float(ppa_end.iloc[0]) + 0.5, y=1, yref="y domain", text="PPA expiry", showarrow=False,
                           xanchor="left", font=dict(size=11, color=C["base"]))

    if with_timeline:
        zmax = max(float(np.abs(heat.to_numpy()).max()), 1e-6)
        fig.add_trace(
            go.Heatmap(
                z=heat.to_numpy(), x=list(heat.columns), y=list(heat.index),
                zmin=-zmax, zmax=zmax, colorscale=[[0, "#16834b"], [0.5, "#f8f9fa"], [1, C["loss"]]],
                showscale=False, xgap=1, ygap=2,
                hovertemplate="%{y}<br>%{x}: %{z:,.2f} $m<extra>Loss (+) / avoided loss (-)</extra>",
            ),
            row=2, col=1,
        )
        fig.update_yaxes(title_text="Risk timeline", row=2, col=1, tickfont=dict(size=10), autorange="reversed")

    fig.update_yaxes(title_text="$m (nominal)", row=1, col=1, zeroline=True, zerolinecolor="#adb5bd")
    return _layout(fig, 600 if with_timeline else 470, barmode="relative", bargap=0.15)


def dscr_figure(a: ScenarioAnalysis) -> go.Figure:
    df, base = a.risked.table, a.baseline.table
    mask = df["debt_service"] > 0
    x = df.loc[mask, "year"]
    target = a.risked.inputs.target_dscr
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=[target] * len(x), name=f"Target DSCR ({target:.2f}x)", mode="lines",
                             line=dict(color=C["loss"], dash="dot")))
    fig.add_trace(go.Scatter(x=x, y=np.minimum(df.loc[mask, "dscr"], target), name="Breach of target", mode="lines",
                             line=dict(width=0), fill="tonexty", fillcolor=LOSS_FILL, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=base.loc[base["debt_service"] > 0, "year"], y=base.loc[base["debt_service"] > 0, "dscr"],
                             name="DSCR (no risk)", mode="lines", line=dict(color=C["base"], dash="dash")))
    fig.add_trace(go.Scatter(x=x, y=df.loc[mask, "dscr"], name="DSCR (risked)", mode="lines+markers",
                             line=dict(color=C["line"], width=2.5)))
    fig.add_hline(y=1.0, line=dict(color="#adb5bd", width=1))
    fig.update_yaxes(title_text="DSCR (x)")
    return _layout(fig, 380, hovermode="x unified")


def debt_service_figure(a: ScenarioAnalysis) -> go.Figure:
    df, base = a.risked.table, a.baseline.table
    op = df["op_year"] > 0
    fig = go.Figure()
    fig.add_trace(go.Bar(x=df.loc[op, "year"], y=df.loc[op, "cfads"], name="CFADS (risked)", marker_color=C["revenue"]))
    fig.add_trace(go.Scatter(x=base.loc[op, "year"], y=base.loc[op, "cfads"], name="CFADS (no risk)",
                             line=dict(color=C["base"], dash="dash")))
    fig.add_trace(go.Scatter(x=df.loc[op, "year"], y=df.loc[op, "debt_service"], name="Debt service",
                             line=dict(color=C["debt"], width=2.5), line_shape="hv"))
    fig.update_yaxes(title_text="$m (nominal)")
    return _layout(fig, 380)


def coc_waterfall(a: ScenarioAnalysis, which: str) -> go.Figure:
    i = a.risked.inputs
    before = a.unmitigated if a.unmitigated is not None else a.risked
    rf = i.risk_free_rate_pct / 100
    base_prem = i.debt_margin_bps / 1e4 if which == "debt" else i.equity_premium_bps / 1e4
    labels = ["Risk-free", "Base margin" if which == "debt" else "Base equity premium"]
    values = [rf, base_prem]
    for r in a.risked.risks:
        imp = before.risk_impacts[r.risk_id]
        if imp.country_premium:
            continue
        bps = imp.premium_debt_bps if which == "debt" else imp.premium_equity_bps
        if bps:
            labels.append(r.name)
            values.append(bps / 1e4)
    country = [imp for imp in before.risk_impacts.values() if imp.country_premium]
    country_bps = max((imp.premium_debt_bps if which == "debt" else imp.premium_equity_bps for imp in country), default=0)
    if country_bps:
        labels.append("Shared country allowance")
        values.append(country_bps / 1e4)
    premium_column = "premium_debt_bps" if which == "debt" else "premium_equity_bps"
    for _, intervention in a.intervention_attribution.iterrows():
        change = intervention[premium_column]
        if abs(change) > 1e-9:
            labels.append(f"De-risking: {intervention['risk']}")
            values.append(change / 1e4)
    total = a.risked.coc.cost_of_debt if which == "debt" else a.risked.coc.cost_of_equity
    labels.append("Cost of debt" if which == "debt" else "Cost of equity")
    values.append(total)
    measures = ["absolute", "relative"] + ["relative"] * (len(values) - 3) + ["total"]
    fig = go.Figure(
        go.Waterfall(
            x=labels, y=[v * 100 for v in values], measure=measures,
            text=[f"{v * 100:.2f}%" if n < 2 or m == "total" else f"{v * 1e4:+.0f} bps" for n, (v, m) in enumerate(zip(values, measures))],
            textposition="outside",
            increasing=dict(marker=dict(color=C["loss"])),
            decreasing=dict(marker=dict(color="#16834b")),
            totals=dict(marker=dict(color=C["line"])),
            connector=dict(line=dict(color="#ced4da")),
            hovertemplate="%{x}: %{y:.2f}%<extra></extra>",
        )
    )
    fig.update_yaxes(title_text="%", rangemode="tozero")
    fig.update_xaxes(tickmode="array", tickvals=labels,
                     ticktext=["<br>".join(escape(line) for line in wrap(label, width=18)) for label in labels],
                     tickangle=0, automargin=True)
    return _layout(fig, 380, title=dict(text="Cost of debt build-up" if which == "debt" else "Cost of equity build-up", x=0),
                   showlegend=False, hovermode="closest")


def hurdle_waterfall(a: ScenarioAnalysis) -> go.Figure:
    base_wacc = a.baseline.coc.wacc
    priced = a.risked.coc.wacc - base_wacc
    expected = a.cashflow_equivalent_premium_bps / 1e4 if _ok(a.cashflow_equivalent_premium_bps) else 0.0
    labels = ["Baseline WACC", "Priced risk premiums", "Expected-loss equivalent", "Risk-adjusted hurdle"]
    values = [base_wacc * 100, priced * 100, expected * 100, (base_wacc + priced + expected) * 100]
    fig = go.Figure(
        go.Waterfall(
            x=labels, y=values, measure=["absolute", "relative", "relative", "total"],
            text=[f"{values[0]:.2f}%", f"{priced * 1e4:+.0f} bps", f"{expected * 1e4:+.0f} bps", f"{values[3]:.2f}%"],
            textposition="outside",
            increasing=dict(marker=dict(color=C["loss"])), decreasing=dict(marker=dict(color=C["revenue"])),
            totals=dict(marker=dict(color=C["line"])), connector=dict(line=dict(color="#ced4da")),
            hovertemplate="%{x}: %{y:.2f}%<extra></extra>",
        )
    )
    fig.update_yaxes(title_text="% (post-tax WACC)", rangemode="tozero")
    return _layout(fig, 380, title=dict(text="Discount rate for unrisked base-case cashflows", x=0),
                   showlegend=False, hovermode="closest")


def premium_bar(a: ScenarioAnalysis) -> go.Figure:
    names = [r.name for r in a.risked.risks]
    debt = [a.risked.risk_impacts[r.risk_id].premium_debt_bps for r in a.risked.risks]
    eq = [a.risked.risk_impacts[r.risk_id].premium_equity_bps for r in a.risked.risks]
    fig = go.Figure()
    fig.add_trace(go.Bar(y=names, x=debt, name="Debt premium", orientation="h", marker_color=C["debt"]))
    fig.add_trace(go.Bar(y=names, x=eq, name="Equity premium", orientation="h", marker_color=C["equity"]))
    fig.update_xaxes(title_text="bps (strength-adjusted)")
    fig.update_yaxes(autorange="reversed")
    return _layout(fig, 120 + 40 * len(names), barmode="group", hovermode="y unified")


ATTRIBUTION_OPTIONS = {
    "LCOE ($/MWh)": ("lcoe", lambda v: f"{v:+.2f}", lambda v: f"{v:.2f}"),
    "Project NPV ($m)": ("npv", lambda v: f"{v:+.1f}", lambda v: f"{v:.1f}"),
    "Equity IRR (%)": ("equity_irr", lambda v: f"{v * 100:+.2f}pp", lambda v: f"{v * 100:.2f}%"),
    "Minimum DSCR (x)": ("min_dscr", lambda v: f"{v:+.2f}x", lambda v: f"{v:.2f}x"),
}


def attribution_waterfall(a: ScenarioAnalysis, label: str) -> go.Figure:
    key, fmt_d, fmt_abs = ATTRIBUTION_OPTIONS[label]
    att = a.attribution
    base_v, risk_v = a.baseline.metrics[key], a.risked.metrics[key]
    scale = 100 if key == "equity_irr" else 1
    labels = ["No-risk base case", *att["risk"], "Risked case"]
    vals = [base_v, *att[key].fillna(0), risk_v]
    fig = go.Figure(
        go.Waterfall(
            x=labels, y=[v * scale for v in vals], measure=["absolute", *["relative"] * len(att), "total"],
            text=[fmt_abs(vals[0]), *[fmt_d(v) for v in vals[1:-1]], fmt_abs(vals[-1])],
            textposition="outside",
            increasing=dict(marker=dict(color=C["loss"] if key == "lcoe" else C["revenue"])),
            decreasing=dict(marker=dict(color=C["revenue"] if key == "lcoe" else C["loss"])),
            totals=dict(marker=dict(color=C["line"])), connector=dict(line=dict(color="#ced4da")),
            hoverinfo="skip",
        )
    )
    fig.update_yaxes(title_text=label)
    return _layout(fig, 430, showlegend=False, hovermode="closest")


def lcoe_split_figure(a: ScenarioAnalysis) -> go.Figure:
    att = a.attribution[a.attribution["risk"] != "Interaction effects"]
    fig = go.Figure()
    fig.add_trace(go.Bar(y=att["risk"], x=att["lcoe_cashflow"], name="Cashflow effect", orientation="h", marker_color=C["loss"]))
    fig.add_trace(go.Bar(y=att["risk"], x=att["lcoe_cost_of_capital"], name="Cost-of-capital effect", orientation="h",
                         marker_color=C["equity"]))
    fig.update_xaxes(title_text="Δ LCOE ($/MWh, standalone)")
    fig.update_yaxes(autorange="reversed")
    return _layout(fig, 120 + 40 * len(att), barmode="relative", hovermode="y unified")


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #
def kpi_cards(a: ScenarioAnalysis) -> None:
    m, b = a.risked.metrics, a.baseline.metrics
    cards = [
        ("LCOE", f"${m['lcoe']:.1f}/MWh", delta_str(m["lcoe"], b["lcoe"], "lcoe"), "inverse",
         "Levelised cost of electricity: PV of capex, O&M and risk costs / PV of delivered generation, at the risked WACC."),
        ("Project NPV", fmt_money(m["npv"]), delta_str(m["npv"], b["npv"], "money"), "normal",
         "NPV of unlevered post-tax free cashflow at the (risk-adjusted) WACC."),
        ("Equity IRR", fmt_pct(m["equity_irr"]), delta_str(m["equity_irr"], b["equity_irr"], "pct"), "normal",
         f"Compare against the cost of equity: {fmt_pct(m['cost_of_equity'])}."),
        ("Min DSCR", f"{m['min_dscr']:.2f}x" if _ok(m["min_dscr"]) else "n/a", delta_str(m["min_dscr"], b["min_dscr"], "ratio"), "normal",
         f"Lowest annual CFADS / debt service. Target {a.risked.inputs.target_dscr:.2f}x."),
        ("WACC", fmt_pct(m["wacc"]), delta_str(m["wacc"], b["wacc"], "pct"), "inverse",
         "Post-tax nominal WACC including risk premiums."),
    ]
    more = [
        ("Project IRR", fmt_pct(m["project_irr"]), delta_str(m["project_irr"], b["project_irr"], "pct"), "normal", None),
        ("Average DSCR", f"{m['avg_dscr']:.2f}x" if _ok(m["avg_dscr"]) else "n/a", delta_str(m["avg_dscr"], b["avg_dscr"], "ratio"), "normal", None),
        ("LLCR", f"{m['llcr']:.2f}x" if _ok(m["llcr"]) else "n/a", delta_str(m["llcr"], b["llcr"], "ratio"), "normal",
         "Loan life cover ratio: PV of CFADS over the debt tenor / debt."),
        ("Max gearing at target DSCR", fmt_pct(m["max_gearing"], 1), delta_str(m["max_gearing"], b["max_gearing"], "pp"), "normal",
         "Debt capacity from sculpting risked CFADS at the target DSCR, as a share of capex."),
        ("Levelised revenue", f"${m['levelised_revenue']:.1f}/MWh", delta_str(m["levelised_revenue"], b["levelised_revenue"], "lcoe"), "normal",
         "PV revenue / PV generation - captures price-side risks that LCOE (a cost metric) does not."),
        ("PV revenue lost", fmt_money(m["revenue_lost_pv"]), None, "off", "PV of revenue lost to risks, at the risked WACC."),
        ("Total capex", fmt_money(m["total_capex"]), delta_str(m["total_capex"], b["total_capex"], "money"), "inverse", None),
        ("Equity payback", f"{m['equity_payback_years']:.0f} yrs" if _ok(m["equity_payback_years"]) else "Not reached",
         delta_str(m["equity_payback_years"], b["equity_payback_years"], "years"), "inverse", "Operating years until cumulative equity cashflow turns positive."),
    ]
    for col, (label, value, delta, color, help_) in zip(st.columns(len(cards)), cards):
        col.metric(label, value, delta, delta_color=color, help=help_, border=True)
    with st.expander("More financial metrics", icon=":material/query_stats:"):
        for row in (more[:4], more[4:]):
            for col, (label, value, delta, color, help_) in zip(st.columns(4), row):
                col.metric(label, value, delta, delta_color=color, help=help_)


def cashflow_tab(a: ScenarioAnalysis) -> None:
    c1, c2 = st.columns([3, 2])
    view = c1.segmented_control("Cashflow view", ["Project", "Equity"], default="Project", required=True,
                                help="Project: unlevered cashflow. Equity: levered cashflow after debt service.")
    show_timeline = c2.toggle("Show risk timeline", value=True, help="Indicative standalone cash impact of each risk by year.")
    st.plotly_chart(cashflow_figure(a, view, show_timeline), key="cashflow_chart")
    st.caption(
        "Hatched red bars show revenue lost, extra costs and capex overruns caused by the selected risks; "
        "the shaded band is the gap between the no-risk and risked net cashflow. "
        "The risk timeline shows each risk's standalone cash impact (red = loss)."
    )
    if a.interventions:
        st.caption("Green negative hatching is net avoided loss after fees and tax, compared with the same risks without interventions. It is a separate comparison stack, not an expenditure or an additional cashflow. Lines and financial metrics use actual net cashflows.")


def debt_tab(a: ScenarioAnalysis) -> None:
    m, b = a.risked.metrics, a.baseline.metrics
    c = st.columns(4)
    c[0].metric("Debt", fmt_money(m["debt"]), delta_str(m["debt"], b["debt"], "money"), delta_color="off")
    c[1].metric("Debt capacity @ target DSCR", fmt_money(m["debt_capacity"]), delta_str(m["debt_capacity"], b["debt_capacity"], "money"))
    c[2].metric("Cost of debt", fmt_pct(m["cost_of_debt"]), delta_str(m["cost_of_debt"], b["cost_of_debt"], "pct"), delta_color="inverse")
    breaches = int((a.risked.table["dscr"] < a.risked.inputs.target_dscr).sum())
    c[3].metric("Years below target DSCR", breaches, breaches - int((a.baseline.table["dscr"] < a.baseline.inputs.target_dscr).sum()) or None,
                delta_color="inverse")
    if m["debt"] > m["debt_capacity"]:
        st.warning(
            f"Debt at {a.risked.inputs.gearing_pct:.0f}% gearing ({fmt_money(m['debt'])}) exceeds the risked debt capacity "
            f"({fmt_money(m['debt_capacity'])}). Lenders would cap gearing at about {fmt_pct(m['max_gearing'], 0)}.",
            icon=":material/warning:",
        )
    l, r = st.columns(2)
    l.plotly_chart(dscr_figure(a), key="dscr_chart")
    r.plotly_chart(debt_service_figure(a), key="ds_chart")


def coc_tab(a: ScenarioAnalysis) -> None:
    st.caption("The combined hurdle-rate display is an indicative additive approximation, not an exact NPV-equivalent discount rate. Expected-loss equivalent rates are valuation comparisons, not empirical estimates of the premium investors require.")
    bc, rc = a.baseline.coc, a.risked.coc
    c = st.columns(4)
    c[0].metric("Risk-free rate", fmt_pct(rc.risk_free))
    c[1].metric("Cost of debt", fmt_pct(rc.cost_of_debt), delta_str(rc.cost_of_debt, bc.cost_of_debt, "pct"), delta_color="inverse")
    c[2].metric("Cost of equity", fmt_pct(rc.cost_of_equity), delta_str(rc.cost_of_equity, bc.cost_of_equity, "pct"), delta_color="inverse")
    c[3].metric("WACC (post-tax)", fmt_pct(rc.wacc), delta_str(rc.wacc, bc.wacc, "pct"), delta_color="inverse")

    st.plotly_chart(coc_waterfall(a, "debt"), key="kd_wf")
    st.plotly_chart(coc_waterfall(a, "equity"), key="ke_wf")

    l, r = st.columns(2)
    with l:
        st.plotly_chart(hurdle_waterfall(a), key="hurdle_wf")
    with r:
        st.markdown("**How should the risks be priced?**")
        cfe = a.cashflow_equivalent_premium_bps
        st.markdown(
            f"- **Priced risk premiums** add **{(rc.wacc - bc.wacc) * 1e4:+.0f} bps** to the WACC. They compensate investors "
            "for uncertainty that cannot be diversified away and are applied to the risked cashflows in this model.\n"
            f"- **Expected-loss equivalent**: **{cfe:+.0f} bps**. This is the WACC uplift that, applied to the *unrisked* base-case "
            "cashflows, gives the same NPV as the risk-adjusted cashflows. It is the minimum premium a financier "
            "should demand if they model only the base case.\n"
            f"- **Risk-adjusted hurdle** for a base-case model: **{fmt_pct(bc.wacc + (rc.wacc - bc.wacc) + (cfe / 1e4 if _ok(cfe) else 0))}**."
            if _ok(cfe) else "Expected-loss equivalent premium could not be solved for this cashflow profile."
        )
        if a.risked.risks:
            st.plotly_chart(premium_bar(a), key="prem_bar")


def attribution_tab(a: ScenarioAnalysis) -> None:
    if a.attribution.empty:
        st.info("Select one or more risks to see their individual contribution.", icon=":material/info:")
        return
    label = st.segmented_control("Metric", list(ATTRIBUTION_OPTIONS), default="LCOE ($/MWh)", required=True)
    st.plotly_chart(attribution_waterfall(a, label), key="attr_wf")
    st.caption(
        "Each bar is the risk's standalone impact versus the no-risk case; 'Interaction effects' captures the "
        "difference between the joint impact and the sum of standalone impacts."
    )
    if a.interventions:
        st.caption("Intervention contributions are sequential marginal effects in instrument-selection order, including fees. This allocation is order-dependent, not a causal estimate or Shapley attribution.")
    l, r = st.columns([3, 2])
    l.plotly_chart(lcoe_split_figure(a), key="lcoe_split")
    with r:
        st.markdown("**Standalone impacts**")
        st.dataframe(
            a.attribution[["risk", "kind", "lcoe", "npv", "equity_irr", "min_dscr", "wacc"]],
            hide_index=True,
            column_config={
                "risk": "Risk",
                "kind": "Type",
                "lcoe": st.column_config.NumberColumn("Δ LCOE ($/MWh)", format="%+.2f"),
                "npv": st.column_config.NumberColumn("Δ NPV ($m)", format="%+.1f"),
                "equity_irr": st.column_config.NumberColumn("Δ Equity IRR", format="percent"),
                "min_dscr": st.column_config.NumberColumn("Δ Min DSCR", format="%+.2f"),
                "wacc": st.column_config.NumberColumn("Δ WACC", format="percent"),
            },
        )


TABLE_COLUMNS = {
    "year": "Year", "phase": "Phase", "generation_mwh": "Generation (MWh)", "price": "Price ($/MWh)",
    "revenue": "Revenue", "revenue_lost": "Revenue lost", "opex": "O&M", "risk_extra_cost": "Risk costs",
    "capex": "Capex", "ebitda": "EBITDA", "tax": "Tax", "cfads": "CFADS", "interest": "Interest",
    "principal": "Principal", "dscr": "DSCR", "fcff": "Project FCF", "equity_cf": "Equity CF",
    "intervention_capex": "Intervention upfront cost", "intervention_fees": "Intervention annual fees",
}


def risk_configs_csv(selected: list[RiskConfig], risks: list[Risk]) -> str:
    """Export the current risk settings in the library CSV format so they can be reused as defaults."""
    rows = []
    configs = {cfg.risk_id: cfg for cfg in selected}
    for r in risks:
        cfg = configs[r.risk_id]
        rows.append({
            "risk_id": r.risk_id, "risk_type": cfg.risk_type, "name": r.name, "category": r.category, "enabled": True,
            "strength": r.strength, "premium_debt_bps": r.premium_debt_bps, "premium_equity_bps": r.premium_equity_bps,
            "parameters": ";".join(f"{k}={v}" for k, v in r.params.items()), "description": r.description,
        })
    buf = io.StringIO()
    pd.DataFrame(rows).to_csv(buf, index=False)
    return buf.getvalue()


def data_tab(a: ScenarioAnalysis, selected: list[RiskConfig]) -> None:
    case = st.segmented_control("Case", ["Risked", "No risk"], default="Risked", required=True)
    result = a.risked if case == "Risked" else a.baseline
    table = result.table[list(TABLE_COLUMNS)].rename(columns=TABLE_COLUMNS)
    money = [c for c in table.columns if c not in ("Year", "Phase", "Generation (MWh)", "Price ($/MWh)", "DSCR")]
    st.dataframe(
        table, hide_index=True, height=420,
        column_config={
            "Year": st.column_config.NumberColumn(format="%d"),
            "Generation (MWh)": st.column_config.NumberColumn(format="%.0f"),
            "Price ($/MWh)": st.column_config.NumberColumn(format="%.2f"),
            "DSCR": st.column_config.NumberColumn(format="%.2fx"),
            **{c: st.column_config.NumberColumn(f"{c} ($m)", format="%.2f") for c in money},
        },
    )
    c1, c2 = st.columns(2)
    c1.download_button("Download cashflow (CSV)", result.table.to_csv(index=False), f"cashflow_{case.lower().replace(' ', '_')}.csv",
                       "text/csv", icon=":material/download:", width="stretch")
    c2.download_button("Download risk settings (CSV)", risk_configs_csv(selected, a.risked.risks), "risks.csv", "text/csv",
                       icon=":material/download:", width="stretch", disabled=not a.risked.risks,
                       help="Same format as data/default_risks.csv - save it there to make these settings the defaults.")


def probability_tab(simulation: SimulationAnalysis, inputs: ProjectInputs) -> None:
    metrics = simulation.metrics
    first, second, third = st.columns(3)
    first.metric("Probability NPV < 0", f"{100 * (metrics.npv < 0).mean():.1f}%")
    dscr = metrics.min_dscr.dropna()
    second.metric("Probability min DSCR < 1", f"{100 * (dscr < 1).mean():.1f}%" if len(dscr) else "n/a")
    third.metric("Probability min DSCR < target", f"{100 * (dscr < inputs.target_dscr).mean():.1f}%" if len(dscr) else "n/a")
    lower, median, upper = np.quantile(simulation.annual_fcff, [0.05, 0.5, 0.95], axis=0)
    years = simulation.selected.risked.table.year
    figure = go.Figure()
    figure.add_trace(go.Scatter(x=years, y=upper, mode="lines", line=dict(width=0), showlegend=False, hoverinfo="skip"))
    figure.add_trace(go.Scatter(x=years, y=lower, mode="lines", line=dict(width=0), fill="tonexty",
                               fillcolor=LOSS_FILL, name="5th-95th percentile band"))
    figure.add_trace(go.Scatter(x=years, y=median, mode="lines", name="Pointwise P50", line=dict(color=C["base"], dash="dash")))
    figure.add_trace(go.Scatter(x=years, y=simulation.selected.risked.table.fcff, mode="lines",
                               name=f"P{simulation.exceedance_pct:g} NPV-ranked path", line=dict(color=C["line"])))
    figure.update_yaxes(title_text="Project free cashflow ($m)")
    st.plotly_chart(_layout(figure, 380), key="probability_cashflow")
    st.caption("Pointwise bands are annual quantiles, not one project path. Below, each metric has its own downside percentile: low NPV/IRR/DSCR/generation, high LCOE. NaN IRRs are excluded from IRR quantiles.")
    st.dataframe(simulation.percentiles().rename(columns={"npv": "NPV ($m)", "equity_irr": "Equity IRR", "min_dscr": "Minimum DSCR", "lcoe": "LCOE ($/MWh)", "lifetime_generation_twh": "Generation (TWh)"}),
                 column_config={"Equity IRR": st.column_config.NumberColumn(format="percent")})
    histogram = go.Figure(go.Histogram(x=metrics.npv, nbinsx=35, marker_color=C["revenue"], name="NPV"))
    histogram.add_vline(x=0, line=dict(color=C["loss"], dash="dash"))
    histogram.update_xaxes(title_text="Project NPV ($m)")
    histogram.update_yaxes(title_text="Simulation draws")
    st.plotly_chart(_layout(histogram, 300, showlegend=False), key="npv_distribution")
    st.download_button("Download simulated metrics", metrics.to_csv(index=False), "simulation_metrics.csv", "text/csv", icon=":material/download:")


def intervention_tab(a: ScenarioAnalysis, simulation: SimulationAnalysis, inputs: ProjectInputs,
                     settings: list[dict], intervention_settings: list[dict], draws: int, seed: int,
                     severity_cv: float, case: str) -> None:
    if a.interventions:
        counterfactual = a.unmitigated
        metrics = a.risked.metrics
        first, second, third = st.columns(3)
        first.metric("Net NPV benefit", fmt_money(metrics["npv"] - counterfactual.metrics["npv"]))
        second.metric("Minimum DSCR change", f"{metrics['min_dscr'] - counterfactual.metrics['min_dscr']:+.2f}x")
        third.metric("WACC relief", f"{(counterfactual.coc.wacc - a.risked.coc.wacc) * 1e4:.0f} bps")
        st.dataframe(pd.DataFrame({"Without interventions": counterfactual.metrics, "With interventions": metrics}).loc[
            ["npv", "equity_npv", "lcoe", "min_dscr", "llcr", "debt", "wacc"]])
        benefit = simulation.metrics.npv - simulation.unmitigated_metrics.npv
        error = benefit.std(ddof=1) / math.sqrt(len(benefit))
        st.caption(f"Paired Monte Carlo mean NPV benefit: {benefit.mean():.2f} $m; Monte Carlo standard error: {error:.3f} $m ({len(benefit):,} paired draws). This measures sampling precision, not model or parameter uncertainty.")
        st.dataframe(a.intervention_attribution.rename(columns={"risk": "Intervention", "npv": "Marginal NPV ($m)"}), hide_index=True)
        paired = simulation.metrics.add_prefix("mitigated_").join(simulation.unmitigated_metrics.add_prefix("unmitigated_"))
        st.download_button("Download paired simulation metrics", paired.to_csv(index=False), "paired_intervention_metrics.csv", "text/csv", icon=":material/download:")
    else:
        st.info("Select an intervention and its target risks to compare de-risking outcomes.")
    st.markdown("**Research assumptions and reproducibility**")
    st.caption("Project/investor perspective, not a social cost-benefit analysis. Guarantee contingent liabilities and public implementation costs are not valued unless entered as project fees. Effectiveness and premium relief require evidence; defaults are illustrative. Project NPV combines cashflow and discount-rate changes; use equity NPV and paired cashflows to distinguish investor effects. No claim of empirical validation is made.")
    directory = Path(__file__).parent
    files = ["project_cashflow.py", "risk_objects.py", "intervention_objects.py", "streamlit_app.py", "data/default_risks.csv", "data/sovereign_spreads.csv"]
    specification = {
        "model_version": "de-risking-v1", "project_inputs": asdict(inputs), "risks": settings,
        "interventions": intervention_settings, "sovereign_spreads": rating_spreads(),
        "simulation": {"draws": draws, "seed": seed, "severity_cv": severity_cv, "case": case},
        "dependencies": {"numpy": np.__version__, "pandas": pd.__version__, "streamlit": st.__version__},
        "sha256": {file: hashlib.sha256((directory / file).read_bytes()).hexdigest() for file in files},
        "perspective": "Project/investor; nominal USD; no public contingent-liability valuation",
        "assumptions": "Independent risks; mean-one lognormal severity; annual FX lognormal increments; fixed gearing; annuity debt; no tax loss carryforward; illustrative country spreads",
    }
    st.download_button("Download research run specification", json.dumps(specification, indent=2), "research_run.json", "application/json", icon=":material/download:")


def model_notes() -> None:
    with st.expander("Model notes & assumptions", icon=":material/menu_book:"):
        st.markdown(
            """
- **Timeline**: annual model; capex is spread evenly over construction, operation starts at COD. Cashflows are nominal,
  with prices and O&M escalated from the construction start year and discounted to that year.
- **Risks** act on generation, price, O&M, capex and additional costs. Multiple risks combine **multiplicatively** on
  factors and **additively** on costs and cost-of-capital premiums. *Discrete* risks are probability-weighted in the
    *Expected* case. Simulation samples event occurrence with Bernoulli draws and independent mean-one lognormal severity.
    Systemic severity is persistent over the life; FX uses independent annual lognormal increments.
- **Percentiles** use exceedance conventions: P90 is a downside 10th percentile for value/coverage and a 90th percentile
    for costs. The selected main case is a complete path ranked by project NPV, not a stitched cashflow or a P90 guarantee for every metric.
    Metric-specific quantiles and pointwise cashflow bands are reported separately. Correlations are not calibrated.
- **Risk strength** scales cashflow severity and ordinary premiums; sovereign/political premiums depend only on rating.
    Rating spreads are editable illustrative benchmarks in data/sovereign_spreads.csv, not live agency ratings or market prices.
    Political and sovereign allowances share one premium (the maximum) to avoid double counting.
- **Currency**: construction costs and debt are USD; revenue and O&M local shares are translated to USD from year 0.
    FX scenario inputs are not observed depreciation for the named currency. Optional annual history calibrates mean USD-value loss
    and log-return volatility; no live FX feed, hedge costs, or interest-rate parity model is included.
- **Financing**: debt = gearing x total capex, drawn pro-rata with capex and repaid as an annuity from COD. Interest during
  construction is assumed within capex. Debt capacity sculpts risked CFADS at the target DSCR over the tenor.
- **Cost of capital**: cost of debt = risk-free + base margin + risk debt premiums; cost of equity = risk-free + base
  equity premium + risk equity premiums; post-tax WACC is used for NPV and LCOE.
- **Tax**: straight-line tax depreciation, no loss carry-forward.
- **De-risking**: targeted transformations reduce only adverse components of an existing risk; no output is restored
    above the no-risk level. Stacked instruments reduce residual losses multiplicatively. Guarantees reduce only targeted
    debt premiums, not baseline margins, physical losses or CFADS. Fees are independent of severity and charged once per
    instrument. General credit guarantees cover non-country risks; political/country guarantees cover rating-driven
    sovereign/political factors only. Guarantor default, contractual exclusions and recovery delays are not modelled. Fees per
    instrument: upfront costs are capitalised across construction; annual fees escalate with O&M through operating life
    (guarantees stop at loan maturity). Annual fees are quoted against base capex, not guaranteed outstanding debt.
    Assistance/reforms change severity, not event probability. Fixed gearing remains; debt capacity is indicative and does
    not automatically resize financing. Country allowance is recomputed after mitigation; overlapping unprotected risks can
    retain the premium. Causal effectiveness, correlations, fiscal guarantee cost and general-equilibrium effects are not estimated.
- **Adding risk categories**: subclass `Risk` in `risk_objects.py`, decorate with `@register_risk`, and add a row to
  `data/default_risks.csv`. The UI is generated from each risk's `param_specs`.
            """
        )


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #
def main() -> None:
    library = risk_library()
    init_state(library)
    inputs = project_inputs_sidebar()

    st.title("Renewables Project Finance Risk Modeller")
    st.caption(
        f"{inputs.technology} · {inputs.capacity_mw:,.0f} MW · capex {fmt_money(inputs.capex_total)} · "
        f"{inputs.lifetime_years}-year life · PPA {inputs.ppa_tenor} yrs @ ${inputs.ppa_price:.0f}/MWh"
    )

    left, right = st.columns([1, 2.6], gap="large")
    with left:
        risks, case, selected = risk_panel(library, inputs.lifetime_years)
        interventions = intervention_panel(risks, inputs.lifetime_years)
        with st.expander("Simulation assumptions", icon=":material/tune:"):
            draws = st.select_slider("Simulation draws", [200, 500, 1000, 2000], value=500)
            severity_cv = st.slider("Severity uncertainty (coefficient of variation)", 0.0, 1.0, 0.25, 0.05,
                                    help="Mean-one lognormal severity for ordinary risks. Does not scale rating premiums; FX volatility has its own input.")
            seed = st.number_input("Random seed", 0, 100000, 42)

    settings = [{"risk_type": risk.risk_type, "kwargs": vars(risk)} for risk in risks]
    intervention_settings = [{"risk_type": intervention.risk_type, "kwargs": vars(intervention)} for intervention in interventions]
    simulation = cached_simulation(inputs, settings, intervention_settings, rating_spreads(), draws, int(case[1:]) if case != "Expected" else 90, int(seed), severity_cv)
    analysis = analyse(inputs, risks, interventions=interventions) if case == "Expected" else simulation.selected

    with right:
        st.caption("Expected-input case" if case == "Expected" else f"{case} NPV-exceedance scenario · {draws:,} draws · seed {seed}. Other metrics belong to this same path, not their own percentile.")
        kpi_cards(analysis)
        tabs = st.tabs(["Cashflow", "Debt & DSCR", "Cost of capital", "Risk attribution", "Data", "Probability", "De-risking & research"])
        with tabs[0]:
            cashflow_tab(analysis)
        with tabs[1]:
            debt_tab(analysis)
        with tabs[2]:
            coc_tab(analysis)
        with tabs[3]:
            attribution_tab(analysis)
        with tabs[4]:
            data_tab(analysis, selected)
        with tabs[5]:
            probability_tab(simulation, inputs)
        with tabs[6]:
            intervention_tab(analysis, simulation, inputs, settings, intervention_settings, draws, int(seed), severity_cv, case)
        model_notes()


main()
