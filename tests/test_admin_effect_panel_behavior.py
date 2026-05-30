from pathlib import Path


SERVER_ROOT = Path(__file__).resolve().parents[1]
ADMIN_JS = SERVER_ROOT / "static" / "admin" / "admin.js"
ADMIN_CSS = SERVER_ROOT / "static" / "admin" / "admin.css"
ADMIN_HTML = SERVER_ROOT / "templates" / "admin" / "index.html"


def test_effect_checkbox_handlers_do_not_collapse_panels():
    source = ADMIN_JS.read_text(encoding="utf-8")

    assert 'classList.toggle("hidden", !e.target.checked)' not in source


def test_effect_panels_have_separate_collapse_control():
    source = ADMIN_JS.read_text(encoding="utf-8")

    assert "effect-title-toggle" in source
    assert "matches('input[type=\"checkbox\"]')" in source
    assert "toggleEffectPanelCollapsed" in source
    assert "effect-collapse-toggle" not in source


def test_effect_groups_do_not_change_style_on_hover():
    source = ADMIN_CSS.read_text(encoding="utf-8")

    assert ".effect-group:hover" not in source


def test_effect_checkboxes_override_global_hover_style():
    source = ADMIN_CSS.read_text(encoding="utf-8")

    assert 'input[type="checkbox"]:checked:hover:not(:disabled)::after' in source
    assert '.effect-header input[type="checkbox"]:hover:not(:disabled)' in source
    assert '.effect-header input[type="checkbox"]:checked:hover:not(:disabled)' in source
    assert '.effect-header input[type="checkbox"]:checked::after' in source
    assert '.effect-header input[type="checkbox"]:checked:hover:not(:disabled)::after' in source


def test_effect_checkbox_changes_show_loading_overlay():
    js = ADMIN_JS.read_text(encoding="utf-8")
    html = ADMIN_HTML.read_text(encoding="utf-8")

    assert 'id="effectUpdateOverlay"' in html
    assert "setEffectUpdateLoading" in js
    assert "showLoading: true" in js
