"""Dashboard page — dataset overview, Ro5, model performance, feature importance."""

import math
import pandas as pd
import altair as alt
import streamlit as st

from _shared import load_eda, load_metrics, load_ro5, load_predictions, load_top_features

# ── Sidebar controls (only visible on this page) ──────────────────────────────

# Read shared settings from session state

st.title(":material/dashboard: Dashboard")
st.caption(
    "Explore the training dataset, Lipinski Ro5 properties, "
    "model performance metrics, and feature importance."
)
threshold       = st.session_state.get("threshold", (6.0, 7.0))
threshold_lo    = threshold[0]
threshold_hi    = threshold[1]
show_ic50       = st.session_state.get("show_ic50", False)
activity_filter = st.session_state.get("activity_filter", ["Active", "Intermediate", "Inactive"])

_ZONE_DOMAIN = ["Active", "Intermediate", "Inactive"]
_ZONE_RANGE  = ["#e040fb", "#5D3FD3", "#00bcd4"]

# Dynamic colour scale — only include selected classes so legend stays in sync
_zone_color  = dict(zip(_ZONE_DOMAIN, _ZONE_RANGE))
_filt_domain = [z for z in _ZONE_DOMAIN if z in activity_filter]
_filt_range  = [_zone_color[z] for z in _filt_domain]

def _zone(pic50):
    if pic50 >= threshold_hi: return "Active"
    elif pic50 >= threshold_lo: return "Intermediate"
    return "Inactive"

