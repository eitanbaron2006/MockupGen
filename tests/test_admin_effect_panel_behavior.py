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

    # A grip on each rail, and nothing else claims to be one.
    assert html.count("data-drag-handle") == html.count('class="canvas-rail')
    assert html.count("data-drag-handle") == 4
    assert 'makeToolbarDraggable($("zoomHud"), "zoomHud")' in js
    assert 'makeToolbarDraggable($("selectionStyleToolbar"), "selectionStyleToolbar")' in js

    # All four are the same object: one slim, square rail, not four cards.
    assert html.count('class="canvas-rail ') == 4
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
    # Every rail that moved is remembered, not only the one under the hand.
    assert "rememberToolbarPositions(parent);" in drag
    remember = js.split("function rememberToolbarPositions(parent) {", 1)[1].split(chr(10) + "  }", 1)[0]
    assert "canvasToolbars.forEach((entry) => {" in remember

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


def test_coordinate_readout_is_a_rail_of_its_own():
    """The readout reads the canvas, so it rides on the canvas.

    As a rail like the others: it has a grip, it is dragged and docked through
    the same code, and it starts lying flat -- which is how a row of figures
    reads. The numbers themselves never take the pointer; the canvas under them
    is dragged and zoomed straight through the text.
    """
    js = ADMIN_JS.read_text(encoding="utf-8")
    css = ADMIN_CSS.read_text(encoding="utf-8")
    html = ADMIN_HTML.read_text(encoding="utf-8")

    assert "data-drag-handle" in html.split('id="coordsRail"', 1)[1][:400]
    assert 'class="canvas-rail coords-rail is-horizontal"' in html
    for field in ('id="coordX"', 'id="coordY"', 'id="coordW"', 'id="coordH"'):
        assert field in html.split('id="coordsRail"', 1)[1].split("<!-- ", 1)[0]

    # In the workspace, not in the bar under it.
    assert html.index('id="coordsRail"') < html.index('class="editor-foot"')

    assert 'makeToolbarDraggable($("coordsRail"), "coordsRail")' in js
    # The chip's own placement logic went with the chip.
    assert "placeCoordinateReadout" not in js

    readout = css.split(chr(10) + ".coordinates {", 1)[1].split("}", 1)[0]
    assert "pointer-events: none" in readout

    # Stood on end it stacks, one figure per line, in the same 38px the other
    # rails are wide -- which is what setting the letter solid against its
    # number pays for: X1254 rather than X 1254.
    upright = css.split(".canvas-rail:not(.is-horizontal) .coordinates {", 1)[1].split("}", 1)[0]
    assert "flex-direction: column" in upright
    # No width of its own upright: it is as wide as any other rail.
    assert ".canvas-rail.coords-rail:not(.is-horizontal)" not in css
    solid = css.split(".canvas-rail:not(.is-horizontal) .coordinates strong {", 1)[1].split("}", 1)[0]
    assert "margin-left: 0" in solid


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


def test_detection_switches_live_in_the_top_bar_on_one_line():
    """Engine and mode are chosen from the page's top bar.

    Both switches sit between the breadcrumb and the top actions, on a single
    line -- the container no longer stacks them -- and the pieces around them
    are the ones that give way when the bar runs short, since a button cannot
    be ellipsized.
    """
    css = ADMIN_CSS.read_text(encoding="utf-8")
    html = ADMIN_HTML.read_text(encoding="utf-8")

    topbar = html.split('<header class="topbar">', 1)[1].split("</header>", 1)[0]
    assert 'class="detection-mode-container"' in topbar
    assert topbar.index("detection-mode-container") < topbar.index('class="top-actions"')

    tools = html.split('<div class="editor-tools">', 1)[1].split("</div>", 1)[0]
    assert "detection-mode" not in tools
    assert 'id="detectButton"' in tools

    container = css.split(".detection-mode-container {", 1)[1].split("}", 1)[0]
    assert "flex-direction: column" not in container
    assert "flex-shrink: 0" in container

    crumb = css.split(chr(10) + ".crumb {", 1)[1].split("}", 1)[0]
    assert "text-overflow: ellipsis" in crumb
    actions = css.split(chr(10) + ".top-actions {", 1)[1].split("}", 1)[0]
    assert "flex-shrink: 0" in actions


