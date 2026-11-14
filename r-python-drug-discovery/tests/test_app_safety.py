"""Focused checks for opt-in external lookups and prediction persistence."""

import ast
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "streamlit"


def test_pubchem_fallback_requires_opt_in():
    source = (ROOT / "_shared.py").read_text()
    module = ast.parse(source)
    image = next(node for node in module.body if isinstance(node, ast.FunctionDef)
                 and node.name == "smiles_to_image")
    code = compile(ast.Module(body=[image], type_ignores=[]), str(ROOT / "_shared.py"), "exec")

    fake_mol = object()
    chem = types.SimpleNamespace(MolFromSmiles=lambda smiles: fake_mol)
    calls = []
    requests = types.SimpleNamespace(get=lambda *args, **kwargs: calls.append((args, kwargs)))
    namespace = {"Chem": chem, "_RDKIT_DRAW": False, "requests": requests}
    exec(code, namespace)
    assert namespace["smiles_to_image"]("CC") is None
    assert calls == []


def test_single_prediction_logs_only_on_predict_click():
    source = (ROOT / "pages" / "single_prediction.py").read_text()
    module = ast.parse(source)
    top_level_logs = [node for node in module.body if isinstance(node, ast.Expr)
                      and isinstance(node.value, ast.Call)
                      and isinstance(node.value.func, ast.Name)
                      and node.value.func.id == "log_prediction"]
    assert not top_level_logs
    button_block = next(node for node in module.body if isinstance(node, ast.If)
                        and "predict_btn" in ast.unparse(node.test))
    assert "log_prediction(" in ast.unparse(button_block)


def test_get_predict_url_discovers_service_in_snowflake():
    source = (ROOT / "_shared.py").read_text()
    assert "<hash>" not in source
    assert "SHOW SERVICES LIKE 'QSAR_PLUMBER_SVC'" in source
