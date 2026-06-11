import time
import json
from pathlib import Path
from PIL import Image
from services.simple_mockup_service import render_simple_mockup

tid = "template_598d444a76f9"
manifest = json.loads(Path(f"templates_data/{tid}/manifest.json").read_text(encoding="utf-8"))
art = Path("scratch/bench_art.png")
Image.effect_mandelbrot((1200, 900), (-2, -1.2, 0.8, 1.2), 60).convert("RGBA").save(art)
for run in range(3):
    t0 = time.perf_counter()
    render_simple_mockup(
        template_id=tid,
        artwork_path=art,
        output_format="png",
        templates_folder=Path("templates_data"),
        output_folder=Path("scratch/bench_out"),
        fit_mode=None,
        realism=False,
        effects=manifest.get("effects"),
        artwork_area=manifest.get("artwork_area"),
        raw_artwork_area=manifest.get("raw_artwork_area"),
        mask_name=manifest.get("mask"),
    )
    print(f"realism=false run {run + 1}: {time.perf_counter() - t0:.2f}s")