def test_confidence_is_hidden_but_still_computed():
    """The number is out of sight, not out of the code.

    It is hidden in the stylesheet alone, so the element is still there and
    still written to -- putting it back is one line, and nothing else has to be
    rebuilt to do it.
    """
    css = ADMIN_CSS.read_text(encoding="utf-8")
    js = ADMIN_JS.read_text(encoding="utf-8")
    html = ADMIN_HTML.read_text(encoding="utf-8")

    assert 'id="confidence"' in html
    assert '$("confidence").textContent = confidenceLabel(' in js
    hidden = css.split(".editor-foot .confidence {", 1)[1].split("}", 1)[0]
    assert "display: none" in hidden


def test_editor_head_is_as_shallow_as_the_minimal_queue_head():
    """One slim row over the canvas.

    The title and its line of explanation share a row instead of stacking, the
    buttons are smaller than the page's standard ones, and the bar lands on the
    height the queue's head has when the queue is minimal -- 54px, measured in
    the browser for both.
    """
    css = ADMIN_CSS.read_text(encoding="utf-8")

    head = css.split(chr(10) + ".editor-head {", 1)[1].split("}", 1)[0]
    assert "min-height: 54px" in head
    assert "padding: 12px 19px" in head

    copy = css.split(chr(10) + ".editor-title-copy {", 1)[1].split("}", 1)[0]
    assert "display: flex" in copy
    assert "align-items: baseline" in copy

    sub = css.split(".editor-title-copy .sub {", 1)[1].split("}", 1)[0]
    assert "text-overflow: ellipsis" in sub
    assert "white-space: nowrap" in sub

    # Smaller than the 40px buttons everywhere else on the page.
    buttons = css.split(".editor-tools .btn {", 1)[1].split("}", 1)[0]
    assert "height: 28px" in buttons


def test_canvas_has_a_rail_for_the_mockup_actions():
    """Preview, back-to-editing and download, as icons on the canvas.

    They are a rail of their own -- draggable and dockable like the other two --
    rather than tools tacked onto the end of the overlay-style rail, and a tool
    that is not available now greys out in place instead of leaving the rail.
    """
    js = ADMIN_JS.read_text(encoding="utf-8")
    css = ADMIN_CSS.read_text(encoding="utf-8")
    html = ADMIN_HTML.read_text(encoding="utf-8")

    rail = html.split('id="actionRail"', 1)[1].split("</div>", 1)[0]
    assert "canvas-rail" in html.split('id="actionRail"', 1)[0].rsplit("<div", 1)[1]
    assert "data-drag-handle" in rail
    for tool in ('id="toolbarPreviewButton"', 'id="toolbarEditButton"', 'id="toolbarDownloadButton"'):
        assert tool in html.split('id="actionRail"', 1)[1].split("<!-- ", 1)[0]

    # They are no longer part of the overlay-style rail.
    style_rail = html.split('id="selectionStyleToolbar"', 1)[1].split('id="actionRail"', 1)[0]
    assert "toolbarPreviewButton" not in style_rail
    assert "toolbarDownloadButton" not in style_rail

    # Unavailable reads as greyed out, not as gone -- the row never shifts.
    greyed = css.split(".action-rail .style-tool.hidden {", 1)[1].split("}", 1)[0]
    assert "display: inline-flex !important" in greyed
    assert "pointer-events: none" in greyed

    # Dragged and docked like the other rails, and the pencil follows the state.
    assert 'makeToolbarDraggable($("actionRail"), "actionRail")' in js
    assert "function syncActionRail()" in js
    assert 'edit.classList.toggle("hidden", !state.isPreviewingMockup)' in js
    assert js.count("syncActionRail();") >= 4
    # It comes and goes with the rail it was split from.
    assert js.count('$("actionRail").classList.add("hidden")') == 1
    assert js.count('$("actionRail").classList.remove("hidden")') == 1


