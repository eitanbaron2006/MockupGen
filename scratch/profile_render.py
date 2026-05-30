import sys
sys.path.append('.')
import time
from pathlib import Path
from PIL import Image
import numpy as np

# Load mockup, mask and template components
templates_folder = Path("draft_templates")
template_id = "template_44ab02ba0914"
template_folder = templates_folder / template_id

background = Image.open(template_folder / "background.png")
mask = Image.open(template_folder / "mask.png")

# Let's create a mock artwork of 800x800
artwork = Image.new("RGBA", (800, 800), (255, 0, 0, 255))

# Let's import the actual render function
from services.simple_mockup_service import load_manifest, render_simple_mockup

# Time render_simple_mockup
print("Timing render_simple_mockup...")
t0 = time.perf_counter()

# Let's create a dummy uploaded artwork path
dummy_art_path = Path("scratch/dummy_artwork.png")
dummy_art_path.parent.mkdir(parents=True, exist_ok=True)
artwork.save(dummy_art_path)

t1 = time.perf_counter()
result = render_simple_mockup(
    template_id=template_id,
    artwork_path=dummy_art_path,
    output_format="png",
    templates_folder=templates_folder,
    output_folder=Path("scratch/output"),
    fit_mode="cover",
    realism=True,
    effects=None,
    artwork_area=None,
    raw_artwork_area=None,
    mask_name="mask.png"
)
t2 = time.perf_counter()
print(f"Total time in render_simple_mockup: {t2 - t1:.3f} seconds")

# Profiling internally:
from services.green_frame_mockup_service import parse_green_frame_settings, detect_green_frames, render_green_frame_mockup
from services.simple_mockup_service import _full_canvas_mask, detection_from_mask

print("Timing internal steps:")
effects = None
fit_mode = "cover"
settings = parse_green_frame_settings(effects, fit_mode)

start = time.perf_counter()
detection = detect_green_frames(background, settings)
print(f"Time for detect_green_frames: {time.perf_counter() - start:.3f} seconds")

start = time.perf_counter()
full_mask = _full_canvas_mask(template_folder, "mask.png", background.size, {"width": 662, "height": 429, "x": 163, "y": 462})
print(f"Time for _full_canvas_mask: {time.perf_counter() - start:.3f} seconds")

# Let's get raw_artwork_area
_, manifest = load_manifest(templates_folder, template_id)
raw_artwork_area = manifest.get("raw_artwork_area")

start = time.perf_counter()
detection = detection_from_mask(full_mask, raw_artwork_area, settings)
print(f"Time for detection_from_mask: {time.perf_counter() - start:.3f} seconds")

start = time.perf_counter()
composed = render_green_frame_mockup(background, artwork, settings, detection)
print(f"Time for render_green_frame_mockup: {time.perf_counter() - start:.3f} seconds")
