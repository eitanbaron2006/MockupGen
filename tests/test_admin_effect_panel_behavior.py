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


def test_mask_and_green_frame_templates_hide_polygon_and_disable_drag():
    js = ADMIN_JS.read_text(encoding="utf-8")

    # Verify isGreenFrameTemplate recognizes green frames, color pick, and frame points modes
    assert 'raw.mode === "color_pick" || raw.provider === "color_pick"' in js
    assert 'raw.mode === "frame_points" || raw.provider === "frame_points"' in js

    # Verify drawSelection hides SVG and polygon handles for mask/green frame templates
    green_branch = js.split("if (isGreenFrameTemplate(template)) {", 1)[1].split("return;", 1)[0]
    assert 'selectionSvg.classList.add("hidden")' in green_branch
    assert '$("selectionPolygon").classList.add("hidden")' in green_branch

    # Verify beginDrag disables manual polygon dragging for mask/green frame templates
    drag_body = js.split("function beginDrag(event) {", 1)[1].split("const target = event.target;", 1)[0]
    assert "if (isGreenFrameTemplate(state.selected)) return;" in drag_body


def test_green_frame_overlay_places_artwork_where_the_renderer_will():
    """The editor overlay is a promise about the finished mockup.

    It has to read the same green-frame settings the renderer does and lay the
    artwork out the same way: the frame's own corners, no perspective warp when
    the renderer draws upright, and the same fit box.
    """
    js = ADMIN_JS.read_text(encoding="utf-8")
    end_of_function = chr(10) + "  function "
    overlay = js.split("function renderGreenFrameArtworkOverlay(", 1)[1].split(end_of_function, 1)[0]
    placement = js.split("function greenFrameArtworkPlacement(", 1)[1].split(end_of_function, 1)[0]

    assert "greenFrameSettings(template.effects)" in overlay
    assert "greenFrameArtworkPlacement(corners, placementSettings)" in overlay
    # Perspective off: _draw_rect fills the region's upright bounding box.
    assert "settings.use_perspective === false" in placement
    # Perspective on: the frame's own corners, never a widened quad. The wide
    # coverage envelope bleeds the artwork's edge colour past the frame in the
    # renderer; it must not move the artwork the editor shows.
    assert ": corners.map((point) => ({ ...point }));" in placement
    assert "use_vector_clip" not in placement
    # The renderer sizes the artwork by the longer of each pair of opposite
    # sides (_render_perspective_region), not by the top and left edges.
    assert "Math.max(side(0, 1), side(3, 2))" in placement
    assert "Math.max(side(0, 3), side(1, 2))" in placement


def test_artwork_overlay_fills_the_frame_exactly_without_the_green_pipeline():
    """A frame that renders without a mask must bound its artwork exactly.

    Geometric multi-frame templates and single-frame detections composite
    straight onto their corners (_render_geometric_frames and the single-frame
    path), so the coverage envelope -- which only stays out of sight because a
    mask clips it -- must not widen the artwork past the frame there.
    """
    js = ADMIN_JS.read_text(encoding="utf-8")
    overlay = js.split("function renderGreenFrameArtworkOverlay(", 1)[1].split("\n  function ", 1)[0]
    pipeline = js.split("function usesGreenFramePipeline(", 1)[1].split("\n  function ", 1)[0]
    placement = js.split("function greenFrameArtworkPlacement(", 1)[1].split("\n  function ", 1)[0]

    assert "usesGreenFramePipeline(template) ? greenSettings : null" in overlay
    assert 'mode === "geometry" && multiRegion) return false' in pipeline
    assert '(provider === "vertex" || provider === "local") && !multiRegion) return false' in pipeline
    # Null settings: the artwork is fitted to the frame's own bounding box and
    # warped straight onto its corners.
    assert "if (!settings) {" in placement
    assert "quad: corners.map((point) => ({ ...point }))" in placement
    assert "Math.max(1, Math.round(right - left))" in placement
    assert "Math.max(1, Math.round(bottom - top))" in placement


def test_green_frame_control_changes_redraw_the_artwork_overlay():
    js = ADMIN_JS.read_text(encoding="utf-8")
    body = js.split("function updateGreenFrameSettingsFromControls()", 1)[1].split("\n  [", 1)[0]

    assert "drawSelection();" in body


def test_editor_waits_for_the_new_background_before_laying_out_overlays():
    """Overlays are scaled by the canvas image's own dimensions.

    Assigning a new src does not update naturalWidth/naturalHeight until the
    bitmap is swapped in, cached or not, so drawing on the next frame laid the
    artwork out with the previous template's aspect -- and nothing redrew it.
    """
    js = ADMIN_JS.read_text(encoding="utf-8")
    end_of_block = chr(10) + "    };"
    apply_background = js.split("const applyPreloadedBackground = () => {", 1)[1].split(end_of_block, 1)[0]
    guard = js.split("function drawSelection()", 1)[1].split("if (isGreenFrameTemplate(template))", 1)[0]

    assert "canvas.onload = () => {" in apply_background
    assert "canvas.src = backgroundUrl;" in apply_background
    assert "canvas.complete && canvas.naturalWidth > 0" in apply_background
    assert 'image.src.indexOf(`/templates/${template.template_id}/`) === -1' in guard
    assert 'image.addEventListener("load", () => drawSelection(), { once: true })' in guard


def test_overlay_falls_back_to_the_template_fit_mode_when_the_green_effect_has_none():
    """parse_green_frame_settings falls back to the template's own fit mode.

    The panel defaults carry "cover", so reading them instead of the effect
    cropped the sides off artwork on a template set to stretch -- the editor
    showed a crop the render never made.
    """
    js = ADMIN_JS.read_text(encoding="utf-8")
    end_of_function = chr(10) + "  function "
    overlay = js.split("function renderGreenFrameArtworkOverlay(", 1)[1].split(end_of_function, 1)[0]

    assert "template.effects.green_frame_mockups.fit_mode" in overlay
    assert "(placementSettings && greenFitMode) || templateFit" in overlay
    assert "placementSettings.fit_mode" not in overlay