def test_classic_submodes_grey_out_instead_of_disappearing():
    """Another engine leaves the row of modes on the bar.

    Buttons blinking in and out as the engine changes is what made the top bar
    feel unsteady; they are disabled instead, so the bar keeps its shape.
    """
    js = ADMIN_JS.read_text(encoding="utf-8")
    css = ADMIN_CSS.read_text(encoding="utf-8")

    update = js.split("function updateClassicSubmodes() {", 1)[1].split(chr(10) + "  }", 1)[0]
    assert 'submodeBar.classList.toggle("hidden"' not in update
    assert 'submodeBar.classList.remove("hidden")' in update
    assert 'submodeBar.classList.toggle("is-disabled", !isClassic)' in update
    assert "button.disabled = !isClassic" in update

    assert ".classic-submodes-switch.is-disabled {" in css
    # A disabled button does not light up under the pointer.
    assert ".submode-btn:hover:not(:disabled) {" in css


def test_top_bar_keeps_server_pulse_as_an_icon_and_hides_the_status_line():
    """Two pieces of the top bar step back.

    Server Pulse is an icon -- it is still spelled out as a nav item in the
    sidebar -- and the running green commentary is hidden in the stylesheet
    only, so it is still written and can be brought back in one line.
    """
    css = ADMIN_CSS.read_text(encoding="utf-8")
    js = ADMIN_JS.read_text(encoding="utf-8")
    html = ADMIN_HTML.read_text(encoding="utf-8")

    pulse = html.split('<div class="top-actions">', 1)[1].split("</a>", 1)[0]
    assert "top-icon-button" in pulse
    assert "Server Pulse" not in pulse.split(">")[-1]
    assert 'aria-label="Server Pulse"' in pulse
    assert "top-icon-button" in css

    status = css.split(chr(10) + ".top-status {", 1)[1].split("}", 1)[0]
    assert "display: none" in status
    assert '$("status").textContent = message;' in js


def test_studio_name_shares_the_row_with_the_sidebar_controls():
    """The name sits beside collapse and lock, not under them."""
    css = ADMIN_CSS.read_text(encoding="utf-8")
    html = ADMIN_HTML.read_text(encoding="utf-8")

    head = html.split('<div class="sidebar-head">', 1)[1].split('<p class="nav-label">', 1)[0]
    assert 'class="brand"' in head
    assert 'class="sidebar-controls"' in head
    assert head.index('class="brand"') < head.index('class="sidebar-controls"')

    row = css.split(chr(10) + ".sidebar-head {", 1)[1].split("}", 1)[0]
    assert "display: flex" in row
    assert "align-items: center" in row
    assert "justify-content: space-between" in row

    # The name no longer carries the block margin that stacked it above them,
    # and it is cut short rather than pushing the controls off the row.
    brand = css.split(chr(10) + ".brand {", 1)[1].split("}", 1)[0]
    assert "margin: 0;" in brand
    assert "text-overflow: ellipsis" in brand


def test_green_frame_controls_follow_the_render_not_the_frame_count():
    """A set of green frames gets the panel too.

    The renderer takes a set through the same pipeline as a single green frame
    and reads the same effects.green_frame_mockups block, so a set was left
    with those settings in force and no way to reach them. The panel now asks
    whether the settings reach the render; the editor's own green-only
    behaviour still asks the narrower question and still excludes a set.
    """
    js = ADMIN_JS.read_text(encoding="utf-8")

    apply_fn = js.split("function greenFrameControlsApply(", 1)[1].split(chr(10) + "  }", 1)[0]
    assert "usesGreenFramePipeline(template) || isGreenFrameTemplate(template)" in apply_fn

    # The panel, and the refresh that makes its sliders mean something.
    assert 'panel.classList.toggle("hidden", !greenFrameControlsApply(template))' in js
    assert 'panel.classList.toggle("hidden", !isGreenFrameTemplate(template))' not in js
    assert "if (greenFrameControlsApply() && state.selectionStyle.overlayImage) {" in js

    # Everything else that is green-only is untouched: a set is still not one.
    green_only = js.split("function isGreenFrameTemplate(", 1)[1].split(chr(10) + "  }", 1)[0]
    assert "if (isMultiRegionTemplate(template)) return false;" in green_only
    # ...and it still drives everything else it drove before: the live green
    # render, positioning with the mouse, the caches.
    assert js.count("isGreenFrameTemplate(") >= 10


