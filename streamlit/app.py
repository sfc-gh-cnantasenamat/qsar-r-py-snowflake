"""
QSAR Aromatase Inhibitor Predictor — entry point.
Initialises the Snowflake session and routes to pages.
"""

import streamlit as st
from _shared import _DB_SCHEMA, init_session

st.set_page_config(
    page_title="QSAR Aromatase Predictor",
    page_icon=":material/biotech:",
    layout="wide",
)

# Initialise Snowflake session once per browser session
session = init_session()

try:
    agent_available = bool(session.sql(
        f"SHOW AGENTS LIKE 'AROMATASE_AGENT' IN SCHEMA {_DB_SCHEMA}"
    ).collect())
except Exception:
    agent_available = False

_ALL_CLASSES = ["Active", "Intermediate", "Inactive"]

with st.sidebar:
    st.title(":material/science: AromatasePred")
    st.caption(
        "Predict aromatase inhibitory activity (pIC50) of small molecules "
        "using a Random Forest model trained on Human aromatase (CYP19A1) [ChEMBL1978]."
    )
    with st.expander(":material/tune: Bioactivity settings", expanded=True):
        st.slider(
            "Activity threshold (pIC50)", min_value=4.0, max_value=11.0,
            value=(6.0, 7.0), step=0.1,
            help="Lower bound = Inactive/Intermediate boundary; upper bound = Intermediate/Active boundary.",
            key="threshold",
        )
        st.toggle("Show IC\u2085\u2080 (nM)", value=False, key="show_ic50")
        st.multiselect(
            "Show activity classes",
            options=_ALL_CLASSES,
            default=_ALL_CLASSES,
            key="activity_filter",
        )

# ── Navigation ─────────────────────────────────────────────────────────────────
pages = [
    st.Page("pages/dashboard.py",         title="Dashboard",         icon=":material/dashboard:"),
    st.Page("pages/single_prediction.py", title="Single Prediction", icon=":material/biotech:"),
    st.Page("pages/batch_prediction.py",  title="Batch Prediction",  icon=":material/table:"),
]
if agent_available:
    pages.append(st.Page("pages/ask_agent.py", title="Ask an Agent", icon=":material/forum:"))
pages.append(st.Page("pages/about.py", title="About", icon=":material/info:"))
pg = st.navigation(pages, position="top")
pg.run()
