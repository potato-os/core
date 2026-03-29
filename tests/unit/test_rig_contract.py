"""Contract tests for the RIG architecture pattern — rig.md template and step envelope."""

from pathlib import Path

import pytest

_SKELETON_RIG = Path(__file__).parent.parent.parent / "apps" / "skeleton" / "rig.md"


# ---------------------------------------------------------------------------
# Template structural tests
# ---------------------------------------------------------------------------


def test_skeleton_rig_template_exists():
    assert _SKELETON_RIG.exists(), "apps/skeleton/rig.md must exist"


def test_skeleton_rig_has_required_sections():
    content = _SKELETON_RIG.read_text()
    required = [
        "## Workflow Overview",
        "## Step Catalog",
        "## Flow Graph",
        "## Step Envelope Contract",
        "## Schema References",
    ]
    for heading in required:
        assert heading in content, f"Missing section: {heading}"


def test_skeleton_rig_documents_app_json_relationship():
    content = _SKELETON_RIG.read_text()
    assert "app.json" in content, "rig.md must document the relationship to app.json"


def test_skeleton_rig_step_catalog_has_ts_rows():
    """Skeleton has inferno: false — catalog shows TS steps only."""
    content = _SKELETON_RIG.read_text().lower()
    assert "| ts " in content or "| ts|" in content, "Step catalog must include a ts-type row"


def test_skeleton_rig_documents_both_step_types():
    """Template documents both MS and TS types even though skeleton only uses TS."""
    content = _SKELETON_RIG.read_text()
    assert '"ms"' in content or "= Model Step" in content, "Template must document the ms type"
    assert '"ts"' in content or "= Tool Step" in content, "Template must document the ts type"


# ---------------------------------------------------------------------------
# Envelope validation tests
# ---------------------------------------------------------------------------


def _valid_envelope(**overrides):
    base = {
        "step_id": "check_status",
        "type": "ts",
        "result": {"ok": True},
        "next": {"mode": "direct", "step_id": "apply_mode", "args": {}},
    }
    base.update(overrides)
    return base


def test_valid_envelope_passes():
    from core.rig_envelope import validate_envelope

    assert validate_envelope(_valid_envelope()) == []


def test_valid_envelope_terminal_step():
    from core.rig_envelope import validate_envelope

    assert validate_envelope(_valid_envelope(next=None)) == []


@pytest.mark.parametrize("field", ["step_id", "type", "result", "next"])
def test_envelope_missing_required_field(field):
    from core.rig_envelope import validate_envelope

    env = _valid_envelope()
    del env[field]
    errors = validate_envelope(env)
    assert any(field in e for e in errors), f"Expected error mentioning '{field}'"


def test_envelope_invalid_type():
    from core.rig_envelope import validate_envelope

    errors = validate_envelope(_valid_envelope(type="unknown"))
    assert any("type" in e for e in errors)


def test_envelope_result_must_be_dict():
    from core.rig_envelope import validate_envelope

    errors = validate_envelope(_valid_envelope(result="not a dict"))
    assert any("result" in e for e in errors)


def test_envelope_next_direct_requires_step_id():
    from core.rig_envelope import validate_envelope

    errors = validate_envelope(_valid_envelope(next={"mode": "direct", "args": {}}))
    assert any("step_id" in e for e in errors)


def test_envelope_next_model_requires_prompt_id():
    from core.rig_envelope import validate_envelope

    errors = validate_envelope(_valid_envelope(next={"mode": "model", "inputs": {}}))
    assert any("prompt_id" in e for e in errors)


def test_envelope_next_invalid_mode():
    from core.rig_envelope import validate_envelope

    errors = validate_envelope(_valid_envelope(next={"mode": "bogus"}))
    assert any("mode" in e for e in errors)


def test_envelope_unhashable_type_returns_error():
    from core.rig_envelope import validate_envelope

    errors = validate_envelope(_valid_envelope(type=["not", "a", "string"]))
    assert any("type" in e for e in errors)


def test_envelope_unhashable_next_mode_returns_error():
    from core.rig_envelope import validate_envelope

    errors = validate_envelope(_valid_envelope(next={"mode": {"nested": True}}))
    assert any("mode" in e for e in errors)