def test_every_slider_has_a_reset_and_answers_the_wheel():
    """One pass over the page gives every range input the same two affordances.

    Nothing is added to the markup slider by slider -- there are forty of them
    and more are cloned at runtime -- so the pass is generic and repeats over
    whatever the page grows later.
    """
    js = ADMIN_JS.read_text(encoding="utf-8")
    css = ADMIN_CSS.read_text(encoding="utf-8")

    enhance = js.split("function enhanceSlider(input) {", 1)[1].split(chr(10) + "  }", 1)[0]
    assert 'reset.className = "slider-reset"' in enhance
    assert "setSliderValue(input, fallback)" in enhance
    # The wheel adjusts by the slider's own step, and holds the panel still.
    assert '"wheel"' in enhance and "{ passive: false }" in enhance
    assert "event.preventDefault();" in enhance
    assert "sliderStep(input) * (event.shiftKey ? 10 : 1)" in enhance

    # Whatever moves the value tells the page it moved, so the number beside the
    # slider, the redraw and the save all run as if it had been dragged.
    commit = js.split("function setSliderValue(input, value) {", 1)[1].split(chr(10) + "  }", 1)[0]
    assert 'new Event("input", { bubbles: true })' in commit
    assert 'new Event("change", { bubbles: true })' in commit
    # Snapped to the step, or a step of 0.05 drifts to 0.5000000000000001.
    assert "Math.round((value - base) / step) * step" in commit
    assert "sliderDecimals(step)" in commit

    # The default is the value the markup ships with.
    default_fn = js.split("function sliderDefault(input) {", 1)[1].split(chr(10) + "  }", 1)[0]
    assert 'input.getAttribute("value")' in default_fn

    # Cloned panels bring a copy of the button with no handler behind it; the
    # link back to the slider is a property, which a clone does not carry.
    sweep = js.split("function enhanceSliders() {", 1)[1].split(chr(10) + "  }", 1)[0]
    assert "if (!button.__sliderInput) button.remove();" in sweep
    assert "document.querySelectorAll('input[type=\"range\"]').forEach(enhanceSlider)" in sweep
    # ...and the pass repeats over whatever the page grows later.
    assert "sliderSweep" in js
    assert "observe(document.body, { childList: true, subtree: true })" in js

    assert ".slider-reset {" in css
    # A slider that came without a row of its own gets one.
    assert ".slider-with-reset {" in css


def test_canvas_rails_can_be_turned_on_their_side():
    """A rail stands on end or lies flat, and docks to the wall it can reach.

    Turning happens on the head that already moves the rail -- double-click, or
    Enter -- so the bar grows no button that is not a tool. Upright it snaps to
    the side walls as before; flat it snaps to the top or bottom instead, since
    that is the wall its long edge can meet.
    """
    js = ADMIN_JS.read_text(encoding="utf-8")
    css = ADMIN_CSS.read_text(encoding="utf-8")

    drag = js.split("function makeToolbarDraggable(", 1)[1]
    assert 'handle.addEventListener("dblclick"' in drag
    assert 'if (event.key !== "Enter" && event.key !== " ") return;' in drag
    assert 'toolbar.classList.toggle("is-horizontal");' in drag

    # Which walls a rail can reach depends on how it is lying.
    assert "if (snap && toolbarIsHorizontal(toolbar)) {" in drag
    assert 'dock = "top";' in drag and 'dock = "bottom";' in drag

    # Merging two rails into one bar stays a side-wall affair between rails
    # that stand on end -- a flat one has no width to share.
    align = js.split("function alignDockedToolbars() {", 1)[1].split(chr(10) + "  }", 1)[0]
    assert "!toolbarIsHorizontal(element)" in align

    # A turned rail is a different shape in the same corner: it steps clear of
    # whatever is already standing there.
    assert "const clearOfOtherRails = () => {" in js
    # How it was left lying is remembered along with where.
    assert "horizontal: toolbarIsHorizontal(entry.element)" in js
    assert 'toolbar.classList.toggle("is-horizontal", Boolean(saved.horizontal))' in js

    flat = css.split(".canvas-rail.is-horizontal {", 1)[1].split("}", 1)[0]
    assert "flex-direction: row" in flat
    assert "height: 38px" in flat
    # The grip's bars turn with the rail, and so does the single rule in the
    # zoom rail.
    assert ".canvas-rail.is-horizontal .hud-grip span {" in css
    assert ".canvas-rail.is-horizontal #zoomResetBtn::before {" in css
    assert ".canvas-rail.canvas-toolbar-docked-top {" in css


