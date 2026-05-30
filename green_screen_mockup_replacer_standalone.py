from __future__ import annotations

import math
import random
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageTk
from scipy import ndimage
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


Point = dict[str, float]
Corners = dict[str, Point]


@dataclass
class Region:
    x: int
    y: int
    w: int
    h: int
    area: int
    corners: Optional[Corners] = None
    inner_corners: Optional[Corners] = None
    outer_corners: Optional[Corners] = None


@dataclass
class Settings:
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
    contain_bg: tuple[int, int, int] = (255, 255, 255)
    show_mask: bool = False
    show_boxes: bool = False
    enable_inner_shadow: bool = False
    inner_shadow_strength: int = 35
    inner_shadow_size: int = 10


@dataclass
class DetectionState:
    width: int
    height: int
    regions: list[Region]
    raw_mask: np.ndarray
    detect_mask: np.ndarray
    clip_mask: np.ndarray
    soft_mask: np.ndarray
    green_alpha_mask: np.ndarray
    green_count: int


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def pil_rgba(path: Path) -> Image.Image:
    return Image.open(path).convert("RGBA")


def color_distance_rgb(rgb: np.ndarray, target: tuple[int, int, int]) -> np.ndarray:
    target_arr = np.asarray(target, dtype=np.float32)
    diff = rgb.astype(np.float32) - target_arr
    return np.sqrt(np.sum(diff * diff, axis=2))


def green_confidence(rgb: np.ndarray, target: tuple[int, int, int], tolerance: int) -> np.ndarray:
    dist = color_distance_rgb(rgb, target)
    similarity = np.maximum(0.0, 1.0 - (dist / max(1, tolerance)))

    r = rgb[:, :, 0].astype(np.float32)
    g = rgb[:, :, 1].astype(np.float32)
    b = rgb[:, :, 2].astype(np.float32)
    max_other = np.maximum(r, b)
    dominance = np.clip((g - max_other) / 90.0, 0.0, 1.0)
    brightness = np.clip((g - 60.0) / 120.0, 0.0, 1.0)

    return similarity * (0.65 + dominance * 0.35) * (0.7 + brightness * 0.3)


def is_target_pixel(rgb: np.ndarray, target: tuple[int, int, int], tolerance: int) -> np.ndarray:
    dist = color_distance_rgb(rgb, target)
    r = rgb[:, :, 0].astype(np.float32)
    g = rgb[:, :, 1].astype(np.float32)
    b = rgb[:, :, 2].astype(np.float32)
    return (dist <= tolerance) & (g > r * 1.22) & (g > b * 1.22) & (g > 95)


def dilate_mask(mask: np.ndarray, amount: int) -> np.ndarray:
    if amount <= 0:
        return mask.copy()
    return ndimage.binary_dilation(mask.astype(bool), structure=np.ones((3, 3), dtype=bool), iterations=amount)


