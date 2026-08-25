# Detection & green-frame rendering — working notes

Handoff notes for continuing the detection/rendering work.

## The angle/stretch problem — solved (2026-08-24)

In **GREEN FRAMES** (and **COLOR PICK**, which shares the same path) the
artwork's angle looked wrong in the **detection/edit** view while **PREVIEW**
rendered it correctly. `template_44ab02ba0914` frame 2 — the phone on the
wooden stand — showed it plainly: the grid in the editor leaned about 4° while
the render sat square on the screen.

Two faults, one on each side of the same frame:

1. **The saved frame was tilted.** `_region_quad` simplified the phone's
   *rounded* screen outline to four sides that rested on the corner arcs, some
   4° off the real edges, then pushed those sides out to contain the mask. The
   result circumscribed the screen at a slant: 137×296 instead of 130×289, 8%
   more area than the mask itself. The editor draws the artwork on those
   corners, so the artwork leaned with them.
2. **The render ignored the frame.** `_render_perspective_region` warped onto
   `_expanded_quad(outer_corners)`. `outer_corners` is recorded once at
   detection time and never moves, so the render kept using the old level quad
   — and, worse, dragging a frame changed nothing in the output.

Fixes:

- `_region_quad` now builds every candidate it can (the four-sided
  approximation pushed out to contain the mask, plus the mask's minimal
  rectangle) and keeps the one that wastes the least area. A frame in
  perspective still wins on its own quad; a rounded opening now wins on the
  rectangle. Eight regions across the library got a tighter frame, the phones
  by 6–7%.
- `_render_perspective_region` warps onto the region's own frame
  (`region.corners`) and nothing wider.
- `renderGreenFrameArtworkOverlay` in `admin.js` mirrors the renderer:
  `greenFrameArtworkPlacement` draws on the frame's own corners, drops to the
  upright bounding box when **Perspective warp** is off (the renderer's
  `_draw_rect` path), and sizes the fit box by the longer of each pair of
  opposite sides the way `_render_perspective_region` does — or by the frame's
  bounding box wherever no green pipeline runs.

Measured after the fix, on the phone frame: mean editor-vs-render pixel
difference 34.3 → 0.4, and in the live admin the overlay quad matches the
renderer's warp quad to 0.04 px with identical fit boxes.

## How detection is wired

`AUTO DETECT` is **purely geometric** — no colour, no mask. The submodes under
CLASSIC are:

| submode | what it does |
|---|---|
| `auto` | geometry only, `services/frame_geometry_service.py` |
| `frame_points` | flood fill from clicked seed points |
| `green_frames` | fixed chroma pass, writes `mask.png` |
| `color_pick` | flood fill from a sampled colour, writes `mask.png` |

`green_frames` and `color_pick` both produce `raw_artwork_area.mode ==
"green_frames_mockups"` plus a mask, so they render identically.

## Rules that must hold

1. **The mask belongs to the mockup.** Nothing about editing artwork may
   change it — not rendering, not dragging a frame. Rendering never writes to
   the template folder (guarded by
   `test_rendering_never_modifies_the_template_mask`).
2. **The artwork is drawn on the frame's own corners** — `region.corners`, the
   quad the admin sees and drags — and the mask can only remove from what is
   drawn. Both the editor and the renderer read the frame, so a dragged frame
   changes the output.
   This holds with no exception, in every mode and on both sides. The wide
   coverage envelope does not widen the quad the artwork is warped onto — it
   pads the *source* image with replicated edge pixels, which land outside the
   frame and give a mask that reaches past the frame something other than bare
   background. Widening the quad instead would scale and shift every pixel
   inside the frame and spill real artwork outside it, which is what a frame
   dragged inwards made plain to see.
   `usesGreenFramePipeline` in `admin.js` still mirrors the branch in
   `render_simple_mockup`, because the two paths fit the artwork to different
   boxes: the green pipeline to the frame's side lengths, everything else to
   its bounding box.
3. **The frame is the tightest four-sided shape containing the whole mask.**
   `_region_quad` in `green_frame_mockup_service.py` builds candidates — the
   convex hull's four-sided approximation with each side pushed out by the
   minimum that swallows the last stray pixel, and the hull's minimal
   rectangle — and keeps the smallest by area. Never keep the first candidate:
   a rounded opening's approximation comes out tilted, and tilt is invisible in
   a quad's corner list but glaring in the artwork drawn on it.
4. **Corners are ordered by angle around the centre**, then rolled to start at
   the top-left. The older `x+y` heuristic swaps top-right with bottom-left on
   a strongly slanted frame, which mirrors the frame against the mask's slant.
