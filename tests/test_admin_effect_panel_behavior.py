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


def test_mask_backed_templates_edit_their_frames_like_any_other():
    """A frame is a frame however detection found it.

    GREEN FRAMES, COLOR PICK and FRAME POINTS all save their frames in
    raw_artwork_area.regions, and that is what the renderer reads, so those
    frames get the same polygon and corner handles a multi-frame template has.
    Only the whole-artwork-area rectangle stays out of it: a mask-backed
    template's geometry lives in its regions, not in that box.
    """
    js = ADMIN_JS.read_text(encoding="utf-8")

    # isGreenFrameTemplate recognises green frames, colour pick and frame points
    assert 'raw.mode === "color_pick" || raw.provider === "color_pick"' in js
    assert 'raw.mode === "frame_points" || raw.provider === "frame_points"' in js

    green_branch = js.split("if (isGreenFrameTemplate(template)) {", 1)[1].split("return;", 1)[0]
    assert "renderRegionFrames(template, maskedRegions, rect, maskedRegions.length > 1)" in green_branch
    assert '$("selectionPolygon").classList.add("hidden")' in green_branch

    # The region handles are read before anything bails out for a mask-backed
    # template, and the whole-area drag is still refused after them.
    drag_body = js.split("function beginDrag(event) {", 1)[1].split("let handle = \"move\";", 1)[0]
    assert "target.dataset.regionIndex !== undefined" in drag_body
    assert drag_body.index("target.dataset.regionIndex") < drag_body.index("isGreenFrameTemplate(template)")
    assert "if (isGreenFrameTemplate(template)) return;" in drag_body


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


def test_a_mask_that_cannot_load_never_hides_the_artwork():
    """A CSS mask that 404s masks everything out.

    The geometry wizard named a mask.png its template does not have, so during
    a detection review every frame came back empty and the artwork only
    returned once the detection was approved. Geometric frames carry no mask,
    and a mask that fails to load is dropped rather than left hiding what it
    was applied to.
    """
    js = ADMIN_JS.read_text(encoding="utf-8")
    end_of_function = chr(10) + "  function "
    wizard = js.split("async function runStage1Geometry(", 1)[1].split(end_of_function, 1)[0]
    mask = js.split("function applyOverlayMask(", 1)[1].split(end_of_function, 1)[0]

    assert 'mask_name: payload.template?.mask_name || "mask.png"' not in wizard
    assert "mask_name: payload.template?.mask_name || null" in wizard
    assert 'maskAvailability.get(maskUrl) === "missing"' in mask
    assert "probe.onerror" in mask


def test_detection_leaves_preview_mode_before_it_draws():
    """The editor cannot draw while a rendered preview is on screen.

    drawSelection returns early in preview mode, so running a detection from
    there cleared the preview and then drew nothing: the detected frames sat on
    a blank canvas with no artwork in them until the result was approved.
    """
    js = ADMIN_JS.read_text(encoding="utf-8")
    detect = js.split("async function detectFrame() {", 1)[1].split("saveDetectionPreState();", 1)[0]
    draw = js.split("function drawSelection() {", 1)[1].split("const template = state.selected;", 1)[0]

    assert "if (state.isPreviewingMockup) await togglePreviewMode();" in detect
    # The early return is why leaving preview mode has to come first.
    assert "if (state.isPreviewingMockup) {" in draw


