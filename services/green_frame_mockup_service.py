from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np
from PIL import Image, ImageChops, ImageFilter

try:
    from scipy import ndimage
except ImportError:  # pragma: no cover - production/dev dependency guard
    ndimage = None


@dataclass
class GreenFrameSettings:
    use_perspective: bool = True
    wide_coverage_envelope: bool = True
    target_color: tuple[int, int, int] = (0, 255, 0)
    tolerance: int = 95
    min_area: int = 2500
    edge_expand: int = 0
    feather_radius: int = 2
    mask_build_quality: int = 2
    aa_scale: int = 1
    edge_aa_radius: int = 0
    fit_mode: str = "cover"
    artwork_scale: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    contain_bg: tuple[int, int, int] = (255, 255, 255)
    enable_inner_shadow: bool = False
    inner_shadow_strength: int = 35
    inner_shadow_size: int = 10


@dataclass
class GreenRegion:
    x: int
    y: int
    w: int
    h: int
    area: int
    corners: Optional[dict[str, dict[str, float]]] = None
    inner_corners: Optional[dict[str, dict[str, float]]] = None
    outer_corners: Optional[dict[str, dict[str, float]]] = None


@dataclass
class GreenFrameDetection:
    width: int
    height: int
    regions: list[GreenRegion]
    raw_mask: np.ndarray
    detect_mask: np.ndarray
    clip_mask: np.ndarray
    soft_mask: np.ndarray
    green_alpha_mask: np.ndarray
    green_count: int


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def parse_green_frame_settings(effects: dict | None, fallback_fit_mode: str = "cover") -> GreenFrameSettings:
    options = effects.get("green_frame_mockups", {}) if isinstance(effects, dict) else {}
    if not isinstance(options, dict):
        options = {}
    fit_mode = str(options.get("fit_mode") or fallback_fit_mode or "cover").lower()
    if fit_mode not in {"cover", "contain", "stretch"}:
        fit_mode = "cover"

    def number(name: str, fallback: float) -> float:
        try:
            return float(options.get(name, fallback))
        except (TypeError, ValueError):
            return fallback

    shadow_strength = number("inner_shadow_strength", 35)
    if shadow_strength <= 1:
        shadow_strength *= 100
    return GreenFrameSettings(
        use_perspective=bool(options.get("use_perspective", True)),
        wide_coverage_envelope=bool(options.get("use_vector_clip", options.get("wide_coverage_envelope", True))),
        tolerance=int(_clamp(number("tolerance", 95), 10, 220)),
        min_area=int(_clamp(number("min_area", 2500), 80, 200000)),
        edge_expand=int(_clamp(number("edge_expand", 0), 0, 24)),
        feather_radius=int(_clamp(number("feather_radius", 2), 0, 12)),
        mask_build_quality=int(_clamp(number("mask_build_quality", 2), 1, 3)),
        aa_scale=int(_clamp(number("aa_scale", 1), 1, 8)),
        edge_aa_radius=int(_clamp(number("edge_aa_radius", 0), 0, 6)),
        fit_mode=fit_mode,
        artwork_scale=_clamp(number("artwork_scale", 1.0), 0.1, 3.0),
        offset_x=_clamp(number("offset_x", 0.0), -1.0, 1.0),
        offset_y=_clamp(number("offset_y", 0.0), -1.0, 1.0),
        contain_bg=_parse_hex_rgb(options.get("contain_bg_color"), (255, 255, 255)),
        enable_inner_shadow=bool(options.get("enable_inner_shadow", False)),
        inner_shadow_strength=int(_clamp(shadow_strength, 0, 100)),
        inner_shadow_size=int(_clamp(number("inner_shadow_size", 10), 1, 30)),
    )