5. **Saved regions pair with detected regions by position, not by index.**
   Detection does not promise a stable order; index pairing hands a frame
   another frame's corners.
6. **Geometric multi-frame templates never use a mask.** They render straight
   onto their corners via `_render_geometric_frames`.
7. **Every detected frame is editable, however it was found.** GREEN FRAMES,
   COLOR PICK and FRAME POINTS all save their frames in
   `raw_artwork_area.regions`, which is what the renderer reads, so they get
   the same polygon and corner handles a multi-frame template has
   (`renderRegionFrames`). Only the whole-artwork-area rectangle stays out of
   it on a mask-backed template: its geometry lives in its regions. The delete
   badge is held back where there is a single frame -- removing it would leave
   the template with none.
8. **The editor is a promise about the render.** Anything that decides where
   the artwork lands — perspective warp, coverage envelope, fit mode, scale,
   offset — has to be read the same way in `admin.js` as in
   `green_frame_mockup_service.py`.
   `test_green_frame_overlay_places_artwork_where_the_renderer_will` guards the
   pairing.

## FRAME POINTS: the fill stops at hard edges

The seed-point fill compares each pixel with its **neighbour**, which is what
lets it follow the shading across a blank opening -- and also what let it slip
through a soft spot in a frame's bevel and take the moulding with it. On
`template_dff34d2f4740` (opening x 504–943, y 99–682) tolerance 5 returned
x 486–953, y 87–683: the artwork was then painted over the wood.

A pixel sitting on a hard edge now blocks the fill (`_EDGE_BARRIER`, fed to
`cv2.floodFill` through its mask, which it will not cross). The threshold is
deliberately high -- a bezel clears it, shading inside an opening never does --
because the point was to fix the frames that leak without touching the ones
that already read correctly. Measured over the 53 single-opening templates,
seeded at the centre of each saved artwork area, against the same code without
the barrier:

| tolerance | identical result | better | worse |
|---|---|---|---|
| 5 | 41/53 | 7 | 0 |
| 20 | 39/53 | 12 | 0 |
| 47 | 31/53 | 18 | 1 |

The one at tolerance 47 is `template_351e2551773a`, which used to return the
entire canvas and now returns nothing, so the HUD asks for a different
tolerance instead of proposing the whole mockup. The leaking case now reads
x 497–944, y 99–683 at tolerance 5.

An earlier attempt measured the tolerance against the seed colour
(`cv2.FLOODFILL_FIXED_RANGE`) instead. It read the openings well from tolerance
20 up but under-filled badly at the low tolerances actually in use, so it was
reverted -- do not reapply it without measuring at tolerance 5.

## FRAME POINTS: a frame that is not level keeps its angle

`_refine_region_boundaries` snaps the region's bounding box to the frame's
edges and fills that box, which squares the region off. A mockup photographed
a couple of degrees off level therefore came back level: on
`template_71df1eb48bad` the fill found a top edge at -2.19° and the saved frame
read -0.49°, so artwork drawn in it sat crooked against the frame around it.

`_straighten_sides` now gives each side the angle of the line the fill found,
and **only** the angle: the side keeps the reach the snap gave it and stays
outside every pixel of the fill, so nothing is trimmed. Taking the fitted line
itself pulls each side in by the pixel or two the fill stops short of the
opening, and the mockup's own background then shows as a sliver down the edge
of the artwork -- which is exactly what happened on the first attempt.

A side is only trusted when it really is a straight line: 90% of its samples
within 1.5px of the fitted line and no more than 10° of tilt
(`_EDGE_INLIER_PX`, `_EDGE_MIN_INLIERS`, `_EDGE_MAX_TILT`). Where the fill has
leaked its boundary wanders, fails the test, and the squared box stands, so a
line fitted through a leak cannot skew the frame.

Measured over the 53 single-opening templates, against the same code without
the straightening: no region lost, and the bounding box is within a pixel on
41/45 at tolerance 5 and 43/45 at tolerance 20 (median change 0px) -- the
angles move, the coverage does not. The two mockups that prompted this read
-2.13° against an opening of -2.19°, and -0.77° against -1.06°.

## Things already fixed (do not reintroduce)

- The render used to save its derived mask over the detected one, destroying
  the real outline permanently.
- Admin canvas previews used to write a PNG into `outputs/` and the artwork
  into `uploads/` on every redraw. Previews now come back inline as a data URL
  and touch no disk.
- `RESET DETECTION` used to re-run detection instead of clearing it.
- Admin assets were cache-busted by a hand-edited `?v=` number. `asset_version`
  in `app.py` now stamps them from file mtime.