def test_the_download_button_never_points_at_an_empty_href():
    """A link with no href downloads the page itself.

    It arrives named mockup.png and will not open, which is what a failed
    render used to produce: the button was re-enabled with nothing behind it.
    And a rendered mockup runs to megabytes, so the link is a blob rather than
    a data: URL the browser would have to swallow whole.
    """
    js = ADMIN_JS.read_text(encoding="utf-8")
    end_of_function = chr(10) + "  function "
    setter = js.split("function setDownloadTarget(", 1)[1].split(end_of_function, 1)[0]
    clearer = js.split("function clearDownloadTarget(", 1)[1].split(end_of_function, 1)[0]

    assert "URL.createObjectURL" in setter
    assert "URL.revokeObjectURL" in setter and "URL.revokeObjectURL" in clearer
    assert 'button.removeAttribute("href")' in clearer
    assert 'button.style.pointerEvents = "none"' in clearer

    # Nothing assigns the href on its own any more.
    assert '$("downloadMockupButton").href =' not in js
    assert '$("toolbarDownloadButton").href =' not in js

    # A failed render disables the button instead of enabling an empty one.
    background = js.split("Background render failed:", 1)[1].split("} finally {", 1)[0]
    assert "clearDownloadTarget(" in background
    assert "style.pointerEvents = \"auto\"" not in background


def test_canvas_toolbars_are_vertical_draggable_and_dockable():
    """The zoom HUD and the style toolbar are one rail in two places.

    They share their chrome -- a slim, square, vertical bar -- and are moved by
    the grip at their head. Dragged near the left or right wall of the
    workspace they sit flush against it, and two on the same wall read as a
    single rail split by one line.
    """
    js = ADMIN_JS.read_text(encoding="utf-8")
    css = ADMIN_CSS.read_text(encoding="utf-8")
    html = ADMIN_HTML.read_text(encoding="utf-8")
    end_of_function = chr(10) + "  function "
    align = js.split("function alignDockedToolbars(", 1)[1].split(end_of_function, 1)[0]
    drag = js.split("function makeToolbarDraggable(", 1)[1].split(end_of_function, 1)[0]

    # A grip on each toolbar, and nothing else claims to be one.
    assert html.count("data-drag-handle") == 2
    assert 'makeToolbarDraggable($("zoomHud"), "zoomHud")' in js
    assert 'makeToolbarDraggable($("selectionStyleToolbar"), "selectionStyleToolbar")' in js

    # Both are the same object: one slim, square rail, not two different cards.
    assert html.count('class="canvas-rail ') == 2
    rail = css.split(".canvas-rail {", 1)[1].split("}", 1)[0]
    assert "flex-direction: column" in rail
    assert "border-radius: 0" in rail
    assert "width: 38px" in rail
    # Nothing in either toolbar's own rules may put the chrome back.
    for block in (".zoom-hud {", ".selection-style-toolbar {"):
        own = css.split(block, 1)[1].split("}", 1)[0]
        assert "border-radius" not in own and "background" not in own and "padding" not in own

    # Dragging, docking, and remembering where a toolbar was left.
    assert "setPointerCapture" in drag
    assert "TOOLBAR_DOCK_DISTANCE" in drag and "canvas-toolbar-docked-left" in drag
    assert "TOOLBAR_POSITION_KEY" in js

    # Merged rails travel together while the drag is happening, not once it is
    # over: the companions are picked up on pointerdown and moved on every
    # pointermove, and every rail that moved is remembered.
    assert "companions" in drag
    assert "drag.companions.forEach" in drag
    assert "canvasToolbars.forEach((entry) => {" in drag

    # They travel together along the wall only. Pulling one off the wall is how
    # it comes out of the bar, or nothing could ever be separated again.
    assert "placed.dock !== drag.dockedFrom" in drag
    assert "drag.companions = [];" in drag

    # Two on the same edge share a width and meet on one line.
    assert "element.offsetHeight - 1" in align
    assert "canvas-toolbar-merged-first" in align and "canvas-toolbar-merged-last" in align
    assert ".canvas-toolbar-merged" in css

    # The observer reacts to visibility alone: watching every class change would
    # see docking's own classes and chase its own tail.
    assert "if (hidden === wasHidden) return;" in drag

    # Every control in either rail answers the same way, and the zoom rail has
    # exactly one divider: under the zoom controls, drawn above the button so
    # its own outline stays even on hover.
    assert ".canvas-rail .zoom-btn:hover" in css and ".canvas-rail .style-tool:hover" in css
    assert ".canvas-rail .zoom-btn.lock-btn.active:hover" in css
    assert ".canvas-rail #zoomResetBtn::before" in css
    rail_buttons = css.split(".canvas-rail .zoom-btn.reset-btn,", 1)[1].split("}", 1)[0]
    assert "border-top" not in rail_buttons

    # Nothing in a rail is round: a rounded button with a border on top draws an
    # arc where the divider between two buttons should be a line.
    for block in (".zoom-btn {", ".zoom-btn.reset-btn {", ".zoom-btn.lock-btn {"):
        rule = css.split(block, 1)[1].split("}", 1)[0]
        assert "border-radius: 50%" not in rule

    # The head is the top of the rail, edge to edge -- no strip of rail above it.
    assert "padding: 0 0 3px" in rail

    # Each head carries one of the two button colours, so the rails are told
    # apart at a glance -- including when they are docked together.
    style_head = css.split("#selectionStyleToolbar .hud-grip {", 1)[1].split("}", 1)[0]
    zoom_head = css.split("#zoomHud .hud-grip {", 1)[1].split("}", 1)[0]
    assert "color: var(--success)" in style_head
    assert "color: var(--accent)" in zoom_head