def _parse_hex_rgb(value: Any, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return fallback
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


def _dilate_mask(mask: np.ndarray, amount: int) -> np.ndarray:
    if amount <= 0:
        return mask.copy()
    if ndimage is None:
        src = mask.astype(bool)
        h, w = src.shape
        for _ in range(amount):
            padded = np.pad(src, 1, mode="constant", constant_values=False)
            out = np.zeros((h, w), dtype=bool)
            for dy in range(3):
                for dx in range(3):
                    out |= padded[dy : dy + h, dx : dx + w]
            src = out
        return src
    return ndimage.binary_dilation(mask.astype(bool), structure=np.ones((3, 3), dtype=bool), iterations=amount)


def _blur_float_field(field: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0:
        return field
    if ndimage is not None:
        size = radius * 2 + 1
        out = ndimage.uniform_filter1d(field.astype(np.float32), size=size, axis=1, mode="nearest")
        return ndimage.uniform_filter1d(out, size=size, axis=0, mode="nearest")
    return np.asarray(Image.fromarray(np.clip(field * 255, 0, 255).astype(np.uint8), "L").filter(ImageFilter.BoxBlur(radius)), dtype=np.float32) / 255.0


def _color_distance(rgb: np.ndarray, target: tuple[int, int, int]) -> np.ndarray:
    diff = rgb.astype(np.float32) - np.asarray(target, dtype=np.float32)
    return np.sqrt(np.sum(diff * diff, axis=2))


def _green_confidence(rgb: np.ndarray, target: tuple[int, int, int], tolerance: int) -> np.ndarray:
    dist = _color_distance(rgb, target)
    similarity = np.maximum(0.0, 1.0 - (dist / max(1, tolerance)))
    r = rgb[:, :, 0].astype(np.float32)
    g = rgb[:, :, 1].astype(np.float32)
    b = rgb[:, :, 2].astype(np.float32)
    dominance = np.clip((g - np.maximum(r, b)) / 90.0, 0.0, 1.0)
    brightness = np.clip((g - 60.0) / 120.0, 0.0, 1.0)
    return similarity * (0.65 + dominance * 0.35) * (0.7 + brightness * 0.3)


def _target_pixels(rgb: np.ndarray, target: tuple[int, int, int], tolerance: int) -> np.ndarray:
    dist = _color_distance(rgb, target)
    r = rgb[:, :, 0].astype(np.float32)
    g = rgb[:, :, 1].astype(np.float32)
    b = rgb[:, :, 2].astype(np.float32)
    return (dist <= tolerance) & (g > r * 1.22) & (g > b * 1.22) & (g > 95)


def _sample_grid(field: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    h, w = field.shape
    fx = np.clip(xs, 0.0, w - 1.001)
    fy = np.clip(ys, 0.0, h - 1.001)
    x0 = np.floor(fx).astype(np.int32)
    y0 = np.floor(fy).astype(np.int32)
    x1 = np.minimum(w - 1, x0 + 1)
    y1 = np.minimum(h - 1, y0 + 1)
    dx = fx - x0
    dy = fy - y0
    return (
        field[y0, x0] * (1 - dx) * (1 - dy)
        + field[y0, x1] * dx * (1 - dy)
        + field[y1, x0] * (1 - dx) * dy
        + field[y1, x1] * dx * dy
    ).astype(np.float32)


def _sample_float(field: np.ndarray, x: float, y: float) -> float:
    return float(_sample_grid(field, np.asarray([x], dtype=np.float32), np.asarray([y], dtype=np.float32))[0])


def _precision_soft_mask(region_mask: np.ndarray, alpha_mask: np.ndarray, settings: GreenFrameSettings) -> np.ndarray:
    q = int(_clamp(settings.mask_build_quality * 2, 2, 8))
    h, w = region_mask.shape
    hi_h = h * q
    hi_w = w * q
    hi = np.zeros((hi_h, hi_w), dtype=np.float32)
    xs = (np.arange(hi_w, dtype=np.float32) + 0.5) / q - 0.5
    region_float = region_mask.astype(np.float32)

    for hy in range(hi_h):
        sy = (hy + 0.5) / q - 0.5
        ys = np.full(hi_w, sy, dtype=np.float32)
        region_val = _sample_grid(region_float, xs, ys)
        alpha_val = _sample_grid(alpha_mask, xs, ys)
        v = np.clip(alpha_val, 0.0, 1.0)
        v[(region_val > 0.98) & (v > 0.72)] = 1.0
        v[region_val <= 0.001] = 0.0
        hi[hy] = v

    hi_radius = max(1, round(settings.feather_radius * q * 0.45)) if settings.feather_radius > 0 else 0
    if hi_radius > 0:
        hi = _blur_float_field(hi, hi_radius)
    out = hi.reshape(h, q, w, q).mean(axis=(1, 3)).astype(np.float32)
    out[out < 0.01] = 0.0
    return np.clip(out, 0.0, 1.0)


def _find_corners(mask: np.ndarray, region: GreenRegion) -> Optional[dict[str, dict[str, float]]]:
    sub = mask[region.y : region.y + region.h, region.x : region.x + region.w]
    ys, xs = np.where(sub)
    if len(xs) < 4:
        return None
    xs = xs + region.x
    ys = ys + region.y
    sums = xs + ys
    diffs = xs - ys
    return {
        "tl": {"x": float(xs[np.argmin(sums)]), "y": float(ys[np.argmin(sums)])},
        "tr": {"x": float(xs[np.argmax(diffs)]), "y": float(ys[np.argmax(diffs)])},
        "br": {"x": float(xs[np.argmax(sums)]), "y": float(ys[np.argmax(sums)])},
        "bl": {"x": float(xs[np.argmin(diffs)]), "y": float(ys[np.argmin(diffs)])},
    }


def _connected_regions(mask: np.ndarray, min_pixels: int) -> list[GreenRegion]:
    if ndimage is None:
        from collections import deque
        h, w = mask.shape
        visited = np.zeros((h, w), dtype=bool)
        regions = []
        for y in range(h):
            for x in range(w):
                if not mask[y, x] or visited[y, x]:
                    continue
                q = deque([(x, y)])
                visited[y, x] = True
                min_x = max_x = x
                min_y = max_y = y
                count = 0
                while q:
                    cx, cy = q.popleft()
                    count += 1
                    min_x, max_x = min(min_x, cx), max(max_x, cx)
                    min_y, max_y = min(min_y, cy), max(max_y, cy)
                    for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                        if 0 <= nx < w and 0 <= ny < h and mask[ny, nx] and not visited[ny, nx]:
                            visited[ny, nx] = True
                            q.append((nx, ny))
                if count >= min_pixels:
                    regions.append(GreenRegion(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1, count))
        return sorted(regions, key=lambda r: (round(r.y / 25), r.x))

    labels, count = ndimage.label(mask, structure=np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=np.uint8))
    objects = ndimage.find_objects(labels)
    regions: list[GreenRegion] = []
    for label_id, slices in enumerate(objects, start=1):
        if slices is None:
            continue
        ys, xs = slices
        area = int(np.count_nonzero(labels[ys, xs] == label_id))
        if area >= min_pixels:
            regions.append(GreenRegion(xs.start, ys.start, xs.stop - xs.start, ys.stop - ys.start, area))
    return sorted(regions, key=lambda r: (round(r.y / 25), r.x))


def detect_green_frames(mockup: Image.Image, settings: GreenFrameSettings | None = None) -> GreenFrameDetection:
    settings = settings or GreenFrameSettings()
    rgba = np.asarray(mockup.convert("RGBA"))
    rgb = rgba[:, :, :3]
    h, w = rgb.shape[:2]
    score = _green_confidence(rgb, settings.target_color, settings.tolerance)
    alpha = np.clip((score - 0.04) / 0.56, 0.0, 1.0).astype(np.float32)
    alpha[_target_pixels(rgb, settings.target_color, settings.tolerance)] = 1.0
    raw_mask = alpha >= 0.06
    detect_mask = _dilate_mask(raw_mask, settings.edge_expand)
    corner_mask = _dilate_mask(raw_mask, min(1, settings.edge_expand))
    min_area = max(80, min(settings.min_area, int(w * h * 0.5)))
    regions = _connected_regions(detect_mask, min_area)
    if not regions:
        regions = _connected_regions(detect_mask, max(8, int(w * h * 0.0001)))

    union = np.zeros((h, w), dtype=bool)
    for region in regions:
        union[region.y : region.y + region.h, region.x : region.x + region.w] |= detect_mask[
            region.y : region.y + region.h, region.x : region.x + region.w
        ]
    soft_mask = _soft_mask_for_regions(union, alpha, regions, settings)
    for region in regions:
        region.inner_corners = _find_corners(corner_mask, region)
        region.outer_corners = _find_corners(detect_mask, region)
        region.corners = region.inner_corners
    return GreenFrameDetection(w, h, regions, raw_mask, detect_mask, union, soft_mask, alpha, int(raw_mask.sum()))


def _soft_mask_for_regions(
    clip_mask: np.ndarray,
    alpha_mask: np.ndarray,
    regions: list[GreenRegion],
    settings: GreenFrameSettings,
) -> np.ndarray:
    h, w = clip_mask.shape
    out = np.zeros((h, w), dtype=np.float32)
    pad = max(2, settings.feather_radius + settings.edge_expand + 3)
    for region in regions:
        x0 = max(0, region.x - pad)
        y0 = max(0, region.y - pad)
        x1 = min(w, region.x + region.w + pad)
        y1 = min(h, region.y + region.h + pad)
        local_clip = clip_mask[y0:y1, x0:x1]
        if np.any(local_clip):
            local_soft = _precision_soft_mask(local_clip, alpha_mask[y0:y1, x0:x1], settings)
            out[y0:y1, x0:x1] = np.maximum(out[y0:y1, x0:x1], local_soft)
    return out


def green_detection_raw(state: GreenFrameDetection, edge_expand: int = 0) -> dict[str, Any]:
    regions = []
    for region in state.regions:
        regions.append(
            {
                "x": region.x,
                "y": region.y,
                "width": region.w,
                "height": region.h,
                "area": region.area,
                "corners": _corners_to_list(region.corners),
                "inner_corners": _corners_to_list(region.inner_corners),
                "outer_corners": _corners_to_list(region.outer_corners),
            }
        )
    first = regions[0]["corners"] if regions else []
    return {
        "mode": "green_frames_mockups",
        "green_pixels": state.green_count,
        "edge_expand": edge_expand,
        "regions": regions,
        "original_corners": first,
    }


def green_mask_image(state: GreenFrameDetection) -> Image.Image:
    return Image.fromarray(np.where(state.detect_mask, 255, 0).astype(np.uint8), "L")


def detection_from_mask(
    mask: Image.Image,
    raw_artwork_area: dict | None,
    settings: GreenFrameSettings,
) -> GreenFrameDetection:
    alpha = (np.asarray(mask.convert("L"), dtype=np.float32) / 255.0).clip(0, 1)
    raw_mask = alpha >= 0.06
    detect_mask = _dilate_mask(raw_mask, settings.edge_expand)
    raw_regions = raw_artwork_area.get("regions") if isinstance(raw_artwork_area, dict) else None
    regions: list[GreenRegion] = []
    has_raw_regions = isinstance(raw_regions, list) and bool(raw_regions)
    if isinstance(raw_regions, list):
        for item in raw_regions:
            if not isinstance(item, dict):
                continue
            try:
                region = GreenRegion(
                    int(item["x"]),
                    int(item["y"]),
                    int(item.get("width", item.get("w"))),
                    int(item.get("height", item.get("h"))),
                    int(item.get("area", 0)),
                )
            except (KeyError, TypeError, ValueError):
                continue
            region.corners = _list_to_corners(item.get("corners") or item.get("inner_corners"))
            region.inner_corners = _list_to_corners(item.get("inner_corners")) or region.corners
            region.outer_corners = _list_to_corners(item.get("outer_corners")) or region.corners
            regions.append(region)
    if not regions:
        regions = _connected_regions(detect_mask, max(8, min(settings.min_area, int(raw_mask.size * 0.5))))
    union = np.zeros(raw_mask.shape, dtype=bool)
    for region in regions:
        union[region.y : region.y + region.h, region.x : region.x + region.w] |= detect_mask[
            region.y : region.y + region.h, region.x : region.x + region.w
        ]
        if region.corners is None and not has_raw_regions:
            region.inner_corners = _find_corners(raw_mask, region)
            region.outer_corners = _find_corners(detect_mask, region)
            region.corners = region.inner_corners
    if has_raw_regions and not any(region.corners for region in regions):
        soft_mask = union.astype(np.float32)
    else:
        soft_mask = _soft_mask_for_regions(union, alpha.astype(np.float32), regions, settings)
    return GreenFrameDetection(mask.width, mask.height, regions, raw_mask, detect_mask, union, soft_mask, alpha.astype(np.float32), int(raw_mask.sum()))


def _list_to_corners(points: Any) -> Optional[dict[str, dict[str, float]]]:
    if not isinstance(points, list) or len(points) != 4:
        return None
    try:
        return {
            "tl": {"x": float(points[0]["x"]), "y": float(points[0]["y"])},
            "tr": {"x": float(points[1]["x"]), "y": float(points[1]["y"])},
            "br": {"x": float(points[2]["x"]), "y": float(points[2]["y"])},
            "bl": {"x": float(points[3]["x"]), "y": float(points[3]["y"])},
        }
    except (KeyError, TypeError, ValueError):
        return None


def _corners_to_list(corners: Optional[dict[str, dict[str, float]]]) -> list[dict[str, int]]:
    if not corners:
        return []
    return [{"x": int(round(corners[key]["x"])), "y": int(round(corners[key]["y"]))} for key in ("tl", "tr", "br", "bl")]


def _source_image(art: Image.Image, target_w: int, target_h: int, settings: GreenFrameSettings) -> Image.Image:
    target_w = max(2, int(round(target_w)))
    target_h = max(2, int(round(target_h)))
    art = art.convert("RGBA")
    scaled_w = max(1, int(round(target_w * settings.artwork_scale)))
    scaled_h = max(1, int(round(target_h * settings.artwork_scale)))
    if settings.fit_mode == "stretch":
        canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
        resized = art.resize((scaled_w, scaled_h), Image.Resampling.BICUBIC)
        dx = int(round((target_w - scaled_w) / 2 + settings.offset_x * target_w / 2))
        dy = int(round((target_h - scaled_h) / 2 + settings.offset_y * target_h / 2))
        canvas.alpha_composite(resized, (dx, dy))
        return canvas
    image_ratio = art.width / art.height
    box_ratio = target_w / target_h
    if settings.fit_mode == "contain":
        if image_ratio > box_ratio:
            draw_w, draw_h = scaled_w, int(round(scaled_w / image_ratio))
        else:
            draw_h, draw_w = scaled_h, int(round(scaled_h * image_ratio))
        canvas = Image.new("RGBA", (target_w, target_h), settings.contain_bg + (255,))
    else:
        if image_ratio > box_ratio:
            draw_h, draw_w = scaled_h, int(round(scaled_h * image_ratio))
        else:
            draw_w, draw_h = scaled_w, int(round(scaled_w / image_ratio))
        canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
    resized = art.resize((max(1, draw_w), max(1, draw_h)), Image.Resampling.BICUBIC)
    canvas.alpha_composite(
        resized,
        (
            int(round((target_w - draw_w) / 2 + settings.offset_x * target_w / 2)),
            int(round((target_h - draw_h) / 2 + settings.offset_y * target_h / 2)),
        ),
    )
    return canvas


def _blend_pixel(base: np.ndarray, y: int, x: int, sr: float, sg: float, sb: float, alpha: float) -> None:
    inv = 1.0 - alpha
    bg_r, bg_g, bg_b = (float(base[y, x, 0]), float(base[y, x, 1]), float(base[y, x, 2]))
    if alpha < 0.999:
        if bg_g > bg_r + 20 and bg_g > bg_b + 20:
            preserve = _clamp(inv * 0.12, 0.0, 1.0)
            bg_r = sr * (1 - preserve) + bg_r * preserve
            bg_g = sg * (1 - preserve) + bg_g * preserve
            bg_b = sb * (1 - preserve) + bg_b * preserve
        else:
            neutral_g = min(bg_g, max(bg_r, bg_b) + 14)
            edge_mix = _clamp(inv * 1.1, 0.0, 1.0)
            bg_r = bg_r * (1 - edge_mix) + sr * edge_mix
            bg_g = neutral_g * (1 - edge_mix) + sg * edge_mix
            bg_b = bg_b * (1 - edge_mix) + sb * edge_mix
    base[y, x, 0] = round(sr * alpha + bg_r * inv)
    base[y, x, 1] = round(sg * alpha + bg_g * inv)
    base[y, x, 2] = round(sb * alpha + bg_b * inv)
    base[y, x, 3] = 255


def _suppress_green_halo(base: np.ndarray, state: GreenFrameDetection) -> None:
    ys, xs = np.where(state.clip_mask & (state.soft_mask > 0.001) & (state.soft_mask < 0.999))
    for y, x in zip(ys, xs):
        r, g, b = float(base[y, x, 0]), float(base[y, x, 1]), float(base[y, x, 2])
        if g <= r + 8 and g <= b + 8:
            continue
        strength = _clamp((1.0 - float(state.soft_mask[y, x])) * 2.4, 0.0, 1.0)
        rb_avg = (r + b) / 2.0
        base[y, x, 0] = round(r * (1 - strength) + rb_avg * strength * 0.22)
        base[y, x, 1] = round(g * (1 - strength) + min(g, rb_avg + 10) * strength)
        base[y, x, 2] = round(b * (1 - strength) + rb_avg * strength * 0.22)


def render_green_frame_mockup(
    background: Image.Image,
    artwork: Image.Image | list[Image.Image],
    settings: GreenFrameSettings,
    detection: GreenFrameDetection | None = None,
) -> Image.Image:
    state = detection or detect_green_frames(background, settings)
    base = np.asarray(background.convert("RGBA")).copy()
    _suppress_green_halo(base, state)
    result = Image.fromarray(base, "RGBA")
    base_after_rect = np.asarray(result).copy()
    overlays: list[tuple[Image.Image, int, int, int, int]] = []
    shadows: list[tuple[Image.Image, GreenRegion]] = []
    artworks = artwork if isinstance(artwork, list) else [artwork]
    if not artworks:
        return result

    for idx, region in enumerate(state.regions):
        region_art = artworks[idx % len(artworks)]
        if settings.use_perspective:
            overlay_data = _render_perspective_region(region, region_art, state, settings)
            if overlay_data is not None:
                overlays.append(overlay_data)
            else:
                _draw_rect(base_after_rect, region, region_art, state, settings)
        else:
            _draw_rect(base_after_rect, region, region_art, state, settings)
        shadow = _inner_shadow(region, state, settings)
        if shadow is not None:
            shadows.append((shadow, region))

    result = Image.fromarray(base_after_rect, "RGBA")
    for overlay, cx0, cy0, cw, ch in overlays:
        result.alpha_composite(overlay, (cx0, cy0))
    for shadow, region in shadows:
        result.alpha_composite(shadow, (region.x, region.y))
    return result


def _draw_rect(base: np.ndarray, region: GreenRegion, art: Image.Image, state: GreenFrameDetection, settings: GreenFrameSettings) -> None:
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


def _dist(a: dict[str, float], b: dict[str, float]) -> float:
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def _expanded_quad(c: dict[str, dict[str, float]], amount: float) -> dict[str, dict[str, float]]:
    center = {
        "x": (c["tl"]["x"] + c["tr"]["x"] + c["br"]["x"] + c["bl"]["x"]) / 4.0,
        "y": (c["tl"]["y"] + c["tr"]["y"] + c["br"]["y"] + c["bl"]["y"]) / 4.0,
    }
    out = {}
    for key in ("tl", "tr", "br", "bl"):
        dx = c[key]["x"] - center["x"]
        dy = c[key]["y"] - center["y"]
        length = math.hypot(dx, dy) or 1.0
        out[key] = {"x": c[key]["x"] + dx / length * amount, "y": c[key]["y"] + dy / length * amount}
    return out


def _homography(src: list[dict[str, float]], dst: list[dict[str, float]]) -> Optional[np.ndarray]:
    rows, vals = [], []
    for s, d in zip(src, dst):
        x, y, u, v = s["x"], s["y"], d["x"], d["y"]
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); vals.append(u)
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y]); vals.append(v)
    try:
        h = np.linalg.solve(np.asarray(rows, dtype=np.float64), np.asarray(vals, dtype=np.float64))
    except np.linalg.LinAlgError:
        return None
    return np.asarray([h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7], 1.0], dtype=np.float64)