- The delete badge on a detected frame was unclickable: `.selection-svg` sets
  `pointer-events: none` and the badge never opted back in.
- CSS masks read **alpha** by default. `mask.png` is greyscale with no alpha,
  so the canvas mask clipped nothing until `mask-mode: luminance` was set.
- The canvas read `#fitMode` while the renderer reads the template's
  `effects.green_frame_mockups.fit_mode`, so the two fitted artwork differently.
- FRAME POINTS' fill slipped through soft spots in a frame's bezel and took
  the moulding with it — see the section above.
- A frame that is not level came back level, because the region was squared
  off to its bounding box — see the section above.
- A mask-backed template drew no polygon and refused every drag, so a FRAME
  POINTS or COLOR PICK result could not be adjusted at all. Its frames are
  edited like any other now (`renderRegionFrames`).
- The geometry wizard named a `mask.png` its template does not have, and a CSS
  mask that 404s masks everything out: during an AUTO DETECT review every frame
  came back empty and the artwork only returned once the detection was
  approved. Geometric frames carry no mask, and `applyOverlayMask` now probes a
  mask before trusting it and drops one that cannot load.
- Running a detection from PREVIEW left the canvas blank behind the detected
  frames for the same reason from the other direction: `drawSelection` returns
  early while `state.isPreviewingMockup` is set, so clearing the preview and
  then redrawing produced nothing. Detection leaves preview mode first.
- A template whose `mask.png` was not beside it — five of them, the file
  living only in the draft folder — failed to render at all with "Green frame
  mask has no usable regions". The frames carry exact corners, so the render
  draws the opening from them when no mask file stands behind the template
  (`_mask_from_regions`).
- That failure surfaced in the admin as a Download button enabled with an empty
  href, which downloads the page itself under the name mockup.png: an image
  file that will not open. The download link is set in one place now
  (`setDownloadTarget`), it hands over a blob rather than a multi-megabyte
  `data:` URL, and a render that fails leaves the button disabled
  (`clearDownloadTarget`) with the reason on it.
- The overlay then read the fit mode off the *panel defaults* rather than off
  the template's green effect. Those defaults carry `cover`, so a template set
  to stretch had the sides cropped off its artwork in the editor — visible the
  moment a mask-backed mode (FRAME POINTS, COLOR PICK) took over from AUTO
  DETECT on the same template. `parse_green_frame_settings` falls back to the
  template's own fit mode when the effect names none, and the editor now does
  the same.
- The editor warped the artwork onto the frame while the renderer warped onto
  the detection-time `outer_corners`, and sized its fit box from the top and
  left edges while the renderer used the longer of each pair of opposite sides.
- The frame of a rounded opening was the *tilted* circumscribing quad, because
  the first four-sided approximation was kept without comparing its area.
- The editor applied the green pipeline's coverage envelope to *every*
  template. On a geometric multi-frame template — AUTO DETECT, no mask — that
  drew the artwork some 10px outside its frame with nothing to clip it, so the
  artwork visibly overhung the editing frame.
- Switching templates left the artwork overlay scaled by the previous mockup's
  aspect: `canvasImage.src = url` does not update `naturalWidth`/`naturalHeight`
  until the new bitmap is swapped in, cached or not, and the redraw ran on the
  next animation frame. Nothing redrew it afterwards, so the artwork stayed
  squashed until the window was resized.
- The wide coverage envelope used to warp the artwork onto a quad ~10px wider
  than the frame. Wherever the mask reached past the frame — any frame dragged
  inwards, which is what `corners_edited` records — real artwork was painted
  outside the frame, and every pixel inside it was scaled and shifted with it.
  It pads the source image instead now, so the frame bounds the artwork and the
  envelope only decides what lands in the sliver beyond it.

## Open follow-ups

- **Saved corners predate the `_region_quad` fix.** The render now honours the
  saved frame, so a template whose frame was recorded tilted renders tilted
  until detection is re-run on it. Re-running GREEN FRAMES fixes it. Affected
  (region in brackets): `template_fa83e6c793fe` [2], `template_a12c3d259ecc`
  [2], `template_44ab02ba0914` [2], `template_ac1671aa932f` [2],
  `template_598d444a76f9` [4] — plus three sub-1% cases not worth touching
  (`fa83e6c793fe` [1], `1364f4fca28f` [3], `f7ef6fbdcaac` [2]).
  **`template_ac1671aa932f` carries `corners_edited: true`** — its frames were
  adjusted by hand, and re-detecting would throw that work away.
  `template_44ab02ba0914` has already been re-run and carries the level frame.
