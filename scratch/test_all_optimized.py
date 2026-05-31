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

# Set settings
effects = None
fit_mode = "cover"
settings = parse_green_frame_settings(effects, fit_mode)

# Detect
full_mask = _full_canvas_mask(template_folder, "mask.png", background.size, {"width": 662, "height": 429, "x": 163, "y": 462})
_, manifest = load_manifest(templates_folder, template_id)
raw_artwork_area = manifest.get("raw_artwork_area")
detection = detection_from_mask(full_mask, raw_artwork_area, settings)

# --- Optimized implementations ---

def optimized_suppress_green_halo(base: np.ndarray, state: GreenFrameDetection) -> None:
    mask_idx = state.clip_mask & (state.soft_mask > 0.001) & (state.soft_mask < 0.999)
    if not np.any(mask_idx):
        return
    r = base[mask_idx, 0].astype(np.float32)
    g = base[mask_idx, 1].astype(np.float32)
    b = base[mask_idx, 2].astype(np.float32)
    
    valid = (g > r + 8) & (g > b + 8)
    if not np.any(valid):
        return
        
    r_val = r[valid]
    g_val = g[valid]
    b_val = b[valid]
    
    soft = state.soft_mask[mask_idx][valid].astype(np.float32)
    strength = np.clip((1.0 - soft) * 2.4, 0.0, 1.0)
    rb_avg = (r_val + b_val) / 2.0
    
    ys, xs = np.where(mask_idx)
    ys = ys[valid]
    xs = xs[valid]
    
    base[ys, xs, 0] = np.round(r_val * (1.0 - strength) + rb_avg * strength * 0.22)
    base[ys, xs, 1] = np.round(g_val * (1.0 - strength) + np.minimum(g_val, rb_avg + 10.0) * strength)
    base[ys, xs, 2] = np.round(b_val * (1.0 - strength) + rb_avg * strength * 0.22)

def optimized_draw_rect(base: np.ndarray, region: GreenRegion, art: Image.Image, state: GreenFrameDetection, settings: GreenFrameSettings) -> None:
    from services.green_frame_mockup_service import _source_image
    h, w = region.h, region.w
    base_crop = base[region.y : region.y + h, region.x : region.x + w].astype(np.float32)
    repl = np.asarray(_source_image(art, w, h, settings).convert("RGBA")).astype(np.float32)
    clip_mask = state.clip_mask[region.y : region.y + h, region.x : region.x + w]
    soft_mask = state.soft_mask[region.y : region.y + h, region.x : region.x + w]
    
    source_alpha = repl[:, :, 3] / 255.0
    alpha = soft_mask * source_alpha
    valid = clip_mask & (alpha > 0.001)
    if not np.any(valid):
        return
        
    sr = repl[valid, 0]
    sg = repl[valid, 1]
    sb = repl[valid, 2]
    
    bg_r = base_crop[valid, 0]
    bg_g = base_crop[valid, 1]
    bg_b = base_crop[valid, 2]
    
    a = alpha[valid]
    inv = 1.0 - a
    
    blend_idx = a < 0.999
    if np.any(blend_idx):
        bgr = bg_r[blend_idx]
        bgg = bg_g[blend_idx]
        bgb = bg_b[blend_idx]
        
        s_r = sr[blend_idx]
        s_g = sg[blend_idx]
        s_b = sb[blend_idx]
        
        in_v = inv[blend_idx]
        spill = (bgg > bgr + 20) & (bgg > bgb + 20)
        
        if np.any(spill):
            preserve = np.clip(in_v[spill] * 0.12, 0.0, 1.0)
            bgr[spill] = s_r[spill] * (1.0 - preserve) + bgr[spill] * preserve
            bgg[spill] = s_g[spill] * (1.0 - preserve) + bgg[spill] * preserve
            bgb[spill] = s_b[spill] * (1.0 - preserve) + bgb[spill] * preserve
            
        no_spill = ~spill
        if np.any(no_spill):
            max_rb = np.maximum(bgr[no_spill], bgb[no_spill])
            neutral_g = np.minimum(bgg[no_spill], max_rb + 14.0)
            edge_mix = np.clip(in_v[no_spill] * 1.1, 0.0, 1.0)
            bgr[no_spill] = bgr[no_spill] * (1.0 - edge_mix) + s_r[no_spill] * edge_mix
            bgg[no_spill] = neutral_g * (1.0 - edge_mix) + s_g[no_spill] * edge_mix
            bgb[no_spill] = bgb[no_spill] * (1.0 - edge_mix) + s_b[no_spill] * edge_mix
            
        bg_r[blend_idx] = bgr
        bg_g[blend_idx] = bgg
        bg_b[blend_idx] = bgb
        
    base_crop[valid, 0] = np.round(sr * a + bg_r * inv)
    base_crop[valid, 1] = np.round(sg * a + bg_g * inv)
    base_crop[valid, 2] = np.round(sb * a + bg_b * inv)
    base_crop[valid, 3] = 255.0
    
    base[region.y : region.y + h, region.x : region.x + w] = np.clip(base_crop, 0, 255).astype(np.uint8)

