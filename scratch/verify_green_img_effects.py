"""Verify artwork-targeted (IMG) effects now affect green-frame renders."""
import json
import copy
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops

from services.simple_mockup_service import render_simple_mockup

tid = "template_598d444a76f9"
manifest = json.loads(Path(f"templates_data/{tid}/manifest.json").read_text(encoding="utf-8"))
art = Path("scratch/effects_art.png")
Image.new("RGBA", (800, 600), (240, 240, 240, 255)).save(art)

def run(effects):
    return render_simple_mockup(
        template_id=tid,
        artwork_path=art,
        output_format="png",
        templates_folder=Path("templates_data"),
        output_folder=Path("scratch/effects_out"),
        fit_mode=None,
        realism=True,
        effects=effects,
        artwork_area=manifest.get("artwork_area"),
        raw_artwork_area=manifest.get("raw_artwork_area"),
        mask_name=manifest.get("mask"),
    )

base_effects = copy.deepcopy(manifest.get("effects") or {})
base_effects.pop("inner_shadow", None)
with_shadow = copy.deepcopy(base_effects)
with_shadow["inner_shadow"] = {
    "enabled": True, "top": 20, "bottom": 20, "left": 20, "right": 20,
    "opacity": 0.8, "blur": 10, "target": "artwork",
}

r1 = run(base_effects)
r2 = run(with_shadow)
img1 = Image.open(f"scratch/effects_out/{Path(r1.output_url).name}").convert("RGB")
img2 = Image.open(f"scratch/effects_out/{Path(r2.output_url).name}").convert("RGB")
diff = np.asarray(ImageChops.difference(img1, img2))
print("inner_shadow(IMG) diff -> max:", diff.max(), "changed px:", int((diff.max(axis=2) > 8).sum()))
assert diff.max() > 30, "Inner Frame Shadow (IMG) had no effect on green render!"
print("OK — IMG-targeted Inner Frame Shadow affects green mockups")