## Diagnostics

Frame vs mask orientation and enclosure, per region:

```python
import sys, numpy as np, cv2, math
sys.path.insert(0, r"c:\Users\Eitan Baron\Desktop\MockupGen")
from PIL import Image
from services.green_frame_mockup_service import (
    GreenFrameSettings, detect_green_frames, green_detection_raw)

def orient(binary):
    m = cv2.moments(binary.astype(np.uint8), binaryImage=True)
    if not m["m00"]:
        return None
    a, c, d = m["mu20"] / m["m00"], m["mu02"] / m["m00"], m["mu11"] / m["m00"]
    return 0.5 * math.degrees(math.atan2(2 * d, a - c))

tid = "template_e753c81fcfca"
state = detect_green_frames(
    Image.open(f"templates_data/{tid}/background.png").convert("RGBA"),
    GreenFrameSettings(edge_expand=1, min_area=2500))
for i, r in enumerate(green_detection_raw(state, 1)["regions"]):
    x0, y0 = r["x"], r["y"]
    sub = state.detect_mask[y0:y0 + r["height"], x0:x0 + r["width"]]
    quad = np.zeros_like(sub, np.uint8)
    cv2.fillPoly(quad, [np.array(
        [[int(round(p["x"])) - x0, int(round(p["y"])) - y0] for p in r["corners"]],
        np.int32)], 1)
    outside = int((sub & ~quad.astype(bool)).sum())
    om, oq = orient(sub), orient(quad.astype(bool))
    diff = min(abs(om - oq), 180 - abs(om - oq))
    print(f"region {i+1}: mask {om:+7.2f} frame {oq:+7.2f} off {diff:5.2f}deg "
          f"outside {outside}px fill {sub.mean():.2f}")
```

Wasted area is the other tell: compare the quad's area against
`detect_mask.sum()` for the region. Anything past ~1.05 on a rectangular
opening means the quad is tilted off the real edges.

## Driving the admin UI headlessly

The browser-automation skill's `--script` loader cannot take a Windows path.
Import patchright directly instead:

```js
import { chromium } from 'file:///C:/Users/Eitan%20Baron/.vscode/extensions/danielsanmedium.dscodegpt-3.24.39/standalone/node_modules/patchright/index.mjs'
```

Run the app against a throwaway sandbox so real templates are never touched:
pass `DATABASE_PATH`, `DRAFT_TEMPLATES_FOLDER`, `TEMPLATES_FOLDER`,
`UPLOAD_FOLDER` and `OUTPUT_FOLDER` into `create_app` pointing at a temp dir.

Two things that cost time there:

- **Seeding a fresh catalog from a templates folder drops the detection data.**
  `CatalogService.initialize` reads the manifests but leaves
  `raw_artwork_area`, `effects` and `detection_provider` null, so the template
  renders down the single-area mask path and the editor shows one region. Copy
  those columns from the real `data/mockup_catalog.sqlite3` row.
- The artwork overlay lives in `localStorage`, not behind a file input. Seed
  `mockupStudio.selectionStyle` with `overlayMode: "image"` and an
  `overlayImage` data URL through `context.addInitScript` and the canvas comes
  up with artwork already on it. Then click `.category-select` before
  `#queue .queue-select`.

To check editor/render parity without eyeballing a screenshot, read the region
overlays' own geometry out of the page:

```js
const m = new DOMMatrix(getComputedStyle(div).transform)   // .green-frame-region-overlay
const p = m.transformPoint(new DOMPoint(w, 0, 0, 1))       // corner in display px
```

and compare it against the region's own `corners` — the renderer warps onto
those exactly, so the two should agree to a fraction of a pixel.

## Templates worth knowing about

- `template_44ab02ba0914` — laptop + phone on a stand. Frame 2 is the rounded
  phone screen the tilt was found on.
- `template_a12c3d259ecc` — tablet + phone, **rounded screen corners**. Its
  stored mask was squared off (IoU 0.807 against the green); re-running GREEN
  FRAMES restores it (0.988).
- `template_e753c81fcfca` — five frames in perspective, good slant test. Its
  saved region order differs from a fresh detection's.
- `template_f80429375602` — collage of 15 white frames, the geometry stress
  test.
- `template_598d444a76f9` region 1 — spurious detection (green fills 0.43 of
  its own bounding box), ignore it when judging results.

## Tests

`python -m pytest tests/ -q` — 122 passing. The detection and green-frame
behaviour above is covered in `tests/test_detection_services.py`,
`tests/test_mockup_api.py` and `tests/test_admin_effect_panel_behavior.py`.