def optimized_render_perspective(region: GreenRegion, art: Image.Image, state: GreenFrameDetection, settings: GreenFrameSettings) -> Image.Image | None:
    inner = region.inner_corners or region.corners
    outer = region.outer_corners or region.corners
    if not inner:
        return None
    warp = _expanded_quad(outer, max(2, settings.edge_expand + 2)) if settings.wide_coverage_envelope and outer else inner
    target_w = max(2, round(max(_dist(warp["tl"], warp["tr"]), _dist(warp["bl"], warp["br"]))))
    target_h = max(2, round(max(_dist(warp["tl"], warp["bl"]), _dist(warp["tr"], warp["br"]))))
    
    from services.green_frame_mockup_service import _source_image
    src = _source_image(art, target_w, target_h, settings)
    
    # Pad source image by 4 pixels to prevent edge bleeding/transparency under PIL perspective warp
    pad = 4
    W, H = src.width, src.height
    padded = Image.new("RGBA", (W + 2 * pad, H + 2 * pad))
    padded.paste(src, (pad, pad))
    
    # Repeat edges (top, bottom, left, right)
    left = padded.crop((pad, pad, pad + 1, pad + H))
    padded.paste(left.resize((pad, H), Image.Resampling.NEAREST), (0, pad))
    
    right = padded.crop((pad + W - 1, pad, pad + W, pad + H))
    padded.paste(right.resize((pad, H), Image.Resampling.NEAREST), (pad + W, pad))
    
    top = padded.crop((0, pad, W + 2 * pad, pad + 1))
    padded.paste(top.resize((W + 2 * pad, pad), Image.Resampling.NEAREST), (0, 0))
    
    bottom = padded.crop((0, pad + H - 1, W + 2 * pad, pad + H))
    padded.paste(bottom.resize((W + 2 * pad, pad), Image.Resampling.NEAREST), (0, pad + H))
    
    src = padded
    
    ss = max(1, settings.aa_scale)
    
    # Scale destination points for supersampling
    src_pts = [
        {"x": float(pad), "y": float(pad)},
        {"x": float(pad + W - 1), "y": float(pad)},
        {"x": float(pad + W - 1), "y": float(pad + H - 1)},
        {"x": float(pad), "y": float(pad + H - 1)},
    ]
    dst_pts = [warp["tl"], warp["tr"], warp["br"], warp["bl"]]
    dst_pts_scaled = [{"x": p["x"] * ss, "y": p["y"] * ss} for p in dst_pts]
    
    h = _homography(src_pts, dst_pts_scaled)
    if h is None:
        return None
    try:
        inv = np.linalg.inv(h.reshape(3, 3)).reshape(9)
        if abs(inv[8]) > 1e-9:
            inv = inv / inv[8]
        else:
            return None
    except np.linalg.LinAlgError:
        return None
        
    coefficients = inv[:8]
    # Define padded crop bounding box to prevent edge bleeding on subsequent resize
    crop_pad = 4 if state.width >= 100 else 0
    cx0 = max(0, region.x - crop_pad)
    cy0 = max(0, region.y - crop_pad)
    cx1 = min(state.width, region.x + region.w + crop_pad)
    cy1 = min(state.height, region.y + region.h + crop_pad)
    cw, ch = cx1 - cx0, cy1 - cy0
    
    out_w, out_h = max(1, round(cw * ss)), max(1, round(ch * ss))
    
    # Warp the source image to the scaled canvas size using compiled C code (lightning fast!)
    warped_full = src.transform(
        (state.width * ss, state.height * ss),
        Image.Transform.PERSPECTIVE,
        coefficients,
        Image.Resampling.BICUBIC,
    )
    
    # Crop to the scaled region bounding box (with padding)
    rx0, ry0 = round(cx0 * ss), round(cy0 * ss)
    warped_region = warped_full.crop((rx0, ry0, rx0 + out_w, ry0 + out_h))
    
    # Downscale the warped region to target size (cw, ch) BEFORE applying soft mask and un-padding
    if ss > 1:
        warped_region = warped_region.resize((cw, ch), Image.Resampling.BICUBIC)
        
    # Crop it back to the exact un-padded size (region.w, region.h)
    x_offset = region.x - cx0
    y_offset = region.y - cy0
    warped_region = warped_region.crop((x_offset, y_offset, x_offset + region.w, y_offset + region.h))
    
    # Apply the soft mask at target scale (no resize needed for the mask!)
    soft_mask_np = state.soft_mask[region.y : region.y + region.h, region.x : region.x + region.w]
    soft_mask_uint8 = np.clip(soft_mask_np * 255.0, 0, 255).astype(np.uint8)
    soft_mask_region_img = Image.fromarray(soft_mask_uint8, mode="L")
    
    blur_radius = round(settings.edge_aa_radius)
    if blur_radius > 0:
        soft_mask_region_img = soft_mask_region_img.filter(ImageFilter.BoxBlur(blur_radius))
        
    final_alpha = ImageChops.multiply(warped_region.getchannel("A"), soft_mask_region_img)
    warped_region.putalpha(final_alpha)
    
    return warped_region

