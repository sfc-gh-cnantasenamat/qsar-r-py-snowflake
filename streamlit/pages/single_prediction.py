"""Single Prediction page — draw or type a SMILES and predict pIC50."""

import altair as alt
import pandas as pd
import streamlit as st
from streamlit_jsme import st_jsme

from _shared import EXAMPLES, smiles_to_image, smiles_to_names, predict_pic50, log_prediction

_ALL_CLASSES = ["Active", "Intermediate", "Inactive"]

# ── Sidebar controls (only visible on this page) ──────────────────────────────
with st.sidebar:
    with st.expander(":material/settings: Prediction settings", expanded=True):
        input_method = st.radio(
            "Input method",
            ["SMILES text", "Draw structure"],
            horizontal=True,
            key="input_method",
        )
        st.markdown("")
        example_choice = st.radio(
            "Try an example molecule:",
            options=["(none)"] + list(EXAMPLES.keys()),
            key="example_choice",
        )

# Read shared settings
threshold       = st.session_state.get("threshold", (6.0, 7.0))
threshold_lo    = threshold[0]
threshold_hi    = threshold[1]
show_ic50       = st.session_state.get("show_ic50", False)
activity_filter = st.session_state.get("activity_filter", _ALL_CLASSES)

active_smiles = "" if example_choice == "(none)" else EXAMPLES[example_choice]

st.title(":material/biotech: Single Prediction")
st.caption(
    "Draw or type a SMILES string to predict the pIC50 and "
    "90% confidence interval for a single compound."
)

# ── Input container ───────────────────────────────────────────────────────────
st.subheader("Input")
with st.container(border=True):
    st.markdown("")

    if input_method == "SMILES text":
        if example_choice == "(none)":
            st.session_state["smiles_field"] = ""
        else:
            st.session_state["smiles_field"] = active_smiles
        smiles_input = st.text_input(
            "SMILES",
            key="smiles_field",
            placeholder="Paste or type SMILES notation here…",
        )
    else:
        if active_smiles != st.session_state.get("_draw_example", "__unset__"):
            st.session_state["jsme_mol_input"] = active_smiles
            st.session_state["_draw_example"]  = active_smiles

        molecule = st.text_input(
            "Load SMILES into editor",
            key="jsme_mol_input",
            placeholder="Paste a SMILES or pick an example above…",
        )
        smiles_input = st_jsme(molecule, height=460, key="jsme_draw")
        if smiles_input:
            st.caption(f"SMILES: `{smiles_input}`")

    predict_btn = st.button("Predict pIC50", type="primary", disabled=not smiles_input)

