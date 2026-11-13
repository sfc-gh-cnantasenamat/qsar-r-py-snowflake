"""Regression checks for source-grounded agent descriptions."""

from pathlib import Path


SPEC = Path(__file__).resolve().parents[1] / "agent" / "aromatase_agent.yaml"


def test_descriptors_must_come_from_tool_results():
    content = SPEC.read_text()
    assert "use the Ro5 fields in the" in content
    assert "Do not estimate HBA, HBD, LogP" in content
    assert "say it is unavailable" in content


def test_activity_label_is_not_a_separate_classifier():
    content = SPEC.read_text()
    assert "thresholded\n    output of the same regression prediction" in content
    assert "not a separate classifier" in content
    assert "Do\n    not claim classifier/regressor disagreement" in content