# ── Section 1: Dataset overview ───────────────────────────────────────────────
st.subheader("Dataset Overview")
eda_df = load_eda().copy()
_ds_metrics  = load_metrics()
if not eda_df.empty:
    eda_df["activity_zone"] = eda_df["pchembl_value"].apply(_zone)
    n_total  = len(eda_df)
    n_active = (eda_df["activity_label"] == "Active").sum()
    eda_filt = eda_df[eda_df["activity_zone"].isin(activity_filter)]

    with st.container(border=True):
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Total compounds", f"{n_total:,}",       icon=":material/science:")
        c2.metric("Active",           f"{n_active:,}",       icon=":material/check_circle:")
        c3.metric("Inactive",         f"{n_total - n_active:,}", icon=":material/cancel:")
        c4.metric("Train size",  f"{_ds_metrics.get('n_train', 0):,}" if _ds_metrics else "—", icon=":material/model_training:")
        c5.metric("Test size",   f"{_ds_metrics.get('n_test', 0):,}"  if _ds_metrics else "—", icon=":material/quiz:")

    col_hist, col_class = st.columns(2)

    with col_hist:
        if show_ic50:
            eda_plot = eda_filt.copy()
            eda_plot["ic50_nm"] = 10 ** (9 - eda_plot["pchembl_value"])
            lo_ic50 = round(10 ** (9 - threshold_hi), 2)
            hi_ic50 = round(10 ** (9 - threshold_lo), 2)
            lo_log  = math.log10(lo_ic50)
            hi_log  = math.log10(hi_ic50)
            eda_plot["log10_ic50"] = eda_plot["ic50_nm"].apply(
                lambda v: math.log10(v) if v > 0 else -6
            )
            _log_min = eda_plot["log10_ic50"].min()
            _log_max = eda_plot["log10_ic50"].max()

            def _linspace(a, b, n):
                return [a + i * (b - a) / n for i in range(n + 1)]

            def _fmt_ic50(v):
                return f"{v:.0f}" if v >= 10 else (f"{v:.1f}" if v >= 1 else f"{v:.2f}")

            _n = 14
            _ra  = max(lo_log - _log_min, 0.01)
            _rb  = max(hi_log - lo_log, 0.01)
            _rc  = max(_log_max - hi_log, 0.01)
            _tot = _ra + _rb + _rc
            _edges = (
                _linspace(_log_min, lo_log,  max(1, round(_n * _ra / _tot)))
                + _linspace(lo_log,  hi_log,  max(1, round(_n * _rb / _tot)))[1:]
                + _linspace(hi_log,  _log_max, max(1, round(_n * _rc / _tot)))[1:]
            )
            _rows = []
            for _i in range(len(_edges) - 1):
                _lo, _hi_e = _edges[_i], _edges[_i + 1]
                _is_last   = (_i == len(_edges) - 2)
                _mask      = (eda_plot["log10_ic50"] >= _lo) & (
                    (eda_plot["log10_ic50"] <= _hi_e) if _is_last else (eda_plot["log10_ic50"] < _hi_e)
                )
                _mid = (_lo + _hi_e) / 2
                _z   = "Active" if _mid <= lo_log else ("Intermediate" if _mid <= hi_log else "Inactive")
                _rows.append({
                    "bin_start": _lo, "bin_end": _hi_e, "zone": _z,
                    "count": int(_mask.sum()),
                    "ic50_range": f"{_fmt_ic50(10**_lo)}–{_fmt_ic50(10**_hi_e)} nM",
                })
            _hist_df = pd.DataFrame(_rows)
            _hist_df = _hist_df[_hist_df["count"] > 0]
            _hist_df["y0"]        = 0
            _gap = (_log_max - _log_min) / _n * 0.05
            _hist_df["bar_start"] = _hist_df["bin_start"] + _gap
            _hist_df["bar_end"]   = _hist_df["bin_end"]   - _gap
            st.markdown("**IC\u2085\u2080 distribution**")
            hist = (
                alt.Chart(_hist_df)
                .mark_rect(opacity=0.85)
                .encode(
                    alt.X("bar_start:Q", title="IC\u2085\u2080 (nM)",
                          axis=alt.Axis(
                              values=[-2, -1, 0, 1, 2, 3, 4, 5],
                              labelExpr="pow(10, datum.value) < 1 ? format(pow(10, datum.value), '.2f') : pow(10, datum.value) < 1000 ? format(pow(10, datum.value), '.0f') : format(pow(10, datum.value)/1000, '.0f') + 'k'"
                          )),
                    alt.X2("bar_end:Q"),
                    alt.Y("count:Q", title="Compounds"),
                    alt.Y2("y0:Q"),
                    alt.Color("zone:N",
                              scale=alt.Scale(domain=_filt_domain, range=_filt_range),
                              legend=None),
                    tooltip=[alt.Tooltip("ic50_range:N", title="IC\u2085\u2080 range"),
                             alt.Tooltip("count:Q", title="Count")],
                )
            )
            rules_df = pd.DataFrame({
                "x":     [lo_log, hi_log],
                "label": [f"active \u2264 {lo_ic50:.1f} nM", f"inactive > {hi_ic50:.0f} nM"],
            })
        else:
            eda_plot = eda_filt.copy()
            st.markdown("**pIC50 distribution**")
            hist = (
                alt.Chart(eda_plot)
                .mark_bar(opacity=0.85)
                .encode(
                    alt.X("pchembl_value:Q", bin=alt.Bin(maxbins=30), title="pIC50"),
                    alt.Y("count():Q", title="Compounds"),
                    alt.Color("activity_zone:N",
                              scale=alt.Scale(domain=_filt_domain, range=_filt_range),
                              legend=None),
                    tooltip=[alt.Tooltip("pchembl_value:Q", bin=True, title="pIC50 bin"),
                             alt.Tooltip("count():Q", title="Count")],
                )
            )
            rules_df = pd.DataFrame({
                "x":     [threshold_lo, threshold_hi],
                "label": [f"inactive < {threshold_lo:.1f}", f"active \u2265 {threshold_hi:.1f}"],
            })

        rules_lines = (
            alt.Chart(rules_df)
            .mark_rule(strokeDash=[6, 3], strokeWidth=1.5)
            .encode(x="x:Q",
                    color=alt.Color("label:N",
                                    scale=alt.Scale(range=["#e040fb", "#00bcd4"]),
                                    legend=None))
        )
        rules_text = (
            alt.Chart(rules_df)
            .mark_text(align="left", dx=4, dy=-8, fontSize=11)
            .encode(x="x:Q", y=alt.value(0), text="label:N",
                    color=alt.Color("label:N",
                                    scale=alt.Scale(range=["#e040fb", "#00bcd4"]),
                                    legend=None))
        )
        st.altair_chart(
            alt.layer(hist, rules_lines, rules_text)
                .resolve_scale(color="independent")
                .properties(height=280)
                .interactive(),
            width="stretch",
        )

    with col_class:
        st.markdown("**Activity class breakdown**")
        counts = eda_filt["activity_zone"].value_counts().reset_index()
        counts.columns = ["Activity", "Count"]
        counts["Label"] = counts["Count"].apply(lambda c: f"{c:,}")
        # preserve consistent zone order
        counts["_order"] = counts["Activity"].map({"Active": 0, "Intermediate": 1, "Inactive": 2})
        counts = counts.sort_values("_order").drop(columns="_order")
        count_max = max(1, int(counts["Count"].max() * 1.25)) if not counts.empty else 1
        bar = (
            alt.Chart(counts)
            .mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
            .encode(
                alt.Y("Activity:N", sort=_ZONE_DOMAIN, title=None),
                alt.X("Count:Q", title="Compounds",
                       scale=alt.Scale(domain=[0, count_max])),
                alt.Color("Activity:N",
                          scale=alt.Scale(domain=_filt_domain, range=_filt_range),
                          legend=None),
                tooltip=["Activity:N", "Count:Q"],
            )
        )
        text = bar.mark_text(align="left", dx=6, baseline="middle").encode(
            text="Label:N", color=alt.value("white"))
        st.altair_chart(
            (bar + text).properties(height=280).configure_axisY(grid=False),
            width="stretch",
        )