def box_blur_horizontal(field: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0:
        return field
    height, width = field.shape
    out = np.empty_like(field, dtype=np.float32)
    window_size = radius * 2 + 1

    for y in range(height):
        row = field[y]
        total = 0.0
        for i in range(-radius, radius + 1):
            x = min(width - 1, max(0, i))
            total += float(row[x])
        for x in range(width):
            out[y, x] = total / window_size
            remove_x = max(0, x - radius)
            add_x = min(width - 1, x + radius + 1)
            total -= float(row[remove_x])
            total += float(row[add_x])
    return out


def box_blur_vertical(field: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0:
        return field
    height, width = field.shape
    out = np.empty_like(field, dtype=np.float32)
    window_size = radius * 2 + 1

    for x in range(width):
        col = field[:, x]
        total = 0.0
        for i in range(-radius, radius + 1):
            y = min(height - 1, max(0, i))
            total += float(col[y])
        for y in range(height):
            out[y, x] = total / window_size
            remove_y = max(0, y - radius)
            add_y = min(height - 1, y + radius + 1)
            total -= float(col[remove_y])
            total += float(col[add_y])
    return out


def blur_float_field(field: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0:
        return field
    size = radius * 2 + 1
    out = ndimage.uniform_filter1d(field.astype(np.float32), size=size, axis=1, mode="nearest")
    return ndimage.uniform_filter1d(out, size=size, axis=0, mode="nearest")


def sample_float_bilinear(field: np.ndarray, x: float, y: float) -> float:
    height, width = field.shape
    fx = min(width - 1.001, max(0.0, x))
    fy = min(height - 1.001, max(0.0, y))
    x0 = int(math.floor(fx))
    y0 = int(math.floor(fy))
    x1 = min(width - 1, x0 + 1)
    y1 = min(height - 1, y0 + 1)
    dx = fx - x0
    dy = fy - y0
    return float(
        field[y0, x0] * (1 - dx) * (1 - dy)
        + field[y0, x1] * dx * (1 - dy)
        + field[y1, x0] * (1 - dx) * dy
        + field[y1, x1] * dx * dy
    )


def sample_float_bilinear_grid(field: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    height, width = field.shape
    fx = np.clip(xs, 0.0, width - 1.001)
    fy = np.clip(ys, 0.0, height - 1.001)
    x0 = np.floor(fx).astype(np.int32)
    y0 = np.floor(fy).astype(np.int32)
    x1 = np.minimum(width - 1, x0 + 1)
    y1 = np.minimum(height - 1, y0 + 1)
    dx = fx - x0
    dy = fy - y0
    return (
        field[y0, x0] * (1 - dx) * (1 - dy)
        + field[y0, x1] * dx * (1 - dy)
        + field[y1, x0] * (1 - dx) * dy
        + field[y1, x1] * dx * dy
    ).astype(np.float32)


def build_precision_soft_mask(
    region_mask: np.ndarray,
    alpha_mask: np.ndarray,
    quality: int,
    feather: int,
) -> np.ndarray:
    q = int(clamp((quality or 2) * 2, 2, 8))
    feather_radius = max(0, int(feather or 0))
    height, width = region_mask.shape

    hi_h = height * q
    hi_w = width * q

    hi = np.zeros((hi_h, hi_w), dtype=np.float32)
    xs = (np.arange(hi_w, dtype=np.float32) + 0.5) / q - 0.5

    region_float = region_mask.astype(np.float32)
    for hy in range(hi_h):
        sy = (hy + 0.5) / q - 0.5
        ys = np.full(hi_w, sy, dtype=np.float32)
        region_val = sample_float_bilinear_grid(region_float, xs, ys)
        alpha_val = sample_float_bilinear_grid(alpha_mask, xs, ys)
        v = np.clip(alpha_val, 0.0, 1.0)
        v[(region_val > 0.98) & (v > 0.72)] = 1.0
        v[region_val <= 0.001] = 0.0
        hi[hy] = v

    hi_radius = max(1, round(feather_radius * q * 0.45)) if feather_radius > 0 else 0
    if hi_radius > 0:
        hi = blur_float_field(hi, hi_radius)

    out = hi.reshape(height, q, width, q).mean(axis=(1, 3)).astype(np.float32)
    out[out < 0.01] = 0.0
    return np.clip(out, 0.0, 1.0)


def find_corners_for_region(mask: np.ndarray, region: Region) -> Optional[Corners]:
    tl = tr = br = bl = None
    min_sum = float("inf")
    max_sum = -float("inf")
    min_diff = float("inf")
    max_diff = -float("inf")

    for y in range(region.y, region.y + region.h):
        row = mask[y]
        for x in range(region.x, region.x + region.w):
            if not row[x]:
                continue
            s = x + y
            d = x - y
            if s < min_sum:
                min_sum = s
                tl = {"x": float(x), "y": float(y)}
            if s > max_sum:
                max_sum = s
                br = {"x": float(x), "y": float(y)}
            if d > max_diff:
                max_diff = d
                tr = {"x": float(x), "y": float(y)}
            if d < min_diff:
                min_diff = d
                bl = {"x": float(x), "y": float(y)}

    if not (tl and tr and br and bl):
        return None
    return {"tl": tl, "tr": tr, "br": br, "bl": bl}


def find_connected_regions(mask: np.ndarray, min_pixels: int) -> list[Region]:
    structure = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=np.uint8)
    labels, count = ndimage.label(mask, structure=structure)
    if count == 0:
        return []

    objects = ndimage.find_objects(labels)
    regions: list[Region] = []

    for label_id, slices in enumerate(objects, start=1):
        if slices is None:
            continue
        ys, xs = slices
        area = int(np.count_nonzero(labels[ys, xs] == label_id))
        if area < min_pixels:
            continue
        region = Region(xs.start, ys.start, xs.stop - xs.start, ys.stop - ys.start, area)
        region.corners = find_corners_for_region(mask, region)
        regions.append(region)

    regions.sort(key=lambda r: (round(r.y / 25), r.x))
    return regions


def build_soft_mask_for_regions(
    clip_mask: np.ndarray,
    alpha_mask: np.ndarray,
    regions: list[Region],
    settings: Settings,
) -> np.ndarray:
    height, width = clip_mask.shape
    out = np.zeros((height, width), dtype=np.float32)
    if not regions:
        return out

    padding = max(2, int(settings.feather_radius) + int(settings.edge_expand) + 3)
    for region in regions:
        x0 = max(0, region.x - padding)
        y0 = max(0, region.y - padding)
        x1 = min(width, region.x + region.w + padding)
        y1 = min(height, region.y + region.h + padding)

        local_clip = clip_mask[y0:y1, x0:x1]
        if not np.any(local_clip):
            continue
        local_alpha = alpha_mask[y0:y1, x0:x1]
        local_soft = build_precision_soft_mask(
            local_clip,
            local_alpha,
            settings.mask_build_quality,
            settings.feather_radius,
        )
        out[y0:y1, x0:x1] = np.maximum(out[y0:y1, x0:x1], local_soft)
    return out


def detect_regions(mockup: Image.Image, settings: Settings) -> DetectionState:
    rgba = np.asarray(mockup.convert("RGBA"))
    rgb = rgba[:, :, :3]
    height, width = rgb.shape[:2]

    score = green_confidence(rgb, settings.target_color, settings.tolerance)
    target = is_target_pixel(rgb, settings.target_color, settings.tolerance)
    green_alpha = np.clip((score - 0.04) / 0.56, 0.0, 1.0).astype(np.float32)
    green_alpha[target] = 1.0

    raw_mask = green_alpha >= 0.06
    green_count = int(raw_mask.sum())
    detect_mask = dilate_mask(raw_mask, settings.edge_expand)
    corner_mask = dilate_mask(raw_mask, min(1, settings.edge_expand))

    regions = find_connected_regions(detect_mask, settings.min_area)

    region_union = np.zeros((height, width), dtype=bool)
    for region in regions:
        sub = detect_mask[region.y : region.y + region.h, region.x : region.x + region.w]
        region_union[region.y : region.y + region.h, region.x : region.x + region.w] |= sub

    clip_mask = region_union
    soft_mask = build_soft_mask_for_regions(clip_mask, green_alpha, regions, settings)

    for region in regions:
        region.inner_corners = find_corners_for_region(corner_mask, region)
        region.outer_corners = find_corners_for_region(detect_mask, region)
        region.corners = region.inner_corners

    return DetectionState(
        width=width,
        height=height,
        regions=regions,
        raw_mask=raw_mask,
        detect_mask=detect_mask,
        clip_mask=clip_mask,
        soft_mask=soft_mask,
        green_alpha_mask=green_alpha,
        green_count=green_count,
    )


def distance(a: Point, b: Point) -> float:
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def expand_point_from_center(point: Point, center: Point, amount: float) -> Point:
    dx = point["x"] - center["x"]
    dy = point["y"] - center["y"]
    length = math.hypot(dx, dy) or 1.0
    return {"x": point["x"] + (dx / length) * amount, "y": point["y"] + (dy / length) * amount}


def get_expanded_quad(corners: Corners, amount: float) -> Corners:
    center = {
        "x": (corners["tl"]["x"] + corners["tr"]["x"] + corners["br"]["x"] + corners["bl"]["x"]) / 4.0,
        "y": (corners["tl"]["y"] + corners["tr"]["y"] + corners["br"]["y"] + corners["bl"]["y"]) / 4.0,
    }
    return {
        "tl": expand_point_from_center(corners["tl"], center, amount),
        "tr": expand_point_from_center(corners["tr"], center, amount),
        "br": expand_point_from_center(corners["br"], center, amount),
        "bl": expand_point_from_center(corners["bl"], center, amount),
    }


def get_homography(src_pts: list[Point], dst_pts: list[Point]) -> Optional[np.ndarray]:
    rows = []
    vals = []
    for src, dst in zip(src_pts, dst_pts):
        x, y = src["x"], src["y"]
        u, v = dst["x"], dst["y"]
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        vals.append(u)
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        vals.append(v)
    try:
        h = np.linalg.solve(np.asarray(rows, dtype=np.float64), np.asarray(vals, dtype=np.float64))
    except np.linalg.LinAlgError:
        return None
    return np.asarray([h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7], 1.0], dtype=np.float64)


def apply_homography(matrix: np.ndarray, x: float, y: float) -> tuple[float, float]:
    denominator = matrix[6] * x + matrix[7] * y + matrix[8]
    return (
        (matrix[0] * x + matrix[1] * y + matrix[2]) / denominator,
        (matrix[3] * x + matrix[4] * y + matrix[5]) / denominator,
    )


def source_image(art: Image.Image, target_w: int, target_h: int, settings: Settings) -> Image.Image:
    target_w = max(2, int(round(target_w)))
    target_h = max(2, int(round(target_h)))
    art = art.convert("RGBA")

    if settings.fit_mode == "stretch":
        return art.resize((target_w, target_h), Image.Resampling.BICUBIC)

    image_ratio = art.width / art.height
    box_ratio = target_w / target_h

    if settings.fit_mode == "contain":
        if image_ratio > box_ratio:
            draw_w = target_w
            draw_h = int(round(target_w / image_ratio))
        else:
            draw_h = target_h
            draw_w = int(round(target_h * image_ratio))
        canvas = Image.new("RGBA", (target_w, target_h), settings.contain_bg + (255,))
    else:
        if image_ratio > box_ratio:
            draw_h = target_h
            draw_w = int(round(target_h * image_ratio))
        else:
            draw_w = target_w
            draw_h = int(round(target_w / image_ratio))
        canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))

    resized = art.resize((max(1, draw_w), max(1, draw_h)), Image.Resampling.BICUBIC)
    dx = int(round((target_w - draw_w) / 2))
    dy = int(round((target_h - draw_h) / 2))
    canvas.alpha_composite(resized, (dx, dy))
    return canvas