def test_the_sidebar_slides_open_under_the_pointer_and_locks_open():
    """The sidebar is a rail that slides open when the pointer is on it.

    It slides over the page rather than pushing it, so opening a drawer never
    reflows the canvas, and it keeps its controls and the workspace icons while
    narrow. The lock pins it open: it takes its column back and stops sliding.
    """
    js = ADMIN_JS.read_text(encoding="utf-8")
    css = ADMIN_CSS.read_text(encoding="utf-8")
    html = ADMIN_HTML.read_text(encoding="utf-8")

    assert 'id="sidebarCollapseToggle"' in html and 'id="sidebarLockToggle"' in html
    assert "SIDEBAR_LOCKED_KEY" in js

    # Pointer in, pointer out -- with a moment's grace on the way out.
    assert 'sidebar.addEventListener("pointerenter"' in js
    assert 'sidebar.addEventListener("pointerleave"' in js
    assert "SIDEBAR_SLIDE_AWAY_MS" in js
    # The keyboard gets in and out of it too.
    assert 'sidebar.addEventListener("focusin"' in js

    # Locked means pinned open and no sliding at all.
    assert 'document.body.classList.toggle("sidebar-auto", !sidebarLocked)' in js
    assert "if (!sidebar || sidebarLocked) return;" in js

    # A fixed sidebar leaves the grid, so the page has to be told to stay in the
    # second column -- otherwise it slides into the rail's column and is
    # squeezed to nothing.
    auto_shell = css.split("body.sidebar-auto .shell {", 1)[1].split("}", 1)[0]
    assert "grid-column: 2" in auto_shell

    # Sliding happens over the page: the column stays a rail while it does.
    auto_sidebar = css.split("body.sidebar-auto .sidebar {", 1)[1].split("}", 1)[0]
    assert "position: fixed" in auto_sidebar
    assert "transition: width" in auto_sidebar
    auto_app = css.split("body.sidebar-auto .app {", 1)[1].split("}", 1)[0]
    assert "var(--sidebar-rail" in auto_app

    # Narrow, the rail still shows something to click.
    assert ".sidebar.is-narrow .category-list," in css
    collapsed_nav = css.split(".sidebar.is-narrow .nav-item .nav-icon {", 1)[1].split("}", 1)[0]
    assert "font-size: 15px" in collapsed_nav

    # The wiring sits with the rest of the DOM wiring at the end of the file:
    # attached where the document has been parsed, or the buttons do nothing.
    assert js.index("SIDEBAR_LOCKED_KEY") > js.index("function drawSelection()")