def _apply_h(m: np.ndarray, x: float, y: float) -> tuple[float, float]:
    den = m[6] * x + m[7] * y + m[8]
    return ((m[0] * x + m[1] * y + m[2]) / den, (m[3] * x + m[4] * y + m[5]) / den)


def _sample_rgb(src: np.ndarray, x: float, y: float) -> tuple[float, float, float]:
    h, w = src.shape[:2]
    fx = min(w - 1.001, max(0.0, x))
    fy = min(h - 1.001, max(0.0, y))
    x0, y0 = int(math.floor(fx)), int(math.floor(fy))
    x1, y1 = min(w - 1, x0 + 1), min(h - 1, y0 + 1)
    dx, dy = fx - x0, fy - y0
    val = (
        src[y0, x0, :3] * (1 - dx) * (1 - dy)
        + src[y0, x1, :3] * dx * (1 - dy)
        + src[y1, x0, :3] * (1 - dx) * dy
        + src[y1, x1, :3] * dx * dy
    )
    return float(val[0]), float(val[1]), float(val[2])


def _sample_rgba(src: np.ndarray, x: float, y: float) -> tuple[float, float, float, float]:
    h, w = src.shape[:2]
    fx = min(w - 1.001, max(0.0, x))
    fy = min(h - 1.001, max(0.0, y))
    x0, y0 = int(math.floor(fx)), int(math.floor(fy))
    x1, y1 = min(w - 1, x0 + 1), min(h - 1, y0 + 1)
    dx, dy = fx - x0, fy - y0
    val = (
        src[y0, x0, :4] * (1 - dx) * (1 - dy)
        + src[y0, x1, :4] * dx * (1 - dy)
        + src[y1, x0, :4] * (1 - dx) * dy
        + src[y1, x1, :4] * dx * dy
    )
    return float(val[0]), float(val[1]), float(val[2]), float(val[3])