def sample_rgb_bilinear(src: np.ndarray, x: float, y: float) -> tuple[float, float, float]:
    height, width = src.shape[:2]
    fx = min(width - 1.001, max(0.0, x))
    fy = min(height - 1.001, max(0.0, y))
    x0 = int(math.floor(fx))
    y0 = int(math.floor(fy))
    x1 = min(width - 1, x0 + 1)
    y1 = min(height - 1, y0 + 1)
    dx = fx - x0
    dy = fy - y0
    value = (
        src[y0, x0, :3] * (1 - dx) * (1 - dy)
        + src[y0, x1, :3] * dx * (1 - dy)
        + src[y1, x0, :3] * (1 - dx) * dy
        + src[y1, x1, :3] * dx * dy
    )
    return float(value[0]), float(value[1]), float(value[2])


def blend_pixel(base: np.ndarray, y: int, x: int, sr: float, sg: float, sb: float, alpha: float) -> None:
    inv = 1.0 - alpha
    bg_r, bg_g, bg_b = (float(base[y, x, 0]), float(base[y, x, 1]), float(base[y, x, 2]))

    if alpha < 0.999:
        green_dominant = bg_g > bg_r + 20 and bg_g > bg_b + 20
        if green_dominant:
            preserve = clamp(inv * 0.12, 0.0, 1.0)
            bg_r = sr * (1 - preserve) + bg_r * preserve
            bg_g = sg * (1 - preserve) + bg_g * preserve
            bg_b = sb * (1 - preserve) + bg_b * preserve
        else:
            neutral_g = min(bg_g, max(bg_r, bg_b) + 14)
            edge_mix = clamp(inv * 1.1, 0.0, 1.0)
            bg_r = bg_r * (1 - edge_mix) + sr * edge_mix
            bg_g = neutral_g * (1 - edge_mix) + sg * edge_mix
            bg_b = bg_b * (1 - edge_mix) + sb * edge_mix

    base[y, x, 0] = round(sr * alpha + bg_r * inv)
    base[y, x, 1] = round(sg * alpha + bg_g * inv)
    base[y, x, 2] = round(sb * alpha + bg_b * inv)
    base[y, x, 3] = 255


