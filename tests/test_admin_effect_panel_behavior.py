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


def test_admin_uses_system_dialogs_instead_of_browser_alerts():
    js = ADMIN_JS.read_text(encoding="utf-8")
    html = ADMIN_HTML.read_text(encoding="utf-8")

    assert "systemDialog" in html
    assert "systemPrompt(" in js
    assert "window.prompt(" not in js
    assert "window.confirm(" not in js
    assert "alert(" not in js


def test_admin_sidebar_can_be_resized_and_remembers_width():
    js = ADMIN_JS.read_text(encoding="utf-8")
    css = ADMIN_CSS.read_text(encoding="utf-8")
    html = ADMIN_HTML.read_text(encoding="utf-8")

    assert 'id="sidebarResizeHandle"' in html
    assert "--sidebar-width" in css
    assert "grid-template-columns: var(--sidebar-width" in css
    assert "mockupStudio.sidebarWidth" in js
    assert "setPointerCapture" in js
    assert "localStorage.setItem(SIDEBAR_WIDTH_STORAGE_KEY" in js


def test_regular_mockup_switch_preloads_background_before_atomic_artwork_sync():
    js = ADMIN_JS.read_text(encoding="utf-8")

    assert "loadEditorBackgroundAtomically(template)" in js
    assert "preloadEditorBackgrounds(state.templates)" in js
    assert "|| new Image()" in js
    assert "preloadedBackground.onload = () => {" in js
    assert "$(\"canvasImage\").src = backgroundUrl;" in js
    assert "drawSelection();" in js


def test_green_frame_edit_mode_does_not_render_heavy_preview_on_selection_draw():
    js = ADMIN_JS.read_text(encoding="utf-8")
    green_branch = js.split("if (isGreenFrameTemplate(template)) {", 1)[1].split("return;", 1)[0]

    assert "refreshGreenFrameMockupPreview()" not in green_branch
    assert "greenFramePreviewTimeout" not in green_branch
    assert "selectionRenderedMockup" in green_branch


def test_green_frame_edit_mode_keeps_lightweight_artwork_overlay_visible():
    js = ADMIN_JS.read_text(encoding="utf-8")
    green_branch = js.split("if (isGreenFrameTemplate(template)) {", 1)[1].split("return;", 1)[0]

    assert "if (state.selectionStyle.overlayImage)" in green_branch
    assert "renderGreenFrameArtworkOverlay(template, image)" in green_branch
    assert "state.greenFramePlacementActive && state.selectionStyle.overlayImage" not in green_branch


def test_green_frame_edit_mode_uses_all_detected_regions_for_lightweight_overlay():
    js = ADMIN_JS.read_text(encoding="utf-8")

    assert "function greenFrameOverlayRegions(template)" in js
    assert "template.raw_artwork_area.regions" in js
    assert "green-frame-region-overlay" in js
    assert "greenFrameOverlayRegions(template).forEach" in js


def test_preview_mode_refreshes_after_green_frame_control_changes():
    js = ADMIN_JS.read_text(encoding="utf-8")
    body = js.split("function updateGreenFrameSettingsFromControls()", 1)[1].split("\n  [", 1)[0]

    assert "state.isPreviewingMockup" in body
    assert "refreshPreviewMockup()" in body
    assert "refreshGreenFrameMockupPreview()" in body


def test_preview_effect_updates_can_persist_while_preview_render_is_busy():
    js = ADMIN_JS.read_text(encoding="utf-8")

    assert "async function persistTemplateState(template, options = {})" in js
    assert "(state.busy && !options.force)" in js
    assert "persistTemplateState(state.selected, { force: state.isPreviewingMockup })" in js