def _render_perspective_region(region: GreenRegion, art: Image.Image, state: GreenFrameDetection, settings: GreenFrameSettings) -> Optional[tuple[Image.Image, int, int, int, int]]:
    inner = region.inner_corners or region.corners
    outer = region.outer_corners or region.corners
    if not inner:
        return None
    if settings.wide_coverage_envelope and outer:
        expansion_amount = max(10, settings.edge_expand + 8) if state.width >= 100 else max(2, settings.edge_expand + 2)
        warp = _expanded_quad(outer, expansion_amount)
    else:
        warp = inner
    target_w = max(2, round(max(_dist(warp["tl"], warp["tr"]), _dist(warp["bl"], warp["br"]))))
    target_h = max(2, round(max(_dist(warp["tl"], warp["bl"]), _dist(warp["tr"], warp["br"]))))
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
    crop_pad = max(12, settings.feather_radius + settings.edge_expand + 6) if state.width >= 100 else 0
    cx0 = max(0, region.x - crop_pad)
    cy0 = max(0, region.y - crop_pad)
    cx1 = min(state.width, region.x + region.w + crop_pad)
    cy1 = min(state.height, region.y + region.h + crop_pad)
    cw, ch = cx1 - cx0, cy1 - cy0
    
    out_w, out_h = max(1, round(cw * ss)), max(1, round(ch * ss))
    
    # Warp the source image to the scaled canvas size using compiled C code
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
        
    # Apply the soft mask at target scale (no resize needed for the mask!)
    soft_mask_np = state.soft_mask[cy0:cy1, cx0:cx1]
    soft_mask_uint8 = np.clip(soft_mask_np * 255.0, 0, 255).astype(np.uint8)
    soft_mask_region_img = Image.fromarray(soft_mask_uint8, mode="L")
    
    blur_radius = round(settings.edge_aa_radius)
    if blur_radius > 0:
        soft_mask_region_img = soft_mask_region_img.filter(ImageFilter.BoxBlur(blur_radius))
        
    final_alpha = ImageChops.multiply(warped_region.getchannel("A"), soft_mask_region_img)
    warped_region.putalpha(final_alpha)
    
    return warped_region, cx0, cy0, cw, ch


def _inner_shadow(region: GreenRegion, state: GreenFrameDetection, settings: GreenFrameSettings) -> Optional[Image.Image]:
    if not settings.enable_inner_shadow or settings.inner_shadow_strength <= 0:
        return None
    w, h = region.w, region.h
    edge = np.zeros((h, w), dtype=np.float32)
    local = state.clip_mask[region.y : region.y + h, region.x : region.x + w]
    padded = np.pad(local, 1, mode="constant", constant_values=False)
    for yy in range(h):
        for xx in range(w):
            if local[yy, xx] and not np.all(padded[yy : yy + 3, xx : xx + 3]):
                edge[yy, xx] = 1.0
    alpha = np.clip(_blur_float_field(edge, settings.inner_shadow_size) * max(1, settings.inner_shadow_size * 1.2) * (settings.inner_shadow_strength / 100.0), 0, 0.85)
    alpha[~local] = 0
    out = np.zeros((h, w, 4), dtype=np.uint8)
    out[:, :, 3] = np.round(alpha * 255).astype(np.uint8)
    return Image.fromarray(out, "RGBA")