st.divider()

# ── Section 2: Lipinski Ro5 ───────────────────────────────────────────────────
st.subheader("Lipinski Rule of Five (Ro5)")
ro5_df = load_ro5().copy()
if not ro5_df.empty:
    if "pchembl_value" in ro5_df.columns:
        ro5_df["activity_zone"] = ro5_df["pchembl_value"].apply(_zone)
    else:
        ro5_df["activity_zone"] = ro5_df["activity_label"]  # fallback: binary label until cache refreshes
    ro5_filt = ro5_df[ro5_df["activity_zone"].isin(activity_filter)]

    with st.container(border=True):
        prop_cols = st.columns(4)
        prop_info = [("mw", "MW (Da)", 500, ":material/weight:"), ("logp", "LogP", 5, ":material/water_drop:"),
                     ("hbd", "HBD", 5, ":material/arrow_upward:"), ("hba", "HBA", 10, ":material/arrow_downward:")]
        for col_widget, (prop, label, limit, icon) in zip(prop_cols, prop_info):
            median_val = ro5_df[prop].median()
            pct_ok     = round(100 * (ro5_df[prop] <= limit).mean(), 1)
            col_widget.metric(label, f"{median_val:.1f}",
                              help=f"{pct_ok}% of compounds satisfy {label} \u2264 {limit}",
                              icon=icon)

    col_scatter, col_comply = st.columns(2)
    with col_scatter:
        st.markdown("**MW vs LogP  \u00b7  coloured by activity**")
        _lp_pad   = (ro5_df["logp"].max() - ro5_df["logp"].min()) * 0.1
        _mw_pad   = (ro5_df["mw"].max()   - ro5_df["mw"].min())   * 0.1
        _logp_dom = [ro5_df["logp"].min() - _lp_pad, ro5_df["logp"].max() + _lp_pad]
        _mw_dom   = [ro5_df["mw"].min()   - _mw_pad, ro5_df["mw"].max()   + _mw_pad]
        _ro5_sample = ro5_filt.sample(min(len(ro5_filt), 800), random_state=42) if not ro5_filt.empty else ro5_filt
        scatter = (
            alt.Chart(_ro5_sample)
            .mark_circle(size=40, opacity=0.55)
            .encode(
                alt.X("logp:Q", title="LogP", scale=alt.Scale(domain=_logp_dom)),
                alt.Y("mw:Q",   title="MW (Da)", scale=alt.Scale(domain=_mw_dom)),
                alt.Color("activity_zone:N",
                          scale=alt.Scale(domain=_filt_domain, range=_filt_range),
                          legend=alt.Legend(title="Activity")),
                tooltip=["mw:Q", "logp:Q", "hbd:Q", "hba:Q", "tpsa:Q", "activity_zone:N"],
            )
        )
        vline = alt.Chart(pd.DataFrame({"x": [5.0]})).mark_rule(
            color="#666", strokeDash=[4, 4], strokeWidth=1).encode(x="x:Q")
        hline = alt.Chart(pd.DataFrame({"y": [500.0]})).mark_rule(
            color="#666", strokeDash=[4, 4], strokeWidth=1).encode(y="y:Q")
        st.altair_chart(
            (scatter + vline + hline).properties(height=280).interactive(),
            width="stretch",
        )
        st.caption("Ro5-compliant space: lower-left quadrant of the dashed lines")

    with col_comply:
        st.markdown("**Compounds passing each Ro5 rule**")
        rules = {
            "MW \u2264 500": ro5_df["mw"] <= 500,
            "LogP \u2264 5": ro5_df["logp"] <= 5,
            "HBD \u2264 5":  ro5_df["hbd"] <= 5,
            "HBA \u2264 10": ro5_df["hba"] <= 10,
        }
        comply_rows = []
        for rule_name, mask in rules.items():
            for grp in [z for z in _ZONE_DOMAIN if z in activity_filter]:
                sub = ro5_df[ro5_df["activity_zone"] == grp]
                cnt = int(mask[sub.index].sum()) if len(sub) else 0
                comply_rows.append({"Rule": rule_name, "Activity": grp, "Count": cnt, "Total": len(sub)})
        comply_df = pd.DataFrame(comply_rows)
        _max_count = comply_df["Count"].max() if not comply_df.empty else 1
        bar_comply = (
            alt.Chart(comply_df)
            .mark_bar()
            .encode(
                alt.X("Count:Q", title="Compounds passing",
                      scale=alt.Scale(domain=[0, int(_max_count * 1.18)], nice=False)),
                alt.Y("Rule:N", title=None, sort=list(rules.keys())),
                alt.Color("Activity:N",
                          scale=alt.Scale(domain=_filt_domain, range=_filt_range),
                          legend=alt.Legend(title="Activity", orient="right")),
                alt.YOffset("Activity:N"),
                tooltip=["Rule:N", "Activity:N", "Count:Q", "Total:Q"],
            )
        )
        comply_df["Label"] = comply_df["Count"].apply(lambda c: f"{c:,}")
        text_comply = (
            alt.Chart(comply_df)
            .mark_text(align="left", baseline="middle", dx=4, fontSize=10)
            .encode(
                alt.X("Count:Q"),
                alt.Y("Rule:N", sort=list(rules.keys())),
                alt.YOffset("Activity:N"),
                text=alt.Text("Label:N"),
                color=alt.value("white"),
            )
        )
        st.altair_chart(
            (bar_comply + text_comply)
                .properties(height=280)
                .configure_axisY(grid=False)
                .configure_legend(orient="right"),
            width="stretch",
        )