def test_full_screen_overlays_do_not_blur_what_is_behind_them():
    """A viewport-wide backdrop filter is paid for on every repaint above it.

    The test-mockups modal was the place it showed: scrolling the gallery of
    matching mockups ran at 18fps with the blur and 59 without it, measured in
    the browser, because every frame re-blurred the whole page behind the
    modal. The scrim does the separating; two pixels of blur were adding
    nothing anyone could see, and the lightbox's ten were behind 92% black.
    """
    css = ADMIN_CSS.read_text(encoding="utf-8")

    backdrop = css.split(chr(10) + ".modal-backdrop {", 1)[1].split("}", 1)[0]
    assert "backdrop-filter" not in backdrop
    assert "rgba(0, 0, 0, 0.46)" in backdrop

    lightbox = css.split(chr(10) + ".lightbox-overlay {", 1)[1].split("}", 1)[0]
    assert "backdrop-filter" not in lightbox

    # The rails still carry theirs: they are 38px wide and cost nothing
    # measurable (63fps against 62 while zooming the canvas).
    rail = css.split(chr(10) + ".canvas-rail {", 1)[1].split("}", 1)[0]
    assert "backdrop-filter: blur(12px)" in rail


def test_names_injected_into_attributes_are_escaped_for_attributes():
    """escapeHtml does not escape quotes; inside an attribute that is a hole.

    A template named `" onerror=alert(1) x="` closes the attribute it is
    written into and the rest becomes markup. Template and category names are
    typed by hand in this studio, so every attribute they reach goes through
    escapeAttr, which escapes the quotes as well.
    """
    import re

    js = ADMIN_JS.read_text(encoding="utf-8")

    # The helper is the one that closes the quotes.
    helper = js.split("function escapeAttr(value) {", 1)[1].split(chr(10) + "  }", 1)[0]
    assert '/"/g' in helper and "&quot;" in helper

    # Nothing writes an unescaped-for-attribute name into an attribute.
    leftovers = re.findall(r'[a-zA-Z-]+="\$\{escapeHtml\(', js)
    assert leftovers == [], leftovers
    leftovers = re.findall(r"[a-zA-Z-]+='\$\{escapeHtml\(", js)
    assert leftovers == [], leftovers

    # And the places that carry a name do escape it.
    assert 'title="${escapeAttr(template.name)}"' in js
    assert 'aria-label="Delete ${escapeAttr(template.name)}"' in js
    assert 'alt="${escapeAttr(t.name)}"' in js


def test_green_panel_controls_the_opening_per_side_and_the_green_tolerance():
    """Two things the panel could not say before.

    How far the opening reaches past the detected green on each side -- the way
    to cover a sliver of green left along one edge -- and how strict the green
    test itself is. The backend already understood tolerance; it had no way in
    from the panel.
    """
    js = ADMIN_JS.read_text(encoding="utf-8")
    html = ADMIN_HTML.read_text(encoding="utf-8")

    panel = html.split('id="greenFramePanelBody"', 1)[1].split("</div>\n              </div>", 1)[0]
    assert 'id="greenTolerance"' in panel
    for side in ("Top", "Bottom", "Left", "Right"):
        assert f'id="greenMaskExpand{side}"' in panel, side
        assert f'id="greenMaskExpand{side}Val"' in panel, side

    # Read from the panel, written to the template, and read back into it.
    assert "tolerance: Number($(\"greenTolerance\").value)" in js
    assert 'mask_expand_top: Number($("greenMaskExpandTop").value)' in js
    assert '$("greenTolerance").value = settings.tolerance;' in js
    assert 'settings[`mask_expand_${side.toLowerCase()}`]' in js

    # ...and every one of them redraws and saves like the sliders beside them.
    wired = js.split('    "greenEdgeExpand",', 1)[1].split("]", 1)[0]
    for control in ("greenTolerance", "greenMaskExpandTop", "greenMaskExpandBottom",
                    "greenMaskExpandLeft", "greenMaskExpandRight"):
        assert f'"{control}"' in wired, control
