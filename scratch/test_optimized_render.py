import sys
sys.path.append('.')
import time
from pathlib import Path
from PIL import Image, ImageChops, ImageFilter
import numpy as np

# Load mockup components
templates_folder = Path("draft_templates")
template_id = "template_44ab02ba0914"
template_folder = templates_folder / template_id

background = Image.open(template_folder / "background.png")
mask = Image.open(template_folder / "mask.png")
artwork = Image.new("RGBA", (800, 800), (255, 0, 0, 255))

from services.green_frame_mockup_service import (
    GreenFrameSettings, GreenRegion, GreenFrameDetection,
    parse_green_frame_settings, detect_green_frames,
    _expanded_quad, _dist, _homography
)
from services.simple_mockup_service import load_manifest, _full_canvas_mask, detection_from_mask

# Set settings to use perspective
effects = None
fit_mode = "cover"
settings = parse_green_frame_settings(effects, fit_mode)
settings.use_perspective = True # Force perspective to test the slowest part

# Detect
full_mask = _full_canvas_mask(template_folder, "mask.png", background.size, {"width": 662, "height": 429, "x": 163, "y": 462})
_, manifest = load_manifest(templates_folder, template_id)
raw_artwork_area = manifest.get("raw_artwork_area")
detection = detection_from_mask(full_mask, raw_artwork_area, settings)

from services.green_frame_mockup_service import _render_perspective_region as original_render_perspective

# Let's define the optimized render perspective function
def optimized_render_perspective(region: GreenRegion, art: Image.Image, state: GreenFrameDetection, settings: GreenFrameSettings) -> Image.Image | None:
    inner = region.inner_corners or region.corners
    outer = region.outer_corners or region.corners
    if not inner:
        return None
    warp = _expanded_quad(outer, max(2, settings.edge_expand + 2)) if settings.wide_coverage_envelope and outer else inner
    target_w = max(2, round(max(_dist(warp["tl"], warp["tr"]), _dist(warp["bl"], warp["br"]))))
    target_h = max(2, round(max(_dist(warp["tl"], warp["bl"]), _dist(warp["tr"], warp["br"]))))
    
    # We import _source_image inside
    from services.green_frame_mockup_service import _source_image
    src = _source_image(art, target_w, target_h, settings)
    
    ss = max(1, settings.aa_scale)
    
    # Scale destination points for supersampling
    src_pts = [{"x": 0.0, "y": 0.0}, {"x": float(src.width - 1), "y": 0.0}, {"x": float(src.width - 1), "y": float(src.height - 1)}, {"x": 0.0, "y": float(src.height - 1)}]
    dst_pts = [warp["tl"], warp["tr"], warp["br"], warp["bl"]]
    dst_pts_scaled = [{"x": p["x"] * ss, "y": p["y"] * ss} for p in dst_pts]
    
    h = _homography(src_pts, dst_pts_scaled)
    if h is None:
        return None
    try:
        inv = np.linalg.inv(h.reshape(3, 3)).reshape(9)
    except np.linalg.LinAlgError:
        return None
        
    coefficients = inv[:8]
    out_w, out_h = max(1, round(region.w * ss)), max(1, round(region.h * ss))
    
    # Warp the source image to the scaled canvas size using compiled C code (lightning fast!)
    warped_full = src.transform(
        (state.width * ss, state.height * ss),
        Image.Transform.PERSPECTIVE,
        coefficients,
        Image.Resampling.BICUBIC,
    )
    
    # Crop to the scaled region bounding box
    rx0, ry0 = round(region.x * ss), round(region.y * ss)
    warped_region = warped_full.crop((rx0, ry0, rx0 + out_w, ry0 + out_h))
    
    # Now process the soft mask
    soft_mask_np = state.soft_mask[region.y : region.y + region.h, region.x : region.x + region.w]
    soft_mask_uint8 = np.clip(soft_mask_np * 255.0, 0, 255).astype(np.uint8)
    soft_mask_region_img = Image.fromarray(soft_mask_uint8, mode="L")
    
    # Resize the mask to out_w, out_h
    soft_mask_region_scaled = soft_mask_region_img.resize((out_w, out_h), Image.Resampling.BILINEAR)
    
    # Apply optional edge antialiasing blur as in original code
    blur_radius = round(settings.edge_aa_radius * max(1, ss / 2))
    if blur_radius > 0:
        soft_mask_region_scaled = soft_mask_region_scaled.filter(ImageFilter.BoxBlur(blur_radius))
        
    # Multiply warped region's alpha channel with the soft mask
    final_alpha = ImageChops.multiply(warped_region.getchannel("A"), soft_mask_region_scaled)
    
    # Put the final alpha channel back into the warped region image
    warped_region.putalpha(final_alpha)
    
    return warped_region

region = detection.regions[0]

print("Timing original _render_perspective_region...")
t0 = time.perf_counter()
orig_res = original_render_perspective(region, artwork, detection, settings)
print(f"Original finished in {time.perf_counter() - t0:.4f} seconds")

print("Timing optimized_render_perspective...")
t0 = time.perf_counter()
opt_res = optimized_render_perspective(region, artwork, detection, settings)
print(f"Optimized finished in {time.perf_counter() - t0:.4f} seconds")

# Compare visual differences in visible pixels
orig_arr = np.asarray(orig_res.convert("RGBA")).astype(int)
opt_arr = np.asarray(opt_res.convert("RGBA")).astype(int)

# Mask of visible pixels (alpha > 5 in either)
visible = (orig_arr[:, :, 3] > 5) | (opt_arr[:, :, 3] > 5)

if np.any(visible):
    diff = np.abs(orig_arr[visible] - opt_arr[visible])
    print(f"Number of visible pixels: {np.sum(visible)}")
    print(f"Max visible pixel difference: {diff.max()}")
    print(f"Average visible pixel difference: {diff.mean():.4f}")
    # Print channel-wise differences
    for c, ch in enumerate(["R", "G", "B", "A"]):
        print(f"  Channel {ch} max difference: {diff[:, c].max()}, mean: {diff[:, c].mean():.4f}")
else:
    print("No visible pixels to compare!")

