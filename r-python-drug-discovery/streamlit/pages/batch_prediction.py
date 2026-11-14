"""Batch Prediction page — predict pIC50 for multiple SMILES at once."""

import pandas as pd
import streamlit as st

from _shared import EXAMPLES, predict_pic50

_ALL_CLASSES = ["Active", "Intermediate", "Inactive"]

# Read shared settings
threshold = st.session_state.get("threshold", (6.0, 7.0))
show_ic50 = st.session_state.get("show_ic50", False)
activity_filter = st.session_state.get("activity_filter", _ALL_CLASSES)

st.title(":material/table: Batch Prediction")
st.caption(
    "Paste one SMILES per line to score multiple compounds at once "
    "and download the results as a table."
)
st.subheader("Input")
with st.container(border=True):
    st.markdown("Enter one SMILES per line:")
    batch_text = st.text_area(
        "SMILES (one per line)", height=150,
        value="\n".join(EXAMPLES.values()),
    )
    predict_all_btn = st.button("Predict All", type="primary")

if predict_all_btn:
    smiles_list = [s.strip() for s in batch_text.strip().split("\n") if s.strip()]
    if not smiles_list:
        st.warning("No SMILES provided.")
    else:
        results = []
        for smi in smiles_list:
            p = predict_pic50(smi)
            pic50 = p["pic50"] if p else None
            if pic50 is not None:
                if pic50 >= threshold[1]:
                    activity = "Active"
                elif pic50 >= threshold[0]:
                    activity = "Intermediate"
                else:
                    activity = "Inactive"
            else:
                activity = "Error"
            row = {
                "SMILES":          smi,
                "PREDICTED_PIC50": pic50,
                "ACTIVITY_CLASS":  activity,
                "CI_LOWER_95":     p["ci_lo"] if p else None,
                "CI_UPPER_95":     p["ci_hi"] if p else None,
            }
            if show_ic50 and pic50 is not None:
                row["IC50_NM"] = round(10 ** (9 - pic50), 1)
            results.append(row)

        df = pd.DataFrame(results)

        # Apply activity class filter
        if activity_filter:
            df = df[df["ACTIVITY_CLASS"].isin(activity_filter + ["Error"])]

        # Wrap activity class as single-item list for MultiselectColumn badge rendering
        df["ACTIVITY_CLASS"] = df["ACTIVITY_CLASS"].apply(lambda x: [x])

        st.divider()
        st.subheader("Output")
        st.dataframe(
            df,
            use_container_width=True,
            column_config={
                "ACTIVITY_CLASS": st.column_config.MultiselectColumn(
                    "Activity Class",
                    options=["Active", "Intermediate", "Inactive", "Error"],
                    color=["#e040fb", "#5D3FD3", "#00bcd4", "#9e9e9e"],
                    disabled=True,
                ),
                "PREDICTED_PIC50": st.column_config.ProgressColumn(
                    "Predicted pIC50",
                    format="%.2f",
                    min_value=4.0,
                    max_value=11.0,
                ),
                "CI_LOWER_95": st.column_config.ProgressColumn(
                    "CI Lower 95%",
                    format="%.2f",
                    min_value=4.0,
                    max_value=11.0,
                    color="gray",
                ),
                "CI_UPPER_95": st.column_config.ProgressColumn(
                    "CI Upper 95%",
                    format="%.2f",
                    min_value=4.0,
                    max_value=11.0,
                    color="gray",
                ),
            },
        )