# --- End of optimized implementations ---

# Test _suppress_green_halo speed
from services.green_frame_mockup_service import _suppress_green_halo as original_suppress_green_halo

base_orig = np.asarray(background.convert("RGBA")).copy()
base_opt = base_orig.copy()

print("Timing original _suppress_green_halo...")
t0 = time.perf_counter()
original_suppress_green_halo(base_orig, detection)
print(f"Original _suppress_green_halo: {time.perf_counter() - t0:.4f} seconds")

print("Timing optimized _suppress_green_halo...")
t0 = time.perf_counter()
optimized_suppress_green_halo(base_opt, detection)
print(f"Optimized _suppress_green_halo: {time.perf_counter() - t0:.4f} seconds")
print(f"Suppress diff: {np.abs(base_orig.astype(int) - base_opt.astype(int)).max()}")

# Test _draw_rect speed
from services.green_frame_mockup_service import _draw_rect as original_draw_rect

base_orig_rect = base_orig.copy()
base_opt_rect = base_orig.copy()
region = detection.regions[0]

print("Timing original _draw_rect...")
t0 = time.perf_counter()
original_draw_rect(base_orig_rect, region, artwork, detection, settings)
print(f"Original _draw_rect: {time.perf_counter() - t0:.4f} seconds")

print("Timing optimized_draw_rect...")
t0 = time.perf_counter()
optimized_draw_rect(base_opt_rect, region, artwork, detection, settings)
print(f"Optimized _draw_rect: {time.perf_counter() - t0:.4f} seconds")

diff = np.abs(base_orig_rect.astype(int) - base_opt_rect.astype(int))
print(f"Draw rect diff - Max: {diff.max()}, Mean: {diff.mean():.4f}")
