"""Shared utilities, constants, and cached data-loading functions."""

import json
import os
import math
import tempfile
from io import BytesIO

import requests
import pandas as pd
import streamlit as st
from rdkit import Chem
from rdkit.Chem import rdDepictor

try:
    from rdkit.Chem import Draw as RDKitDraw
    _RDKIT_DRAW = True
except (ImportError, OSError):
    _RDKIT_DRAW = False

from rdkit.Chem.inchi import MolToInchi, InchiToInchiKey

# ── Constants ─────────────────────────────────────────────────────────────────
# Database and schema holding the stage and audit table; change if you used other names
_DB_SCHEMA = "QSAR_DB.PUBLIC"
_STAGE = f"@{_DB_SCHEMA}.QSAR_STAGE"

EXAMPLES = {
    "FDA-approved — Letrozole (Femara)":     "N#Cc1ccc(cc1)C(n1cncn1)c1ccc(C#N)cc1",
    "FDA-approved — Anastrozole (Arimidex)": "CC(C)(C#N)c1cc(Cn2cncn2)cc(c1)C(C)(C)C#N",
    "FDA-approved — Exemestane (Aromasin)":  "C[C@]12CC[C@H]3[C@@H](CC(=C)C4=CC(=O)C=C[C@]34C)[C@@H]1CCC2=O",
    "Inactive — Aspirin":                    "CC(=O)Oc1ccccc1C(=O)O",
    "Inactive — Caffeine":                   "Cn1c(=O)c2c(ncn2C)n(C)c1=O",
}


def _secret(*path, default=None):
    """Read a nested Streamlit secret; Streamlit in Snowflake has no secrets.toml, so missing is normal."""
    try:
        node = st.secrets
        for key in path:
            node = node[key]
        return node
    except Exception:
        return default


def get_predict_url() -> str:
    return _secret("spcs", "predict_url",
                   default="http://qsar-plumber-svc.<hash>.svc.spcs.internal:5000/predict")


def get_spcs_token() -> str:
    # Only needed when calling the public ingress URL from outside Snowflake
    return _secret("spcs", "token") or _secret("connections", "snowflake", "password", default="")


def _create_session():
    try:
        from snowflake.snowpark.context import get_active_session
        st.session_state._sf_session = get_active_session()
    except Exception:
        st.session_state._sf_session = st.connection("snowflake").session()


def init_session():
    """Create (or recreate) the Snowflake session; validates liveness on each call."""
    if "_sf_session" not in st.session_state:
        _create_session()
    else:
        # Validate that the cached session is still alive; reconnect if not.
        try:
            st.session_state._sf_session.sql("SELECT 1").collect()
        except Exception:
            del st.session_state._sf_session
            _create_session()
    return st.session_state._sf_session


# ── Chemistry helpers ─────────────────────────────────────────────────────────

def smiles_to_image(smiles: str):
    """Return PNG bytes of the 2D structure. Uses RDKit if available, else PubChem."""
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    if _RDKIT_DRAW:
        try:
            rdDepictor.Compute2DCoords(mol)
            img = RDKitDraw.MolToImage(mol, size=(400, 300))
            buf = BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            pass
    try:
        resp = requests.get(
            "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/smiles/PNG",
            params={"smiles": smiles},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.content
    except Exception:
        pass
    return None


def smiles_to_names(smiles: str) -> dict:
    """Return {'iupac': str|None, 'common': str|None} via RDKit InChIKey → PubChem."""
    result = {"iupac": None, "common": None}
    try:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return result
        inchikey = InchiToInchiKey(MolToInchi(mol))
        resp = requests.get(
            f"https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/inchikey/{inchikey}/property/IUPACName,Title/JSON",
            timeout=10,
        )
        if resp.status_code == 200:
            props = resp.json()["PropertyTable"]["Properties"][0]
            result["iupac"]  = props.get("IUPACName") or None
            result["common"] = props.get("Title") or None
    except Exception:
        pass
    return result


# ── Inference ─────────────────────────────────────────────────────────────────

def predict_pic50(smiles: str) -> dict | None:
    try:
        token = get_spcs_token()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f'Snowflake Token="{token}"'
        resp = requests.post(
            get_predict_url(),
            headers=headers,
            json={"data": [[0, smiles]]},
            timeout=60,
        )
        resp.raise_for_status()
        rows = resp.json().get("data", [])
        if not rows:
            st.error("Service returned no results.")
            return None
        _, pic50, activity, ci_lo, ci_hi = rows[0]
        _activity = str(activity)
        if _activity == "Borderline":
            _activity = "Intermediate"
        return {
            "pic50":    float(pic50),
            "activity": _activity,
            "ci_lo":    float(ci_lo) if ci_lo is not None else None,
            "ci_hi":    float(ci_hi) if ci_hi is not None else None,
        }
    except requests.exceptions.ConnectionError:
        st.error("Cannot reach inference service — it may still be starting up. Wait ~2 minutes and try again.")
        return None
    except requests.exceptions.HTTPError as e:
        st.error(f"Service error {e.response.status_code}: {e.response.text[:200]}")
        return None
    except Exception as e:
        st.error(f"Prediction error: {e}")
        return None


def log_prediction(smiles: str, pred: dict):
    session = st.session_state.get("_sf_session")
    if session is None:
        return
    safe_smiles = smiles.replace("'", "''").replace("\\", "")[:4000]
    try:
        session.sql(
            f"INSERT INTO {_DB_SCHEMA}.QSAR_PREDICTIONS_LOG "
            f"(INPUT_SMILES, PREDICTED_PIC50, ACTIVITY_CLASS) "
            f"VALUES ('{safe_smiles}', {pred['pic50']:.6f}, '{pred['activity']}')"
        ).collect()
    except Exception:
        pass


# ── Stage / data loading ──────────────────────────────────────────────────────

def _stage_get(fname: str, tmpdir: str) -> str:
    session = st.session_state.get("_sf_session")
    if session is None:
        session = init_session()
    try:
        session.file.get(f"{_STAGE}/{fname}", tmpdir)
    except Exception:
        # Session expired — reconnect once and retry
        del st.session_state._sf_session
        session = init_session()
        session.file.get(f"{_STAGE}/{fname}", tmpdir)
    return os.path.join(tmpdir, fname)


@st.cache_data(ttl=3600)
def load_top_features():
    try:
        tmp = tempfile.mkdtemp()
        df = pd.read_csv(_stage_get("top_features.csv", tmp), header=0)
        df.columns = ["bit", "importance"]
        return df.head(20)
    except Exception as e:
        st.caption(f"Feature importance unavailable: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=3600)
def load_metrics():
    try:
        tmp = tempfile.mkdtemp()
        with open(_stage_get("training_metrics.json", tmp)) as f:
            return json.load(f)
    except Exception as e:
        st.caption(f"Metrics unavailable: {e}")
        return {}


@st.cache_data(ttl=3600)
def load_eda():
    try:
        tmp = tempfile.mkdtemp()
        return pd.read_csv(_stage_get("dashboard_eda.csv", tmp))
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=3600)
def load_ro5():
    try:
        tmp = tempfile.mkdtemp()
        df = pd.read_csv(_stage_get("dashboard_ro5.csv", tmp))
        return df  # v2: includes pchembl_value column
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=3600)
def load_predictions():
    try:
        tmp = tempfile.mkdtemp()
        return pd.read_csv(_stage_get("dashboard_predictions.csv", tmp))
    except Exception:
        return pd.DataFrame()
