"""Benchmark green-frame render end-to-end (detection + compose + save)."""
import json
import sys
import time
from pathlib import Path

from PIL import Image

from services.simple_mockup_service import render_simple_mockup

templates_folder = Path("templates_data")
template_id = sys.argv[1] if len(sys.argv) > 1 else "template_fa83e6c793fe"
manifest = json.loads((templates_folder / template_id / "manifest.json").read_text(encoding="utf-8"))
raw = manifest.get("raw_artwork_area")
print(f"template {template_id}: canvas {manifest['canvas_width']}x{manifest['canvas_height']}, "
      f"regions {len((raw or {}).get('regions', []))}")

art_path = Path("scratch/bench_art.png")
Image.effect_mandelbrot((1200, 900), (-2, -1.2, 0.8, 1.2), 60).convert("RGBA").save(art_path)

out = Path("scratch/bench_out")
for run in range(3):
    t0 = time.perf_counter()
    result = render_simple_mockup(
        template_id=template_id,
        artwork_path=art_path,
        output_format="png",
        templates_folder=templates_folder,
        output_folder=out,
        fit_mode=None,
        realism=True,
        effects=manifest.get("effects"),
        artwork_area=manifest.get("artwork_area"),
        raw_artwork_area=raw,
        mask_name=manifest.get("mask"),
    )
    print(f"run {run + 1}: {time.perf_counter() - t0:.2f}s -> {result.output_url}")