st.divider()

# ── Section 3: Model performance ──────────────────────────────────────────────
st.subheader("ML Model Performance")
metrics = load_metrics()
pred_df = load_predictions().copy()
residuals = (pred_df["pred_pic50"] - pred_df["actual_pic50"]) if not pred_df.empty else None
_oob_r2  = metrics.get("oob_r2") if metrics else None
_oob_rmse = (
    f"{math.sqrt((1 - _oob_r2) * eda_df['pchembl_value'].var()):.3f}"
    if _oob_r2 is not None and not eda_df.empty else "—"
)
with st.container(border=True):
    col_model, col_tr2, col_r2, col_trmse, col_rmse, col_feat = st.columns(6)
    col_model.metric("ML Model", "RF",         icon=":material/account_tree:")
    col_tr2.metric("Train R\u00b2",   f"{metrics.get('oob_r2', 0):.3f}" if metrics else "—",
                   help="OOB (out-of-bag) estimate", icon=":material/trending_up:")
    col_r2.metric("Test R\u00b2",     f"{metrics.get('test_r2', 0):.3f}" if metrics else "—",  icon=":material/trending_up:")
    col_trmse.metric("Train RMSE", _oob_rmse, help="OOB estimate, log units",             icon=":material/straighten:")
    col_rmse.metric("Test RMSE",  f"{metrics.get('test_rmse', 0):.3f}" if metrics else "—",
                    help="log units", icon=":material/straighten:")
    col_feat.metric("Features",   f"{metrics.get('n_bits', 0):,}" if metrics else "—",
                    help="ECFP4 bits", icon=":material/tag:")