# ── Prediction output ─────────────────────────────────────────────────────────
if predict_btn and smiles_input:
    with st.spinner("Running prediction…"):
        pred = predict_pic50(smiles_input.strip())

    if pred and pred["pic50"] is not None:
        pic50  = pred["pic50"]
        ci_lo  = pred["ci_lo"]
        ci_hi  = pred["ci_hi"]
        if pic50 >= threshold_hi:
            act = "Active"
        elif pic50 >= threshold_lo:
            act = "Intermediate"
        else:
            act = "Inactive"
        ic50_nm = round(10 ** (9 - pic50), 1)
        ci_str  = f"[{ci_lo:.2f}, {ci_hi:.2f}]" if ci_lo is not None else "N/A"

        st.divider()
        st.subheader("Output")

        if act not in activity_filter:
            st.info(
                f"The predicted class (**{act}**) is currently filtered out. "
                "Adjust the activity filter in the sidebar to view results."
            )
        else:
            with st.container(border=True):
                col_struct, col_result = st.columns([1, 1])

                with col_struct:
                    st.markdown("#### Compound")
                    mol_img = smiles_to_image(smiles_input.strip())
                    if mol_img:
                        st.image(mol_img)
                    names = smiles_to_names(smiles_input.strip())
                    if names["common"] or names["iupac"]:
                        if names["common"]:
                            st.markdown("**Common name**")
                            st.badge(names["common"], color="blue")
                        if names["iupac"]:
                            st.markdown("**IUPAC name**")
                            st.badge(names["iupac"], color="violet")
                    else:
                        st.caption("Name not found in PubChem")
                    st.markdown(f"**SMILES:** `{smiles_input}`")

                with col_result:
                    st.markdown("#### Prediction")
                    if show_ic50:
                        ic50_ci_lo  = round(10 ** (9 - ci_hi), 1) if ci_hi is not None else None
                        ic50_ci_hi  = round(10 ** (9 - ci_lo), 1) if ci_lo is not None else None
                        ic50_ci_str = f"[{ic50_ci_lo:,.1f}, {ic50_ci_hi:,.1f}]" if ic50_ci_lo else "N/A"

                    # Narrative caption above CI chart
                    if ci_lo is not None and ci_hi is not None:
                        if show_ic50 and ic50_ci_lo is not None:
                            st.caption(f"The model estimates the IC\u2085\u2080 lies between {ic50_ci_lo:,.1f} nM and {ic50_ci_hi:,.1f} nM with 90% confidence.")
                        else:
                            st.caption(f"The model estimates the pIC50 lies between {ci_lo:.2f} and {ci_hi:.2f} with 90% confidence.")

                    # CI range chart
                    if ci_lo is not None and ci_hi is not None:
                        _pad   = max((ci_hi - ci_lo) * 0.4, 0.3)
                        _x_min = max(3.0, ci_lo - _pad)
                        _x_max = min(12.0, ci_hi + _pad)
                        _ci_df = pd.DataFrame([{"lo": ci_lo, "pred": pic50, "hi": ci_hi}])
                        _act_color = "#e040fb" if act == "Active" else ("#5D3FD3" if act == "Intermediate" else "#00bcd4")
                        _ci_bar = (
                            alt.Chart(_ci_df)
                            .mark_bar(height=24, cornerRadius=5, color=_act_color, opacity=0.4)
                            .encode(
                                alt.X("lo:Q", scale=alt.Scale(domain=[_x_min, _x_max]), title="pIC50"),
                                alt.X2("hi:Q"),
                                alt.Y(value=0),
                                tooltip=[
                                    alt.Tooltip("lo:Q",   title="pIC50 low",  format=".2f"),
                                    alt.Tooltip("hi:Q",   title="pIC50 high", format=".2f"),
                                    alt.Tooltip("pred:Q", title="pIC50",      format=".2f"),
                                ],
                            )
                        )
                        _ci_point = (
                            alt.Chart(_ci_df)
                            .mark_circle(size=90, color=_act_color)
                            .encode(alt.X("pred:Q"), alt.Y(value=0))
                        )
                        _thresh_df = pd.DataFrame([
                            {"x": threshold_lo, "label": f"inactive < {threshold_lo:.1f}"},
                            {"x": threshold_hi, "label": f"active \u2265 {threshold_hi:.1f}"},
                        ])
                        _thresh = (
                            alt.Chart(_thresh_df)
                            .mark_rule(strokeDash=[5, 3], strokeWidth=1.5)
                            .encode(
                                alt.X("x:Q"),
                                alt.Color("label:N",
                                          scale=alt.Scale(
                                              domain=[f"inactive < {threshold_lo:.1f}",
                                                      f"active \u2265 {threshold_hi:.1f}"],
                                              range=["#00bcd4", "#e040fb"]),
                                          legend=alt.Legend(orient="bottom", title=None)),
                            )
                        )
                        st.altair_chart(
                            (_ci_bar + _ci_point + _thresh)
                            .properties(height=50)
                            .configure_axis(grid=False)
                            .configure_view(strokeWidth=0),
                            width="stretch",
                        )

                    if show_ic50:
                        st.metric("Predicted IC\u2085\u2080", f"{ic50_nm:,.1f} nM", icon=":material/biotech:")
                        st.caption(f"pIC50 = {pic50:.2f}")
                    else:
                        st.metric("Predicted pIC50", f"{pic50:.2f}", icon=":material/biotech:")

                    st.markdown("#### Activity")
                    if act == "Active":
                        st.success(f"**{act}**")
                    elif act == "Intermediate":
                        st.warning(f"**{act}**")
                    else:
                        st.error(f"**{act}**")

        log_prediction(smiles_input.strip(), pred)