def suppress_green_halo_on_base(base: np.ndarray, state: DetectionState) -> None:
    ys, xs = np.where(state.clip_mask & (state.soft_mask > 0.001) & (state.soft_mask < 0.999))
    for y, x in zip(ys, xs):
        r, g, b = float(base[y, x, 0]), float(base[y, x, 1]), float(base[y, x, 2])
        if g <= r + 8 and g <= b + 8:
            continue
        alpha = float(state.soft_mask[y, x])
        inv = 1.0 - alpha
        strength = clamp(inv * 2.4, 0.0, 1.0)
        rb_avg = (r + b) / 2.0
        target_g = min(g, rb_avg + 10)
        base[y, x, 0] = round(r * (1 - strength) + rb_avg * strength * 0.22)
        base[y, x, 1] = round(g * (1 - strength) + target_g * strength)
        base[y, x, 2] = round(b * (1 - strength) + rb_avg * strength * 0.22)


def draw_rect_into_base(base: np.ndarray, region: Region, art: Image.Image, state: DetectionState, settings: Settings) -> None:
    src = source_image(art, region.w, region.h, settings)
    replacement = np.asarray(src.convert("RGBA"))

    for yy in range(region.h):
        gy = region.y + yy
        for xx in range(region.w):
            gx = region.x + xx
            if not state.clip_mask[gy, gx]:
                continue
            alpha = float(state.soft_mask[gy, gx])
            if alpha <= 0.001:
                continue
            sr, sg, sb = replacement[yy, xx, :3]
            blend_pixel(base, gy, gx, float(sr), float(sg), float(sb), alpha)