if not pred_df.empty:
    pred_df["activity_zone"] = pred_df["actual_pic50"].apply(_zone)
    pred_filt = pred_df[pred_df["activity_zone"].isin(activity_filter)]

    col_scatter2, col_resid = st.columns(2)
    with col_scatter2:
        pred_plot = pred_filt.sample(min(len(pred_filt), 800), random_state=0).copy() if not pred_filt.empty else pred_filt.copy()
        pred_plot["residual_abs"] = (pred_plot["pred_pic50"] - pred_plot["actual_pic50"]).abs()
        if show_ic50:
            pred_plot["actual_ic50"] = 10 ** (9 - pred_plot["actual_pic50"])
            pred_plot["pred_ic50"]   = 10 ** (9 - pred_plot["pred_pic50"])
            ic50_domain = [0.001, 1_000_000]
            diag_data   = pd.DataFrame({"x": ic50_domain, "y": ic50_domain})
            st.markdown("**Actual vs Predicted IC\u2085\u2080 (nM)**")
            scatter2 = (
                alt.Chart(pred_plot)
                .mark_circle(size=35, opacity=0.85)
                .encode(
                    alt.X("actual_ic50:Q", title="Actual IC\u2085\u2080 (nM)",
                          scale=alt.Scale(type="log", domain=ic50_domain),
                          axis=alt.Axis(values=[0.001, 0.1, 10, 1000, 100000])),
                    alt.Y("pred_ic50:Q",   title="Predicted IC\u2085\u2080 (nM)",
                          scale=alt.Scale(type="log", domain=ic50_domain),
                          axis=alt.Axis(values=[0.001, 0.1, 10, 1000, 100000])),
                    alt.Color("activity_zone:N",
                              scale=alt.Scale(domain=_filt_domain, range=_filt_range),
                              legend=alt.Legend(title="Activity")),
                    tooltip=["actual_pic50:Q", "pred_pic50:Q",
                             alt.Tooltip("actual_ic50:Q", format=".2f", title="Actual IC\u2085\u2080 (nM)"),
                             alt.Tooltip("pred_ic50:Q",   format=".2f", title="Pred IC\u2085\u2080 (nM)"),
                             alt.Tooltip("residual_abs:Q", format=".3f", title="|pIC50 error|"),
                             "activity_zone:N"],
                )
            )
        else:
            pic50_domain = [3, 12]
            diag_data    = pd.DataFrame({"x": pic50_domain, "y": pic50_domain})
            st.markdown("**Actual vs Predicted pIC50 (training and test combined)**")
            scatter2 = (
                alt.Chart(pred_plot)
                .mark_circle(size=35, opacity=0.85)
                .encode(
                    alt.X("actual_pic50:Q", title="Actual pIC50",
                          scale=alt.Scale(domain=pic50_domain),
                          axis=alt.Axis(tickCount=5)),
                    alt.Y("pred_pic50:Q",   title="Predicted pIC50",
                          scale=alt.Scale(domain=pic50_domain),
                          axis=alt.Axis(tickCount=5)),
                    alt.Color("activity_zone:N",
                              scale=alt.Scale(domain=_filt_domain, range=_filt_range),
                              legend=alt.Legend(title="Activity")),
                    tooltip=["actual_pic50:Q", "pred_pic50:Q",
                             alt.Tooltip("residual_abs:Q", format=".3f", title="|error|"),
                             "activity_zone:N"],
                )
            )
        diag = (
            alt.Chart(diag_data)
            .mark_line(color="#999", strokeDash=[5, 3], strokeWidth=1.5)
            .encode(x="x:Q", y="y:Q")
        )
        st.altair_chart(
            (scatter2 + diag).properties(height=280).interactive(),
            width="stretch",
        )

    with col_resid:
        st.markdown("**Residual distribution**")
        resid_df   = pd.DataFrame({"residual": residuals})
        resid_hist = (
            alt.Chart(resid_df)
            .mark_bar(color="#5D3FD3", opacity=0.75)
            .encode(
                alt.X("residual:Q", bin=alt.Bin(maxbins=40),
                      title="Predicted \u2212 Actual (log units)"),
                alt.Y("count():Q", title="Compounds"),
                tooltip=[alt.Tooltip("residual:Q", bin=True, title="residual bin"),
                         alt.Tooltip("count():Q", title="Count")],
            )
        )
        zero_rule = (
            alt.Chart(pd.DataFrame({"x": [0]}))
            .mark_rule(color="red", strokeDash=[5, 3], strokeWidth=1.5)
            .encode(x="x:Q")
        )
        st.altair_chart(
            (resid_hist + zero_rule).properties(height=280).interactive(),
            width="stretch",
        )