def test_corner_lists_with_no_coordinates_are_never_drawn():
    """Older detections left corner lists like [{}, {"x": null}] behind.

    Fed to an SVG they become "NaN,NaN", which the browser rejects attribute by
    attribute: a console full of errors and no outline on the canvas. Every
    place that reads corners for drawing checks them first and falls back to
    the area's own box.
    """
    js = ADMIN_JS.read_text(encoding="utf-8")
    end_of_function = chr(10) + "  function "
    guard = js.split("function usableCorners(", 1)[1].split(end_of_function, 1)[0]

    assert "Number.isFinite(Number(point.x))" in guard
    assert "Number.isFinite(Number(point.y))" in guard

    # Nothing reads a corner list straight into the drawing any more.
    assert "area.corners || areaCorners(area)" not in js
    assert "region.corners || region.inner_corners || areaCorners(region)" not in js
    assert js.count("usableCorners(") >= 4


def test_queue_has_a_compact_thumbnail_mode():
    """The queue can shrink to a wall of thumbnails.

    A mockup is recognised by its picture long before its name, so the compact
    mode keeps the pictures, drops every other part of the row, and narrows the
    column to give the editor the space back. The name survives as the row's
    tooltip -- it is the only way left to read it.
    """
    js = ADMIN_JS.read_text(encoding="utf-8")
    css = ADMIN_CSS.read_text(encoding="utf-8")
    html = ADMIN_HTML.read_text(encoding="utf-8")

    assert 'id="queueDensityToggle"' in html
    assert "QUEUE_COMPACT_KEY" in js and "applyQueueDensity" in js

    # The choice outlives the page.
    assert 'localStorage.getItem(QUEUE_COMPACT_KEY)' in js
    assert 'localStorage.setItem(QUEUE_COMPACT_KEY' in js

    # Picking by picture only works if the name is still readable somehow.
    assert 'title="${escapeAttr(template.name)}"' in js

    # Compact: one thumbnail per row, and the column itself gives up its width.
    queue_grid = css.split("body.queue-compact .queue {", 1)[1].split("}", 1)[0]
    assert "grid-template-columns: 1fr" in queue_grid
    # ...packed at the top. Auto rows in a tall panel stretch by default, which
    # puts a band of dead space under every thumbnail. The strip scrolls.
    assert "align-content: start" in queue_grid
    assert "overflow-y: auto" in css.split(chr(10) + ".queue {", 1)[1].split("}", 1)[0]
    compact_content = css.split("body.queue-compact .content {", 1)[1].split("}", 1)[0]
    assert "96px" in compact_content

    # The thumbnails keep the size they have in the full list -- the strip is
    # the same pictures, not bigger ones.
    assert "body.queue-compact .queue-select .thumb" not in css
    assert "justify-items: center" in queue_grid
    # ...and they are not left touching the head above them.
    assert "padding: 10px 8px 14px" in queue_grid

    # The head reserves the same scrollbar gutter the list does, so the toggle
    # is centred on the thumbnails rather than on the panel.
    compact_head = css.split("body.queue-compact .panel-head.queue-head {", 1)[1].split("}", 1)[0]
    assert "var(--scrollbar-width" in compact_head
    assert "syncQueueGutter" in js
    assert "queue.offsetWidth - queue.clientWidth" in js

    # The toggle leads the panel head, ahead of the title.
    title_row = html.split('<div class="queue-title-row">', 1)[1].split("</div>", 1)[0]
    assert 'id="queueDensityToggle"' in title_row
    assert html.index('id="queueDensityToggle"') < html.index('class="queue-title-copy"')

    # Everything that is not a thumbnail goes, the empty text holder included.
    hidden = css.split("body.queue-compact .queue-title-copy,", 1)[1].split("}", 1)[0]
    for part in (".queue-filters", ".queue-checkbox", ".queue-delete", ".file-title", ".meta"):
        assert part in hidden
    assert "display: none" in hidden
    text_holder = css.split("body.queue-compact .queue-select > span {", 1)[1].split("}", 1)[0]
    assert "display: none" in text_holder

    # The wiring sits with the rest of the DOM wiring at the end of the file.
    assert js.index("QUEUE_COMPACT_KEY") > js.index("function drawSelection()")