def render_perspective_region(region: Region, art: Image.Image, state: DetectionState, settings: Settings) -> Optional[Image.Image]:
    inner = region.inner_corners or region.corners
    outer = region.outer_corners or region.corners
    if not inner or not all(inner.get(k) for k in ("tl", "tr", "br", "bl")):
        return None

    if settings.wide_coverage_envelope and outer and all(outer.get(k) for k in ("tl", "tr", "br", "bl")):
        coverage_pad = max(2, settings.edge_expand + 2)
        warp = get_expanded_quad(outer, coverage_pad)
    else:
        warp = inner

    target_w = max(2, round(max(distance(warp["tl"], warp["tr"]), distance(warp["bl"], warp["br"]))))
    target_h = max(2, round(max(distance(warp["tl"], warp["bl"]), distance(warp["tr"], warp["br"]))))
    src = source_image(art, target_w, target_h, settings)
    src_arr = np.asarray(src.convert("RGBA")).astype(np.float32)

    src_pts = [
        {"x": 0.0, "y": 0.0},
        {"x": float(src.width - 1), "y": 0.0},
        {"x": float(src.width - 1), "y": float(src.height - 1)},
        {"x": 0.0, "y": float(src.height - 1)},
    ]
    dst_pts = [warp["tl"], warp["tr"], warp["br"], warp["bl"]]
    homography = get_homography(src_pts, dst_pts)
    if homography is None:
        return None
    try:
        inv_h = np.linalg.inv(homography.reshape(3, 3)).reshape(9)
    except np.linalg.LinAlgError:
        return None

    ss = max(1, int(settings.aa_scale or 1))
    out_w = max(1, round(region.w * ss))
    out_h = max(1, round(region.h * ss))

    alpha_field = np.zeros((out_h, out_w), dtype=np.float32)
    rgb_field = np.zeros((out_h, out_w, 3), dtype=np.float32)

    for oy in range(out_h):
        dest_y = region.y + (oy + 0.5) / ss
        for ox in range(out_w):
            dest_x = region.x + (ox + 0.5) / ss
            alpha = sample_float_bilinear(state.soft_mask, dest_x, dest_y)
            if alpha <= 0.001:
                continue
            sx, sy = apply_homography(inv_h, dest_x, dest_y)
            if not (math.isfinite(sx) and math.isfinite(sy)):
                continue
            sx = min(src.width - 1.001, max(0.0, sx))
            sy = min(src.height - 1.001, max(0.0, sy))
            rgb_field[oy, ox] = sample_rgb_bilinear(src_arr, sx, sy)
            alpha_field[oy, ox] = alpha

    blur_radius = round(max(0, settings.edge_aa_radius) * max(1, ss / 2))
    if blur_radius > 0:
        alpha_field = blur_float_field(alpha_field, blur_radius)

    out = np.zeros((out_h, out_w, 4), dtype=np.uint8)
    out[:, :, :3] = np.clip(np.round(rgb_field), 0, 255).astype(np.uint8)
    out[:, :, 3] = np.clip(np.round(alpha_field * 255), 0, 255).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def render_inner_shadow(region: Region, state: DetectionState, settings: Settings) -> Optional[Image.Image]:
    if not settings.enable_inner_shadow:
        return None
    strength = clamp(settings.inner_shadow_strength, 0, 100) / 100.0
    blur_radius = max(1, int(settings.inner_shadow_size))
    if strength <= 0:
        return None

    w, h = region.w, region.h
    edge_field = np.zeros((h, w), dtype=np.float32)
    for yy in range(h):
        gy = region.y + yy
        for xx in range(w):
            gx = region.x + xx
            if not state.clip_mask[gy, gx]:
                continue
            is_edge = False
            for dy in range(-1, 2):
                for dx in range(-1, 2):
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = gx + dx, gy + dy
                    if nx < 0 or ny < 0 or nx >= state.width or ny >= state.height or not state.clip_mask[ny, nx]:
                        is_edge = True
                        break
                if is_edge:
                    break
            if is_edge:
                edge_field[yy, xx] = 1.0

    blurred = blur_float_field(edge_field, blur_radius)
    boost = max(1.0, blur_radius * 1.2)
    alpha = np.clip(blurred * boost * strength, 0.0, 0.85)
    alpha[~state.clip_mask[region.y : region.y + h, region.x : region.x + w]] = 0.0
    out = np.zeros((h, w, 4), dtype=np.uint8)
    out[:, :, 3] = np.round(alpha * 255).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def render_output(
    mockup: Image.Image,
    art: Optional[Image.Image | list[Image.Image]],
    state: DetectionState,
    settings: Settings,
) -> Image.Image:
    if settings.show_mask:
        mask = np.clip(np.round(state.soft_mask * 255), 0, 255).astype(np.uint8)
        return Image.fromarray(np.dstack([mask, mask, mask, np.full_like(mask, 255)]), "RGBA")

    base = np.asarray(mockup.convert("RGBA")).copy()
    suppress_green_halo_on_base(base, state)
    result = Image.fromarray(base, "RGBA")

    def art_for_region(index: int) -> Optional[Image.Image]:
        if isinstance(art, list):
            if not art:
                return None
            return art[index % len(art)]
        return art

    if art is not None:
        base_after_rect = np.asarray(result).copy()
        overlays: list[tuple[Image.Image, Region]] = []
        shadows: list[tuple[Image.Image, Region]] = []

        for idx, region in enumerate(state.regions):
            region_art = art_for_region(idx)
            if region_art is None:
                continue
            if settings.use_perspective:
                overlay = render_perspective_region(region, region_art, state, settings)
                if overlay is not None:
                    overlays.append((overlay, region))
                else:
                    draw_rect_into_base(base_after_rect, region, region_art, state, settings)
            else:
                draw_rect_into_base(base_after_rect, region, region_art, state, settings)

            shadow = render_inner_shadow(region, state, settings)
            if shadow is not None:
                shadows.append((shadow, region))

        result = Image.fromarray(base_after_rect, "RGBA")
        for overlay, region in overlays:
            resized = overlay.resize((region.w, region.h), Image.Resampling.BICUBIC)
            result.alpha_composite(resized, (region.x, region.y))
        for shadow, region in shadows:
            result.alpha_composite(shadow, (region.x, region.y))

    if settings.show_boxes:
        draw = ImageDraw.Draw(result)
        for idx, region in enumerate(state.regions, start=1):
            draw.rectangle([region.x, region.y, region.x + region.w, region.y + region.h], outline=(255, 0, 0, 220), width=3)
            draw.text((region.x + 8, region.y + 8), f"#{idx}", fill=(255, 0, 0, 255))
            c = region.corners
            if c:
                pts = [(c[k]["x"], c[k]["y"]) for k in ("tl", "tr", "br", "bl")]
                draw.line(pts + [pts[0]], fill=(0, 70, 255, 230), width=3)
                for px, py in pts:
                    draw.ellipse([px - 5, py - 5, px + 5, py + 5], fill=(0, 70, 255, 230))

    return result


