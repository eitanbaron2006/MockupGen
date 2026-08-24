# Detection & green-frame rendering — working notes

Handoff notes for continuing the detection/rendering work. Written because the
chat hit its image limit; start a fresh conversation, point at this file, and
attach the screenshot there.

## Open problem

In **GREEN FRAMES** (and **COLOR PICK**, which shares the same path), the
artwork's angle/stretch over the frame looks wrong in the **detection/edit**
view, while **PREVIEW** renders it correctly.

Everything measured so far says the geometry is right, so the remaining lead
needs a screenshot or the specific template + frame number:

- On the perspective mockup the canvas draws exactly the saved corners
  (verified: drawn points == saved points, to the pixel).
- Frame vs mask orientation agrees within 0.6° on every real frame.
- The only frames that disagree are spurious detections where green fills less
  than half its own bounding box (`template_598d444a76f9` region 1, fill 0.43).

To measure a suspect frame, run the orientation/enclosure check in
"Diagnostics" below with its template id.

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
2. **The editing frame exactly bounds the image.** The artwork is drawn on the
   frame's own corners; the mask can only remove from it, never add. So the
   image is never larger than the frame.
3. **The frame is the tightest four-sided shape containing the whole mask.**
   `_region_quad` in `green_frame_mockup_service.py`: convex hull → four-sided
   approximation (keeps perspective) → each side pushed out by the minimum that
   swallows the last stray pixel → sides intersected back into corners.
4. **Corners are ordered by angle around the centre**, then rolled to start at
   the top-left. The older `x+y` heuristic swaps top-right with bottom-left on
   a strongly slanted frame, which mirrors the frame against the mask's slant.
5. **Saved regions pair with detected regions by position, not by index.**
   Detection does not promise a stable order; index pairing hands a frame
   another frame's corners.
6. **Geometric multi-frame templates never use a mask.** They render straight
   onto their corners via `_render_geometric_frames`.

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

## Templates worth knowing about

- `template_a12c3d259ecc` — tablet + phone, **rounded screen corners**. Its
  stored mask was squared off (IoU 0.807 against the green); re-running GREEN
  FRAMES restores it (0.988).
- `template_e753c81fcfca` — five frames in perspective, good slant test. Its
  saved region order differs from a fresh detection's.
- `template_f80429375602` — collage of 15 white frames, the geometry stress
  test.
- `template_598d444a76f9` region 1 — spurious detection, ignore it when
  judging results.

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

## Driving the admin UI headlessly

The browser-automation skill's `--script` loader cannot take a Windows path.
Import patchright directly instead:

```js
import { chromium } from 'file:///C:/Users/Eitan%20Baron/.vscode/extensions/danielsanmedium.dscodegpt-3.24.39/standalone/node_modules/patchright/index.mjs'
```

Run the app against a throwaway sandbox so real templates are never touched:
pass `DATABASE_PATH`, `DRAFT_TEMPLATES_FOLDER`, `TEMPLATES_FOLDER`,
`UPLOAD_FOLDER` and `OUTPUT_FOLDER` into `create_app` pointing at a temp dir.

## Tests

`python -m pytest tests/ -q` — 105 passing. The detection and green-frame
behaviour above is covered in `tests/test_detection_services.py` and
`tests/test_mockup_api.py`.