def test_canvas_rails_follow_the_workspace_when_it_changes_width_on_its_own():
    """The queue going compact widens the canvas without touching the window.

    Toolbar positions are pixels measured from the workspace, so a rail docked
    to the right wall is left standing in the middle of the canvas when the
    wall moves away from it -- and the window resize event, which is what used
    to put it back, never fires.
    """
    js = ADMIN_JS.read_text(encoding="utf-8")
    draggable = js.split("function makeToolbarDraggable(", 1)[1].split(
        chr(10) + "  // The sidebar sits at the left edge", 1
    )[0]

    assert "new ResizeObserver(" in draggable
    assert ".observe(dockParent)" in draggable
    # Re-anchoring is the same path a window resize takes.
    assert "restore();" in draggable.split("new ResizeObserver(", 1)[1]
    # A width animation fires the observer every frame; one re-anchor per frame.
    assert "requestAnimationFrame" in draggable and "cancelAnimationFrame" in draggable


def test_coordinate_readout_lives_in_the_canvas_corner():
    """The readout reads the canvas, so it sits on the canvas.

    It shares the top-left corner with the rails, which are draggable, so where
    it starts is measured against whatever rail is standing in that corner --
    and it never takes the pointer, since it is only text over a picture that
    is dragged.
    """
    js = ADMIN_JS.read_text(encoding="utf-8")
    css = ADMIN_CSS.read_text(encoding="utf-8")
    html = ADMIN_HTML.read_text(encoding="utf-8")

    # In the workspace, not in the bar under it.
    assert html.index('class="coordinates"') < html.index('class="editor-foot"')
    assert html.index('id="zoomHud"') < html.index('class="coordinates"')

    readout = css.split(chr(10) + ".coordinates {", 1)[1].split("}", 1)[0]
    assert "position: absolute" in readout
    assert "top: 0" in readout
    assert "pointer-events: none" in readout
    # No box of its own: zoomed in, the picture has to be visible under it.
    assert "background" not in readout
    assert "border" not in readout
    assert "backdrop-filter" not in readout
    assert "text-shadow" in readout

    # The corner is contested, so the offset is measured, not assumed.
    place = js.split("function placeCoordinateReadout() {", 1)[1].split(
        chr(10) + "  }", 1
    )[0]
    assert "canvasToolbars" in place
    assert "rail.right - bounds.left" in place
    # Measured again whenever a rail moves, docks, or is put back.
    assert js.count("placeCoordinateReadout();") >= 4


def test_editor_foot_reads_from_the_left():
    """Everything in the bar under the canvas starts at the left edge.

    Nothing is pushed against the far wall by an auto margin, and nothing
    stretches to fill the bar; when the bar runs short, the pieces give way in
    the order the eye would drop them -- the status line first, then the
    confidence, and only then the words of the detection step.
    """
    css = ADMIN_CSS.read_text(encoding="utf-8")

    foot_confidence = css.split(".editor-foot .confidence {", 1)[1].split("}", 1)[0]
    assert "margin-left: auto" not in foot_confidence
    assert "flex-shrink: 20" in foot_confidence

    state = css.split(".editor-foot #proposalState {", 1)[1].split("}", 1)[0]
    assert "text-align: left" in state
    assert "flex-shrink: 100" in state
    assert "overflow: hidden" in state

    wizard = css.split(chr(10) + ".detection-wizard-foot {", 1)[1].split("}", 1)[0]
    assert "flex: 0 1 auto" in wizard
    assert "margin: 0;" in wizard

    instruction = css.split(".detection-wizard-foot .wizard-instruction {", 1)[1].split("}", 1)[0]
    assert "margin-right: auto" not in instruction
    assert "flex-grow" not in instruction
    assert "text-overflow: ellipsis" in instruction