class GreenScreenApp(tk.Tk):
    DETECTION_SETTING_KEYS = {
        "target_color",
        "tolerance",
        "min_area",
        "edge_expand",
        "feather_radius",
        "mask_build_quality",
    }

    def __init__(self) -> None:
        super().__init__()
        self.title("Green Screen Mockup Replacer - Python standalone")
        self.geometry("1320x860")
        self.minsize(1050, 700)

        self.mockup_path: Optional[Path] = None
        self.art_path: Optional[Path] = None
        self.mockup_image: Optional[Image.Image] = None
        self.art_image: Optional[Image.Image] = None
        self.art_pool: list[tuple[Path, Image.Image]] = []
        self.pool_assignments: list[int] = []
        self.state: Optional[DetectionState] = None
        self.result_image: Optional[Image.Image] = None
        self.preview_photo: Optional[ImageTk.PhotoImage] = None
        self._auto_after_id: Optional[str] = None
        self._busy = False

        self.vars: dict[str, tk.Variable] = {}
        self._build_ui()
        self._wire_auto_refresh()

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        controls_outer = ttk.Frame(root)
        controls_outer.grid(row=0, column=0, sticky="ns", padx=(0, 12))

        controls_canvas = tk.Canvas(controls_outer, width=360, highlightthickness=0)
        scrollbar = ttk.Scrollbar(controls_outer, orient="vertical", command=controls_canvas.yview)
        controls = ttk.Frame(controls_canvas)
        controls.bind("<Configure>", lambda _e: controls_canvas.configure(scrollregion=controls_canvas.bbox("all")))
        controls_canvas.create_window((0, 0), window=controls, anchor="nw")
        controls_canvas.configure(yscrollcommand=scrollbar.set)
        controls_canvas.pack(side="left", fill="y", expand=False)
        scrollbar.pack(side="right", fill="y")

        preview_frame = ttk.Frame(root)
        preview_frame.grid(row=0, column=1, sticky="nsew")
        preview_frame.rowconfigure(1, weight=1)
        preview_frame.columnconfigure(0, weight=1)

        title = ttk.Label(preview_frame, text="Preview", font=("Segoe UI", 14, "bold"))
        title.grid(row=0, column=0, sticky="w", pady=(0, 8))

        self.preview = ttk.Label(preview_frame, anchor="center", background="#eee9dc")
        self.preview.grid(row=1, column=0, sticky="nsew")
        self.status = ttk.Label(preview_frame, text="Load a mockup and artwork, then press Process.", anchor="w")
        self.status.grid(row=2, column=0, sticky="ew", pady=(8, 0))

        self._button(controls, "Load mockup", self.load_mockup)
        self._button(controls, "Load artwork", self.load_artwork)
        self._button(controls, "Load artwork pool", self.load_artwork_pool)
        self._button(controls, "Shuffle pool", self.shuffle_pool)
        self._button(controls, "Clear pool", self.clear_pool)
        self._button(controls, "Detect green regions", self.detect)
        self._button(controls, "Process", self.process)
        self._button(controls, "Save PNG", self.save_png)
        ttk.Separator(controls).pack(fill="x", pady=10)

        self._combo(controls, "Image mode", "image_mode", "same", ["same", "pool"])
        self._check(controls, "Perspective warp", "use_perspective", True)
        self._check(controls, "Wide coverage envelope", "wide_coverage_envelope", True)
        self._combo(controls, "Artwork fit", "fit_mode", "cover", ["cover", "contain", "stretch"])
        self._color_entry(controls, "Target green color", "target_color", "#00ff00")
        self._slider(controls, "Tolerance", "tolerance", 10, 220, 95)
        self._slider(controls, "Min area", "min_area", 100, 80000, 2500)
        self._slider(controls, "Green edge cleanup", "edge_expand", 0, 8, 0)
        self._slider(controls, "Mask feather", "feather_radius", 0, 12, 2)
        self._slider(controls, "Mask quality", "mask_build_quality", 1, 3, 2)
        self._slider(controls, "Supersampling", "aa_scale", 1, 8, 1)
        self._slider(controls, "Edge AA", "edge_aa_radius", 0, 6, 0)
        self._color_entry(controls, "Contain background", "contain_bg", "#ffffff")
        self._check(controls, "Inner shadow", "enable_inner_shadow", False)
        self._slider(controls, "Shadow strength", "inner_shadow_strength", 0, 100, 35)
        self._slider(controls, "Shadow size", "inner_shadow_size", 1, 30, 10)
        self._check(controls, "Show mask only", "show_mask", False)
        self._check(controls, "Show boxes/corners", "show_boxes", False)

    def _button(self, parent: ttk.Frame, text: str, command) -> None:
        ttk.Button(parent, text=text, command=command).pack(fill="x", pady=4)

    def _check(self, parent: ttk.Frame, text: str, key: str, value: bool) -> None:
        var = tk.BooleanVar(value=value)
        self.vars[key] = var
        ttk.Checkbutton(parent, text=text, variable=var).pack(fill="x", pady=5)

    def _combo(self, parent: ttk.Frame, label: str, key: str, value: str, choices: list[str]) -> None:
        ttk.Label(parent, text=label).pack(anchor="w", pady=(8, 2))
        var = tk.StringVar(value=value)
        self.vars[key] = var
        ttk.Combobox(parent, textvariable=var, values=choices, state="readonly").pack(fill="x")

    def _color_entry(self, parent: ttk.Frame, label: str, key: str, value: str) -> None:
        ttk.Label(parent, text=label).pack(anchor="w", pady=(8, 2))
        var = tk.StringVar(value=value)
        self.vars[key] = var
        ttk.Entry(parent, textvariable=var).pack(fill="x")

    def _slider(self, parent: ttk.Frame, label: str, key: str, minimum: int, maximum: int, value: int) -> None:
        ttk.Label(parent, text=label).pack(anchor="w", pady=(8, 2))
        row = ttk.Frame(parent)
        row.pack(fill="x")
        var = tk.IntVar(value=value)
        self.vars[key] = var
        ttk.Scale(row, from_=minimum, to=maximum, variable=var, orient="horizontal").pack(side="left", fill="x", expand=True)
        ttk.Spinbox(row, from_=minimum, to=maximum, textvariable=var, width=8).pack(side="left", padx=(8, 0))

    def _wire_auto_refresh(self) -> None:
        for key, var in self.vars.items():
            var.trace_add("write", lambda *_args, k=key: self.schedule_auto_refresh(k))

    def schedule_auto_refresh(self, changed_key: str) -> None:
        if self.mockup_image is None or self._busy:
            return
        if self._auto_after_id is not None:
            self.after_cancel(self._auto_after_id)
        delay = 350 if changed_key in self.DETECTION_SETTING_KEYS else 120
        self._auto_after_id = self.after(delay, lambda k=changed_key: self.auto_refresh(k))

    def auto_refresh(self, changed_key: str) -> None:
        self._auto_after_id = None
        if self.mockup_image is None:
            return
        if changed_key in self.DETECTION_SETTING_KEYS or self.state is None:
            self.detect(auto=True)
        else:
            self.process(auto=True)

    def current_art_source(self) -> Optional[Image.Image | list[Image.Image]]:
        if str(self.vars.get("image_mode", tk.StringVar(value="same")).get()) == "pool":
            if not self.art_pool:
                return None
            self.ensure_pool_assignments()
            return [self.art_pool[i][1] for i in self.pool_assignments]
        return self.art_image

    def ensure_pool_assignments(self) -> None:
        if self.state is None or not self.art_pool:
            self.pool_assignments = []
            return
        region_count = len(self.state.regions)
        if len(self.pool_assignments) == region_count and all(0 <= i < len(self.art_pool) for i in self.pool_assignments):
            return
        self.pool_assignments = [i % len(self.art_pool) for i in range(region_count)]

    def shuffled_pool_assignments(self) -> list[int]:
        if self.state is None or not self.art_pool:
            return []
        region_count = len(self.state.regions)
        result: list[int] = []
        while len(result) < region_count:
            indexes = list(range(len(self.art_pool)))
            random.shuffle(indexes)
            result.extend(indexes)
        return result[:region_count]

    def assignment_text(self) -> str:
        if str(self.vars.get("image_mode", tk.StringVar(value="same")).get()) != "pool":
            return "single artwork" if self.art_image else "no artwork"
        if not self.art_pool:
            return "pool empty"
        self.ensure_pool_assignments()
        names = []
        for idx, pool_index in enumerate(self.pool_assignments, start=1):
            names.append(f"#{idx}->{self.art_pool[pool_index][0].name}")
        return "pool: " + ", ".join(names[:4]) + ("..." if len(names) > 4 else "")

    def settings(self) -> Settings:
        def hex_to_rgb(value: str) -> tuple[int, int, int]:
            clean = value.strip().lstrip("#")
            if len(clean) != 6:
                raise ValueError(f"Invalid color: {value}")
            return int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16)

        def numeric(key: str) -> int:
            return int(round(float(self.vars[key].get())))

        return Settings(
            use_perspective=bool(self.vars["use_perspective"].get()),
            wide_coverage_envelope=bool(self.vars["wide_coverage_envelope"].get()),
            target_color=hex_to_rgb(str(self.vars["target_color"].get())),
            tolerance=numeric("tolerance"),
            min_area=numeric("min_area"),
            edge_expand=numeric("edge_expand"),
            feather_radius=numeric("feather_radius"),
            mask_build_quality=numeric("mask_build_quality"),
            aa_scale=numeric("aa_scale"),
            edge_aa_radius=numeric("edge_aa_radius"),
            fit_mode=str(self.vars["fit_mode"].get()),
            contain_bg=hex_to_rgb(str(self.vars["contain_bg"].get())),
            show_mask=bool(self.vars["show_mask"].get()),
            show_boxes=bool(self.vars["show_boxes"].get()),
            enable_inner_shadow=bool(self.vars["enable_inner_shadow"].get()),
            inner_shadow_strength=numeric("inner_shadow_strength"),
            inner_shadow_size=numeric("inner_shadow_size"),
        )

    def set_status(self, text: str) -> None:
        self.status.configure(text=text)
        self.update_idletasks()

    def load_mockup(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*")])
        if not path:
            return
        self.mockup_path = Path(path)
        self.mockup_image = pil_rgba(self.mockup_path)
        self.state = None
        self.result_image = self.mockup_image
        self.show_image(self.mockup_image)
        self.set_status(f"Mockup loaded: {self.mockup_path.name}. Detecting green regions...")
        self.detect()

    def load_artwork(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*")])
        if not path:
            return
        self.art_path = Path(path)
        self.art_image = pil_rgba(self.art_path)
        self.vars["image_mode"].set("same")
        self.set_status(f"Artwork loaded: {self.art_path.name}.")
        if self.state is not None:
            self.process()

    def load_artwork_pool(self) -> None:
        paths = filedialog.askopenfilenames(filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*")])
        if not paths:
            return
        loaded = 0
        for path in paths:
            image_path = Path(path)
            try:
                self.art_pool.append((image_path, pil_rgba(image_path)))
                loaded += 1
            except Exception:
                traceback.print_exc()
        self.vars["image_mode"].set("pool")
        self.pool_assignments = []
        if self.state is not None:
            self.ensure_pool_assignments()
            self.process()
        else:
            self.set_status(f"Loaded {loaded} artwork image(s) into pool.")

    def shuffle_pool(self) -> None:
        if not self.art_pool:
            messagebox.showwarning("Pool empty", "Load artwork pool images first.")
            return
        self.vars["image_mode"].set("pool")
        if self.state is not None:
            region_count = len(self.state.regions)
            self.pool_assignments = self.shuffled_pool_assignments()
            self.process()
        else:
            random.shuffle(self.art_pool)
            self.set_status(f"Shuffled {len(self.art_pool)} pool image(s).")

    def clear_pool(self) -> None:
        self.art_pool = []
        self.pool_assignments = []
        if str(self.vars["image_mode"].get()) == "pool":
            self.vars["image_mode"].set("same")
        self.set_status("Artwork pool cleared.")
        if self.state is not None:
            self.process()

    def detect(self, auto: bool = False) -> None:
        if self.mockup_image is None:
            if not auto:
                messagebox.showwarning("Missing mockup", "Load a mockup image first.")
            return
        try:
            self._busy = True
            settings = self.settings()
            self.set_status("Detecting green regions...")
            self.state = detect_regions(self.mockup_image, settings)
            self.ensure_pool_assignments()
            self.result_image = render_output(self.mockup_image, self.current_art_source(), self.state, settings)
            self.show_image(self.result_image)
            self.set_status(
                f"Detected {len(self.state.regions)} region(s), green pixels: {self.state.green_count:,}. {self.assignment_text()}"
            )
        except Exception as exc:
            self.handle_error(exc)
        finally:
            self._busy = False

    def process(self, auto: bool = False) -> None:
        if self.mockup_image is None:
            if not auto:
                messagebox.showwarning("Missing mockup", "Load a mockup image first.")
            return
        if self.state is None:
            self.detect(auto=auto)
            return
        try:
            self._busy = True
            settings = self.settings()
            self.set_status("Processing...")
            self.ensure_pool_assignments()
            self.result_image = render_output(self.mockup_image, self.current_art_source(), self.state, settings)
            self.show_image(self.result_image)
            self.set_status(
                f"Done. Regions: {len(self.state.regions)}. Perspective: {'on' if settings.use_perspective else 'off'}. {self.assignment_text()}"
            )
        except Exception as exc:
            self.handle_error(exc)
        finally:
            self._busy = False

    def save_png(self) -> None:
        if self.result_image is None:
            messagebox.showwarning("Nothing to save", "Process an image first.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("All files", "*.*")],
            initialfile="mockup-output-python.png",
        )
        if not path:
            return
        self.result_image.save(path)
        self.set_status(f"Saved: {path}")

    def show_image(self, image: Image.Image) -> None:
        max_w = max(400, self.preview.winfo_width() - 24)
        max_h = max(400, self.preview.winfo_height() - 24)
        preview = image.copy()
        preview.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
        self.preview_photo = ImageTk.PhotoImage(preview)
        self.preview.configure(image=self.preview_photo)

    def handle_error(self, exc: Exception) -> None:
        traceback.print_exc()
        messagebox.showerror("Error", str(exc))
        self.set_status(f"Error: {exc}")


def main() -> int:
    app = GreenScreenApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