st.divider()

# ── Section 4: Feature importance ─────────────────────────────────────────────
st.subheader("Feature Importance")
feat_df = load_top_features()
if not feat_df.empty:
    col_feat_chart, col_feat_info = st.columns([3, 2])
    with col_feat_chart:
        feat_chart = (
            alt.Chart(feat_df)
            .mark_bar()
            .encode(
                alt.X("importance:Q", title="Regression impurity importance"),
                alt.Y("bit:N", sort="-x", title="ECFP4 bit"),
                alt.Color("importance:Q",
                          scale=alt.Scale(range=["#fce4ec", "#e040fb"]),
                          legend=None),
                tooltip=["bit:N", alt.Tooltip("importance:Q", format=".4f")],
            )
            .properties(height=max(340, len(feat_df) * 22 + 40))
        )
        st.altair_chart(feat_chart, width="stretch")
    with col_feat_info:
        with st.expander("What are ECFP4 bits?", expanded=True):
            st.markdown(
                "ECFP4 (Extended-Connectivity Fingerprint, diameter 4) encodes "
                "each atom's circular chemical neighbourhood out to a radius of "
                "2 bonds. Every unique environment is hashed to one of 1,024 "
                "binary positions. The model uses the 178 bits that carried "
                "information across the training set."
            )
        with st.expander("Reading the chart"):
            st.markdown(
                "Regression impurity importance (mean decrease in variance) measures "
                "how much a bit reduces training-set variance when the Random "
                "Forest splits on it. A higher bar = that structural fragment "
                "was more useful for splitting the training data across all 500 trees. "
                "It is not a local contribution to any one prediction."
            )
        with st.expander("Aromatase inhibitor pharmacophore"):
            st.markdown(
                "CYP19A1 inhibitors are non-steroidal (N-heterocycles such as "
                "triazole/imidazole coordinate the heme iron; e.g. anastrozole, "
                "letrozole) or steroidal (modified androstane A-ring; e.g. "
                "exemestane). High-importance bits typically encode aromatic "
                "N-heterocycles, nitrile groups, aryl halogens, and steroid scaffolds."
            )
        with st.expander("Bit interpretation note"):
            st.markdown(
                "ECFP4 bit indices are implementation-specific hashes. "
                "To decode a specific bit (e.g. bit_319) to its exact SMARTS "
                "substructure, run CDK's fingerprint on the training set and "
                "inspect which molecular environments map to that index."
            )
else:
    st.info("Feature importance data not available.")
