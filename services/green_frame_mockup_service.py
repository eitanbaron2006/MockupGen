from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
from PIL import Image, ImageChops, ImageFilter

try:
    from scipy import ndimage
except ImportError:  # pragma: no cover - production/dev dependency guard
    ndimage = None

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


@dataclass
class GreenFrameSettings:
    use_perspective: bool = True
    wide_coverage_envelope: bool = True
    target_color: tuple[int, int, int] = (0, 255, 0)
    tolerance: int = 95
    min_area: int = 2500
    edge_expand: int = 0
    # How far the opening reaches past the detected green on each side, in
    # pixels. A green screen photographed at an angle leaves a sliver of green
    # showing along one edge; these push the artwork over it. Negative pulls
    # the opening back instead.
    mask_expand_left: int = 0
    mask_expand_right: int = 0
    mask_expand_top: int = 0
    mask_expand_bottom: int = 0
    feather_radius: int = 0
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


class GreenFrameDetection:
    """What one detection found, held in the smallest form that still answers.

    These are cached per template, and every mask in here is the size of the
    canvas: at 1254x1254 the five arrays this used to hold came to 17MB an
    entry and 208MB for a full cache, and a 6000x6000 mockup put the same cache
    close to five gigabytes. Two changes bring that down without changing a
    pixel of the result:

    * the green alpha channel was stored and never read again -- it is gone;
    * the three yes/no masks are kept as bits rather than as a byte per pixel
      (numpy spends a whole byte on a bool), and unpacked on the way out.

    The unpacking costs about a millisecond per canvas against a render that
    takes far longer, and the callers see the same arrays they always did.
    """

    __slots__ = (
        "width",
        "height",
        "regions",
        "soft_mask",
        "green_count",
        "_raw_bits",
        "_detect_bits",
        "_clip_bits",
    )

    def __init__(
        self,
        width: int,
        height: int,
        regions: list[GreenRegion],
        raw_mask: np.ndarray,
        detect_mask: np.ndarray,
        clip_mask: np.ndarray,
        soft_mask: np.ndarray,
        green_count: int,
    ) -> None:
        self.width = width
        self.height = height
        self.regions = regions
        self.soft_mask = soft_mask
        self.green_count = green_count
        self._raw_bits = np.packbits(raw_mask)
        self._detect_bits = np.packbits(detect_mask)
        self._clip_bits = np.packbits(clip_mask)

    def _unpack(self, bits: np.ndarray) -> np.ndarray:
        return np.unpackbits(bits, count=self.height * self.width).astype(bool).reshape(
            (self.height, self.width)
        )

    @property
    def raw_mask(self) -> np.ndarray:
        return self._unpack(self._raw_bits)

    @property
    def detect_mask(self) -> np.ndarray:
        return self._unpack(self._detect_bits)

    @property
    def clip_mask(self) -> np.ndarray:
        return self._unpack(self._clip_bits)

    @raw_mask.setter
    def raw_mask(self, mask: np.ndarray) -> None:
        self._raw_bits = np.packbits(mask)

    @detect_mask.setter
    def detect_mask(self, mask: np.ndarray) -> None:
        self._detect_bits = np.packbits(mask)

    @clip_mask.setter
    def clip_mask(self, mask: np.ndarray) -> None:
        self._clip_bits = np.packbits(mask)


# How hard an edge has to be before a seed-point fill is stopped by it. Set
# high on purpose: a frame's bezel clears it, the shading across a blank
# opening does not, so the fill still follows gradients inside the opening.
_EDGE_BARRIER = 220.0

# How straight a side of the fill has to be before the region is trimmed back
# to it: within this many pixels of the fitted line, for this share of the
# side, and no more tilted than this. A leaked edge misses all three.
_EDGE_INLIER_PX = 1.5
_EDGE_MIN_INLIERS = 0.9
_EDGE_MAX_TILT = 10.0


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
        # Wide open by default. 442 is the whole colour space -- the longest
        # distance between two colours in RGB is 255*sqrt(3) = 441.67 -- so at
        # this setting every pixel counts as green and the opening is decided
        # by the frames as drawn rather than by how well the green survived the
        # photograph. That is what makes it a good default here: the artwork
        # fills the frame exactly, with no sliver of green left along an edge
        # that happened to be lit differently. Lower it on a template where the
        # opening has to follow a shape the frames do not describe.
        tolerance=int(_clamp(number("tolerance", 442), 10, 442)),
        min_area=int(_clamp(number("min_area", 2500), 80, 200000)),
        edge_expand=int(_clamp(number("edge_expand", 0), 0, 255)),
        mask_expand_left=int(_clamp(number("mask_expand_left", 0), -50, 150)),
        mask_expand_right=int(_clamp(number("mask_expand_right", 0), -50, 150)),
        mask_expand_top=int(_clamp(number("mask_expand_top", 0), -50, 150)),
        mask_expand_bottom=int(_clamp(number("mask_expand_bottom", 0), -50, 150)),
        feather_radius=int(_clamp(number("feather_radius", 0), 0, 12)),
        mask_build_quality=int(_clamp(number("mask_build_quality", 2), 1, 3)),
        aa_scale=int(_clamp(number("aa_scale", 1), 1, 8)),
        edge_aa_radius=int(_clamp(number("edge_aa_radius", 0), 0, 6)),
        fit_mode=fit_mode,
        artwork_scale=_clamp(number("artwork_scale", 1.0), 0.1, 3.0),
        offset_x=_clamp(number("offset_x", 0.0), -1.0, 1.0),
        offset_y=_clamp(number("offset_y", 0.0), -1.0, 1.0),
        contain_bg=_parse_hex_rgb(options.get("contain_bg_color"), (255, 255, 255)),
        # Legacy green-frame inner shadow is retired: the standard
        # "Inner Frame Shadow" effect (target IMG) now covers green mockups too.
        enable_inner_shadow=False,
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
    return np.asarray(Image.fromarray(np.clip(field * 255, 0, 255).astype(np.uint8)).filter(ImageFilter.BoxBlur(radius)), dtype=np.float32) / 255.0


def _color_distance(rgb: np.ndarray, target: tuple[int, int, int]) -> np.ndarray:
    diff = rgb.astype(np.float32) - np.asarray(target, dtype=np.float32)
    return np.sqrt(np.sum(diff * diff, axis=2))


def _green_confidence(rgb: np.ndarray, target: tuple[int, int, int], tolerance: int) -> np.ndarray:
    dist = _color_distance(rgb, target)
    similarity = np.maximum(0.0, 1.0 - (dist / max(1, tolerance)))
    r = rgb[:, :, 0].astype(np.float32)
    g = rgb[:, :, 1].astype(np.float32)
    b = rgb[:, :, 2].astype(np.float32)
    total = r + g + b + 1e-5
    green_ratio = g / total
    is_chroma_green = (green_ratio > 0.42) & (g > 60) & (g > r * 1.15) & (g > b * 1.15)
    chroma_score = np.where(is_chroma_green, np.clip((green_ratio - 0.38) / 0.22, 0.5, 1.0), 0.0)
    return np.maximum(similarity, chroma_score)


def _target_pixels(rgb: np.ndarray, target: tuple[int, int, int], tolerance: int) -> np.ndarray:
    dist = _color_distance(rgb, target)
    r = rgb[:, :, 0].astype(np.float32)
    g = rgb[:, :, 1].astype(np.float32)
    b = rgb[:, :, 2].astype(np.float32)
    total = r + g + b + 1e-5
    green_ratio = g / total
    is_chroma_green = (green_ratio > 0.42) & (g > 60) & (g > r * 1.15) & (g > b * 1.15)
    return (dist <= tolerance) | is_chroma_green


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


def _bilinear_upsample(field: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    h, w = field.shape
    fx = np.clip(xs, 0.0, w - 1.001)
    fy = np.clip(ys, 0.0, h - 1.001)
    x0 = np.floor(fx).astype(np.int32)
    y0 = np.floor(fy).astype(np.int32)
    x1 = np.minimum(w - 1, x0 + 1)
    y1 = np.minimum(h - 1, y0 + 1)
    dx = (fx - x0).astype(np.float32)[None, :]
    dy = (fy - y0).astype(np.float32)[:, None]
    f = field.astype(np.float32)
    top = f[y0[:, None], x0[None, :]] * (1.0 - dx) + f[y0[:, None], x1[None, :]] * dx
    bottom = f[y1[:, None], x0[None, :]] * (1.0 - dx) + f[y1[:, None], x1[None, :]] * dx
    return top * (1.0 - dy) + bottom * dy


def _precision_soft_mask(region_mask: np.ndarray, alpha_mask: np.ndarray, settings: GreenFrameSettings) -> np.ndarray:
    q = int(_clamp(settings.mask_build_quality * 2, 2, 8))
    h, w = region_mask.shape
    xs = (np.arange(w * q, dtype=np.float32) + 0.5) / q - 0.5
    ys = (np.arange(h * q, dtype=np.float32) + 0.5) / q - 0.5
    region_val = _bilinear_upsample(region_mask.astype(np.float32), xs, ys)
    alpha_val = _bilinear_upsample(alpha_mask, xs, ys)
    hi = np.clip(alpha_val, 0.0, 1.0)
    hi[(region_val > 0.98) & (hi > 0.72)] = 1.0
    hi[region_val <= 0.001] = 0.0

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
                    min_x = min(min_x, cx)
                    max_x = max(max_x, cx)
                    min_y = min(min_y, cy)
                    max_y = max(max_y, cy)
                    for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                        if 0 <= nx < w and 0 <= ny < h and mask[ny, nx] and not visited[ny, nx]:
                            visited[ny, nx] = True
                            q.append((nx, ny))
                if count >= min_pixels:
                    regions.append(GreenRegion(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1, count))
        return sorted(regions, key=lambda r: (round(r.y / 25), r.x))

    labels, num = ndimage.label(mask, structure=np.ones((3, 3), dtype=np.uint8))
    regions = []
    for label_id, slices in enumerate(ndimage.find_objects(labels), start=1):
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
    min_area = max(1200, min(settings.min_area, max(1200, int(w * h * 0.005))))
    regions = _connected_regions(detect_mask, min_area)
    regions = _green_regions_only(regions, rgb, detect_mask, settings)

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
    return GreenFrameDetection(w, h, regions, raw_mask, detect_mask, union, soft_mask, int(raw_mask.sum()))


def _expand_sides(mask: np.ndarray, left: int, right: int, top: int, bottom: int) -> np.ndarray:
    """Grow (or shrink) a mask by a different amount on each side."""
    if cv2 is None:
        out = mask.copy()
        for amount, axis, forward in (
            (left, 1, False), (right, 1, True), (top, 0, False), (bottom, 0, True)
        ):
            for _ in range(max(0, amount)):
                out |= np.roll(out, 1 if forward else -1, axis=axis)
            for _ in range(max(0, -amount)):
                out &= np.roll(out, -1 if forward else 1, axis=axis)
        return out
    out = mask.astype(np.uint8)
    if left or right:
        width = abs(left) + abs(right) + 1
        kernel = np.ones((1, width), np.uint8)
        # cv2 reads the kernel from the anchor outwards, so the anchor sits at
        # the far side of the amount being added: anchor_x = right grows the
        # mask left by `left` and right by `right`.
        if left >= 0 and right >= 0:
            out = cv2.dilate(out, kernel, anchor=(right, 0))
        elif left <= 0 and right <= 0:
            out = cv2.erode(out, kernel, anchor=(-right, 0))
        else:
            # One side out, the other in: do them one at a time.
            out = _expand_sides(out.astype(bool), left, 0, 0, 0).astype(np.uint8)
            out = _expand_sides(out.astype(bool), 0, right, 0, 0).astype(np.uint8)
    if top or bottom:
        height = abs(top) + abs(bottom) + 1
        kernel = np.ones((height, 1), np.uint8)
        if top >= 0 and bottom >= 0:
            out = cv2.dilate(out, kernel, anchor=(0, bottom))
        elif top <= 0 and bottom <= 0:
            out = cv2.erode(out, kernel, anchor=(0, -bottom))
        else:
            out = _expand_sides(out.astype(bool), 0, 0, top, 0).astype(np.uint8)
            out = _expand_sides(out.astype(bool), 0, 0, 0, bottom).astype(np.uint8)
    return out.astype(bool)


def reshape_opening(
    state: GreenFrameDetection,
    settings: GreenFrameSettings,
    bounds: np.ndarray | None = None,
) -> None:
    """Push the opening out per side, and hold it inside the drawn frames.

    Two things the detected green alone cannot say. A screen photographed at an
    angle leaves a sliver of green along one edge that the artwork has to cover
    -- that is what the per-side amounts are for. And a frame the user has
    dragged in is a frame they want smaller: `bounds` is the union of the
    frames as they now stand, and the opening is held inside it, so an edit
    stops being something the render ignores.

    An untouched template is unaffected: the saved quad contains the region it
    was measured from, so the intersection takes nothing away.
    """
    sides = (
        settings.mask_expand_left,
        settings.mask_expand_right,
        settings.mask_expand_top,
        settings.mask_expand_bottom,
    )
    if not any(sides) and bounds is None:
        return

    clip = state.clip_mask
    soft = state.soft_mask
    if any(sides):
        grown = _expand_sides(clip, *sides)
        # Whatever the opening gained is fully open; what it had keeps its edge.
        soft = np.maximum(soft, (grown & ~clip).astype(np.float32))
        clip = grown
        state.detect_mask = _expand_sides(state.detect_mask, *sides)
    if bounds is not None:
        clip = clip & bounds
        soft = soft * bounds.astype(np.float32)
    state.clip_mask = clip
    state.soft_mask = soft


# What a blob is scored against when deciding whether it is a screen at all.
# The tolerance says how far the screen's own colour may drift; this says how
# green the blob has to be at heart, and it does not move with the tolerance --
# scoring a blob with the same wide setting that found it would call the whole
# room green.
_GREEN_REFERENCE_TOLERANCE = 160
# ...and how green that is: half of what the greenest blob in the picture
# manages, never below this floor. Relative, so a screen that photographed dull
# still passes on a mockup where nothing is brighter.
_GREEN_REGION_FLOOR = 0.35
_GREEN_REGION_SHARE = 0.5


def _green_regions_only(
    regions: list[GreenRegion],
    rgb: np.ndarray,
    mask: np.ndarray,
    settings: GreenFrameSettings,
) -> list[GreenRegion]:
    """Keep the blobs that are actually green.

    Raising the tolerance is meant to find more of a screen that photographed
    dull; past a point it starts finding the room instead. At 200 a single
    framed print came back as six regions -- five of them wall and plant, which
    score 0.005 where the frame scores 0.945. Scoring every blob against a
    fixed idea of green and dropping the ones far below the best is what makes
    the tolerance safe to raise.
    """
    if len(regions) < 2:
        return regions
    reference = _green_confidence(
        rgb, settings.target_color, min(settings.tolerance, _GREEN_REFERENCE_TOLERANCE)
    )
    scored = []
    for region in regions:
        window = mask[region.y : region.y + region.h, region.x : region.x + region.w]
        if not window.any():
            continue
        patch = reference[region.y : region.y + region.h, region.x : region.x + region.w][window]
        scored.append((float(patch.mean()), region))
    if not scored:
        return regions
    best = max(score for score, _ in scored)
    floor = max(_GREEN_REGION_FLOOR, best * _GREEN_REGION_SHARE)
    kept = [region for score, region in scored if score >= floor]
    # Nothing cleared the floor: keep the greenest one rather than none at all.
    return kept or [max(scored, key=lambda item: item[0])[1]]


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


def _quad_area(points: np.ndarray) -> float:
    x, y = points[:, 0], points[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2.0)


def _quad_containing(quad: np.ndarray, hull_points: np.ndarray) -> Optional[np.ndarray]:
    """`quad` with every side pushed out until the hull sits wholly inside it.

    Each side moves out by the smallest amount that swallows the last stray
    pixel, then the sides are intersected back into corners, so the shape keeps
    its slant and leaves the least leftover area it can at that slant.
    """
    centre = quad.mean(axis=0)
    lines = []
    for index in range(4):
        start = quad[index]
        direction = quad[(index + 1) % 4] - start
        length = float(np.hypot(*direction))
        if length < 1e-6:
            return None
        normal = np.array([direction[1], -direction[0]]) / length
        if float(np.dot(normal, start - centre)) < 0:
            normal = -normal
        overshoot = float(np.max((hull_points - start) @ normal))
        lines.append((start + normal * max(0.0, overshoot), direction / length))

    widened = []
    for index in range(4):
        (p1, d1), (p2, d2) = lines[index - 1], lines[index]
        denominator = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(denominator) < 1e-9:
            return None
        delta = p2 - p1
        widened.append(p1 + d1 * ((delta[0] * d2[1] - delta[1] * d2[0]) / denominator))
    return np.asarray(widened)


def _region_quad(mask: np.ndarray, region: GreenRegion) -> Optional[dict[str, dict[str, float]]]:
    """The tightest four-sided frame that still contains all of the mask.

    The editing frame has four straight sides, so it should be the four-sided
    shape that wastes the least: the mask entirely inside it, with the smallest
    leftover area. Frame, artwork and mask then describe the same shape.
    """
    if cv2 is None:
        return None
    pad = 2
    y0 = max(0, region.y - pad)
    x0 = max(0, region.x - pad)
    y1 = min(mask.shape[0], region.y + region.h + pad)
    x1 = min(mask.shape[1], region.x + region.w + pad)
    patch = np.ascontiguousarray(mask[y0:y1, x0:x1].astype(np.uint8))
    if not patch.any():
        return None
    contours, _ = cv2.findContours(patch, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    hull = cv2.convexHull(max(contours, key=cv2.contourArea))

    hull_points = hull.reshape(-1, 2).astype(np.float64)

    # Start from the four-sided shape of the opening, so a frame seen at an
    # angle keeps its perspective instead of being squared off, and push each
    # of its sides out by exactly as much as it takes to swallow the last
    # stray pixel: the mask ends up wholly inside that four-sided shape.
    candidates: list[np.ndarray] = []
    perimeter = cv2.arcLength(hull, True)
    epsilon = 0.002 * perimeter
    for _ in range(24):
        approx = cv2.approxPolyDP(hull, epsilon, True)
        if len(approx) <= 4:
            if len(approx) == 4:
                widened = _quad_containing(approx.reshape(-1, 2).astype(np.float64), hull_points)
                if widened is not None:
                    candidates.append(widened)
            break
        epsilon *= 1.3

    # A rounded opening -- a phone screen -- simplifies to a quad whose sides
    # rest on the corner arcs, a few degrees off the real edges. Pushed out to
    # contain the mask that tilt costs area, and the artwork drawn on it comes
    # out visibly rotated. So keep whichever candidate wastes the least: a
    # frame in perspective wins on its own quad, a rounded one on the mask's
    # minimal rectangle.
    candidates.append(cv2.boxPoints(cv2.minAreaRect(hull)).astype(np.float64))
    quad = min(candidates, key=_quad_area)

    points = np.asarray(quad, dtype=np.float64)
    points[:, 0] += x0
    points[:, 1] += y0

    # Order by angle around the centre rather than by x+y sums. On a strongly
    # slanted frame the sum heuristic swaps the top-right and bottom-left
    # corners, which mirrors the frame against the slant of the mask.
    middle = points.mean(axis=0)
    angles = np.arctan2(points[:, 1] - middle[1], points[:, 0] - middle[0])
    clockwise = points[np.argsort(angles)]
    start_index = int(np.argmin(clockwise.sum(axis=1)))
    clockwise = np.roll(clockwise, -start_index, axis=0)
    return {
        key: {"x": float(point[0]), "y": float(point[1])}
        for key, point in zip(("tl", "tr", "br", "bl"), clockwise)
    }


def green_detection_raw(state: GreenFrameDetection, edge_expand: int = 0) -> dict[str, Any]:
    regions = []
    for region in state.regions:
        # Fit the frame to the mask, so the editing frame, the artwork drawn in
        # it and the mask that clips it all describe the same shape.
        hugging = _region_quad(state.detect_mask, region)
        if hugging:
            region.corners = hugging
            region.inner_corners = region.inner_corners or hugging
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
    return Image.fromarray(np.where(state.detect_mask, 255, 0).astype(np.uint8))


def detection_from_mask(
    mask: Image.Image,
    raw_artwork_area: dict | None,
    settings: GreenFrameSettings,
) -> GreenFrameDetection:
    alpha = (np.asarray(mask.convert("L"), dtype=np.float32) / 255.0).clip(0, 1)
    raw_mask = alpha >= 0.06
    detect_mask = _dilate_mask(raw_mask, settings.edge_expand)
    raw_regions = raw_artwork_area.get("regions") if isinstance(raw_artwork_area, dict) else None
    corners_edited = bool(
        isinstance(raw_artwork_area, dict) and raw_artwork_area.get("corners_edited")
    )
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
            if corners_edited and region.inner_corners:
                # The opening belongs to the mockup: it stays where the mask
                # put it, whatever the admin has dragged. Only the artwork
                # inside follows the edited corners.
                xs = [point["x"] for point in region.inner_corners.values()]
                ys = [point["y"] for point in region.inner_corners.values()]
                region.x = int(min(xs))
                region.y = int(min(ys))
                region.w = max(1, int(max(xs) - min(xs)))
                region.h = max(1, int(max(ys) - min(ys)))
                region.outer_corners = region.corners
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
    is_vertex = isinstance(raw_artwork_area, dict) and (raw_artwork_area.get("provider") == "vertex" or raw_artwork_area.get("mode") == "vertex")
    if (has_raw_regions and not any(region.corners for region in regions)) or is_vertex:
        soft_mask = alpha.astype(np.float32)
    else:
        soft_mask = _soft_mask_for_regions(union, alpha.astype(np.float32), regions, settings)
    return GreenFrameDetection(mask.width, mask.height, regions, raw_mask, detect_mask, union, soft_mask, int(raw_mask.sum()))



def detect_frames_by_color(
    mockup: Image.Image,
    target_color: tuple[int, int, int],
    tolerance: int = 40,
    settings: GreenFrameSettings | None = None,
) -> GreenFrameDetection:
    """Detect frame regions matching any target color (no green-specific bias)."""
    settings = settings or GreenFrameSettings(target_color=target_color, tolerance=tolerance)
    rgba = np.asarray(mockup.convert("RGBA"))
    rgb = rgba[:, :, :3]
    h, w = rgb.shape[:2]

    dist = _color_distance(rgb, target_color)
    raw_mask = dist <= tolerance
    detect_mask = _dilate_mask(raw_mask, settings.edge_expand)
    corner_mask = _dilate_mask(raw_mask, min(1, settings.edge_expand))
    min_area = max(400, min(settings.min_area, max(400, int(w * h * 0.001))))
    candidate_regions = _connected_regions(detect_mask, min_area)
    if not candidate_regions:
        candidate_regions = _connected_regions(detect_mask, max(80, int(w * h * 0.0001)))

    regions: list[GreenRegion] = []
    for r in candidate_regions:
        if r.w < 20 or r.h < 20:
            continue
        ratio = r.w / max(1, r.h)
        if ratio < 0.10 or ratio > 10.0:
            continue
        solidity = r.area / max(1, (r.w * r.h))
        if solidity < 0.25:
            continue
        regions.append(r)

    if not regions and candidate_regions:
        regions = candidate_regions

    alpha = np.maximum(0.0, 1.0 - (dist / max(1, tolerance))).astype(np.float32)
    alpha[dist <= tolerance * 0.5] = 1.0

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
    return GreenFrameDetection(w, h, regions, raw_mask, detect_mask, union, soft_mask, int(raw_mask.sum()))


def _refine_region_boundaries(rgb: np.ndarray, region_mask: np.ndarray, max_snap: int = 6) -> np.ndarray:
    """Refine detected mask boundaries by snapping to the strongest local color gradient (frame bevel)."""
    if cv2 is None:
        return region_mask
    ys, xs = np.where(region_mask)
    if len(xs) < 100:
        return region_mask

    h, w = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    grad_x = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
    grad_y = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))

    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    span_y = y1 - y0
    span_x = x1 - x0
    if span_y < 20 or span_x < 20:
        return region_mask

    y_start, y_end = y0 + int(span_y * 0.15), y0 + int(span_y * 0.85)
    x_start, x_end = x0 + int(span_x * 0.15), x0 + int(span_x * 0.85)

    left_scores = [grad_x[y_start:y_end, x].mean() for x in range(max(0, x0 - max_snap), min(w, x0 + max_snap + 1))]
    best_x0 = max(0, x0 - max_snap) + int(np.argmax(left_scores)) if left_scores else x0

    right_scores = [grad_x[y_start:y_end, x].mean() for x in range(max(0, x1 - max_snap), min(w, x1 + max_snap + 1))]
    best_x1 = max(0, x1 - max_snap) + int(np.argmax(right_scores)) if right_scores else x1

    top_scores = [grad_y[y, x_start:x_end].mean() for y in range(max(0, y0 - max_snap), min(h, y0 + max_snap + 1))]
    best_y0 = max(0, y0 - max_snap) + int(np.argmax(top_scores)) if top_scores else y0

    bot_scores = [grad_y[y, x_start:x_end].mean() for y in range(max(0, y1 - max_snap), min(h, y1 + max_snap + 1))]
    best_y1 = max(0, y1 - max_snap) + int(np.argmax(bot_scores)) if bot_scores else y1

    out = region_mask.copy()
    out[best_y0 : best_y1 + 1, best_x0 : best_x1 + 1] = True
    return _straighten_sides(out, region_mask, (best_x0, best_y0, best_x1, best_y1))


def _fitted_edge(samples: list[tuple[int, int]]) -> Optional[tuple[float, float]]:
    """The line the samples lie on, or None when they do not lie on one.

    Only a boundary that really is straight is worth trusting: where the fill
    has leaked, its edge wanders, and a line through it would tilt the frame
    away from the opening.
    """
    points = np.asarray(samples, dtype=np.float64)
    if len(points) < 24:
        return None
    slope, intercept = np.polyfit(points[:, 0], points[:, 1], 1)
    for _ in range(2):
        residual = np.abs(points[:, 1] - (slope * points[:, 0] + intercept))
        keep = residual <= max(_EDGE_INLIER_PX, float(np.median(residual)) * 2.0)
        if int(keep.sum()) < 12:
            return None
        slope, intercept = np.polyfit(points[keep][:, 0], points[keep][:, 1], 1)
    residual = np.abs(points[:, 1] - (slope * points[:, 0] + intercept))
    if float((residual <= _EDGE_INLIER_PX).mean()) < _EDGE_MIN_INLIERS:
        return None
    if abs(math.degrees(math.atan(slope))) > _EDGE_MAX_TILT:
        return None
    return float(slope), float(intercept)


def _straighten_sides(
    squared: np.ndarray, region_mask: np.ndarray, snapped: tuple[int, int, int, int]
) -> np.ndarray:
    """Cut the squared-off region back to the straight sides the fill found.

    Snapping the bounding box to the frame's edges squares the region off, and
    a mockup photographed at a slight angle then loses that angle: its top edge
    comes out level while the frame it belongs to is a couple of degrees off,
    which is plain to see once artwork is drawn in it. Where a side of the fill
    is a straight line, the region takes that line's angle.

    Only the angle: each side keeps the reach the snap gave it, and stays
    outside every pixel the fill found. Trimming back to the fitted line itself
    would pull each side in by the pixel or two the fill stops short of the
    opening, and the artwork would no longer reach the frame.
    """
    ys, xs = np.where(region_mask)
    if len(xs) < 100:
        return squared
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    if x1 - x0 < 40 or y1 - y0 < 40:
        return squared

    top: list[tuple[int, int]] = []
    bottom: list[tuple[int, int]] = []
    left: list[tuple[int, int]] = []
    right: list[tuple[int, int]] = []
    for x in range(x0 + (x1 - x0) // 12, x1 - (x1 - x0) // 12):
        column = np.where(region_mask[:, x])[0]
        if len(column):
            top.append((x, int(column.min())))
            bottom.append((x, int(column.max())))
    for y in range(y0 + (y1 - y0) // 12, y1 - (y1 - y0) // 12):
        row = np.where(region_mask[y])[0]
        if len(row):
            left.append((y, int(row.min())))
            right.append((y, int(row.max())))

    snap_x0, snap_y0, snap_x1, snap_y1 = snapped
    reach = {
        "top": max(0.0, float(y0 - snap_y0)),
        "bottom": max(0.0, float(snap_y1 - y1)),
        "left": max(0.0, float(x0 - snap_x0)),
        "right": max(0.0, float(snap_x1 - x1)),
    }

    out = squared.copy()
    height, width = squared.shape
    columns = np.arange(width, dtype=np.float64)[None, :]
    rows = np.arange(height, dtype=np.float64)[:, None]
    for samples, side in ((top, "top"), (bottom, "bottom"), (left, "left"), (right, "right")):
        fit = _fitted_edge(samples)
        if fit is None:
            continue
        slope, intercept = fit
        points = np.asarray(samples, dtype=np.float64)
        along = points[:, 0]
        across = points[:, 1]
        # Push the line past the last pixel of the fill on this side, then out
        # again by however far the snap reached, so the side keeps its angle
        # without giving up any of the coverage it had.
        overshoot = float(np.max((slope * along + intercept) - across)) if side in ("top", "left")             else float(np.max(across - (slope * along + intercept)))
        offset = max(0.0, overshoot) + reach[side] + 1.0
        if side in ("top", "bottom"):
            line = slope * columns + intercept
            out &= (rows >= line - offset) if side == "top" else (rows <= line + offset)
        else:
            line = slope * rows + intercept
            out &= (columns >= line - offset) if side == "left" else (columns <= line + offset)
    return out if int(out.sum()) >= 80 else squared


def detect_frames_from_points(
    mockup: Image.Image,
    seed_points: list[dict[str, int]],
    tolerance: int = 40,
    settings: GreenFrameSettings | None = None,
) -> GreenFrameDetection:
    """Detect frame regions by flood-filling from user-supplied seed points.

    Each point selects the connected region of similar-colored pixels at that
    location. Smooth gradients and subtle shadows are traversed up to the hard
    boundary of the frame bezel.
    """
    settings = settings or GreenFrameSettings()
    rgba = np.asarray(mockup.convert("RGBA"))
    rgb = rgba[:, :, :3]
    h, w = rgb.shape[:2]

    combined_mask = np.zeros((h, w), dtype=bool)
    detected_regions: list[GreenRegion] = []
    gray_for_edges = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32) if cv2 is not None else None

    for point in seed_points:
        px = int(point.get("x", 0))
        py = int(point.get("y", 0))
        if not (0 <= px < w and 0 <= py < h):
            continue

        seed_color = tuple(int(c) for c in rgb[py, px])
        diff = rgb.astype(np.float32) - np.asarray(seed_color, dtype=np.float32)
        dist = np.sqrt(np.sum(diff * diff, axis=2))
        fixed_similar = dist <= tolerance

        if cv2 is not None:
            delta = int(max(6, min(25, tolerance * 0.35)))
            ff_mask = np.zeros((h + 2, w + 2), dtype=np.uint8)
            # The fill compares each pixel with its neighbour, which is what
            # lets it follow shading -- and also what let it walk down the soft
            # bevel of a frame one small step at a time and come out on the
            # moulding. Block it at hard edges instead: floodFill will not
            # cross a pixel that is already set in its mask. The threshold is
            # deliberately high, so only a real edge stops the fill and the
            # shading inside an opening never does; a fill that lands correctly
            # today is left exactly as it was.
            gradient = cv2.Sobel(gray_for_edges, cv2.CV_32F, 1, 0, ksize=3)
            ridge = np.hypot(gradient, cv2.Sobel(gray_for_edges, cv2.CV_32F, 0, 1, ksize=3)) >= _EDGE_BARRIER
            ridge[py, px] = False
            ff_mask[1:-1, 1:-1][ridge] = 1
            cv2.floodFill(
                rgb.copy(),
                ff_mask,
                (px, py),
                (0, 255, 0),
                loDiff=(delta, delta, delta),
                upDiff=(delta, delta, delta),
                flags=4 | (255 << 8),
            )
            floating_mask = ff_mask[1:-1, 1:-1] == 255
            region_mask = floating_mask | (fixed_similar & _dilate_mask(floating_mask, 3))
        elif ndimage is not None:
            labeled, _ = ndimage.label(fixed_similar, structure=np.ones((3, 3), dtype=np.uint8))
            seed_label = int(labeled[py, px])
            if seed_label == 0:
                continue
            region_mask = labeled == seed_label
        else:
            from collections import deque as _deque
            region_mask = np.zeros((h, w), dtype=bool)
            visited = np.zeros((h, w), dtype=bool)
            q = _deque([(px, py)])
            visited[py, px] = True
            while q:
                cx, cy = q.popleft()
                if fixed_similar[cy, cx]:
                    region_mask[cy, cx] = True
                    for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                        if 0 <= nx < w and 0 <= ny < h and not visited[ny, nx]:
                            visited[ny, nx] = True
                            q.append((nx, ny))

        if not np.any(region_mask):
            continue

        if cv2 is not None:
            c_mask = region_mask.astype(np.uint8)
            contours, _ = cv2.findContours(c_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if contours:
                largest = max(contours, key=cv2.contourArea)
                filled = np.zeros((h, w), dtype=np.uint8)
                cv2.drawContours(filled, [largest], -1, 1, thickness=-1)
                region_mask = filled.astype(bool)

        region_mask = _refine_region_boundaries(rgb, region_mask)

        ys_r, xs_r = np.where(region_mask)
        area = int(np.count_nonzero(region_mask))
        if area < 80:
            continue
        x0, x1 = int(xs_r.min()), int(xs_r.max())
        y0, y1 = int(ys_r.min()), int(ys_r.max())
        combined_mask |= region_mask
        detected_regions.append(GreenRegion(x0, y0, x1 - x0 + 1, y1 - y0 + 1, area))

    detected_regions = sorted(detected_regions, key=lambda r: (round(r.y / 25), r.x))
    detect_mask = _dilate_mask(combined_mask, settings.edge_expand)
    corner_mask = _dilate_mask(combined_mask, min(1, settings.edge_expand))
    alpha = combined_mask.astype(np.float32)
    union = detect_mask.copy()
    soft_mask = _soft_mask_for_regions(union, alpha, detected_regions, settings)
    for region in detected_regions:
        region.inner_corners = _find_corners(corner_mask, region)
        region.outer_corners = _find_corners(detect_mask, region)
        region.corners = region.inner_corners
    return GreenFrameDetection(w, h, detected_regions, combined_mask, detect_mask, union, soft_mask, int(combined_mask.sum()))


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
    sel = state.clip_mask & (state.soft_mask > 0.001) & (state.soft_mask < 0.999)
    if not np.any(sel):
        return
    ys, xs = np.where(sel)
    r = base[ys, xs, 0].astype(np.float32)
    g = base[ys, xs, 1].astype(np.float32)
    b = base[ys, xs, 2].astype(np.float32)
    greenish = (g > r + 8) | (g > b + 8)
    if not np.any(greenish):
        return
    ys, xs = ys[greenish], xs[greenish]
    r, g, b = r[greenish], g[greenish], b[greenish]
    strength = np.clip((1.0 - state.soft_mask[ys, xs].astype(np.float32)) * 2.4, 0.0, 1.0)
    rb_avg = (r + b) / 2.0
    base[ys, xs, 0] = np.round(r * (1 - strength) + rb_avg * strength * 0.22).astype(np.uint8)
    base[ys, xs, 1] = np.round(g * (1 - strength) + np.minimum(g, rb_avg + 10) * strength).astype(np.uint8)
    base[ys, xs, 2] = np.round(b * (1 - strength) + rb_avg * strength * 0.22).astype(np.uint8)


def render_green_frame_mockup(
    background: Image.Image,
    artwork: Image.Image | list[Image.Image],
    settings: GreenFrameSettings,
    detection: GreenFrameDetection | None = None,
    artwork_filter: Any = None,
) -> Image.Image:
    state = detection or detect_green_frames(background, settings)
    base = np.asarray(background.convert("RGBA")).copy()
    _suppress_green_halo(base, state)
    result = Image.fromarray(base)
    base_after_rect = np.asarray(result).copy()
    overlays: list[tuple[Image.Image, int, int, int, int]] = []
    shadows: list[tuple[Image.Image, GreenRegion]] = []
    artworks = artwork if isinstance(artwork, list) else [artwork]
    if not artworks:
        return result

    for idx, region in enumerate(state.regions):
        region_art = artworks[idx % len(artworks)]
        if settings.use_perspective:
            overlay_data = _render_perspective_region(region, region_art, state, settings, artwork_filter)
            if overlay_data is not None:
                overlays.append(overlay_data)
            else:
                _draw_rect(base_after_rect, region, region_art, state, settings, artwork_filter)
        else:
            _draw_rect(base_after_rect, region, region_art, state, settings, artwork_filter)
        shadow = _inner_shadow(region, state, settings)
        if shadow is not None:
            shadows.append((shadow, region))

    result = Image.fromarray(base_after_rect)
    for overlay, cx0, cy0, _cw, _ch in overlays:
        result.alpha_composite(overlay, (cx0, cy0))
    for shadow, region in shadows:
        result.alpha_composite(shadow, (region.x, region.y))
    return result


def _draw_rect(base: np.ndarray, region: GreenRegion, art: Image.Image, state: GreenFrameDetection, settings: GreenFrameSettings, artwork_filter: Any = None) -> None:
    h, w = region.h, region.w
    base_crop = base[region.y : region.y + h, region.x : region.x + w].astype(np.float32)
    source = _source_image(art, w, h, settings)
    if artwork_filter is not None:
        source = artwork_filter(source)
    repl = np.asarray(source.convert("RGBA")).astype(np.float32)
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
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        vals.append(u)
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        vals.append(v)
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


def _render_perspective_region(region: GreenRegion, art: Image.Image, state: GreenFrameDetection, settings: GreenFrameSettings, artwork_filter: Any = None) -> Optional[tuple[Image.Image, int, int, int, int]]:
    # The frame is what the admin sees and drags, so it -- not the corners
    # detection happened to record once -- is what the artwork is warped onto,
    # and it bounds the artwork exactly. The outer corners never move with an
    # edited frame, so expanding those rendered a dragged frame at its old
    # angle and size, and the editor and the finished mockup drifted apart.
    frame = region.corners or region.inner_corners or region.outer_corners
    if not frame:
        return None
    warp = frame
    target_w = max(2, round(max(_dist(warp["tl"], warp["tr"]), _dist(warp["bl"], warp["br"]))))
    target_h = max(2, round(max(_dist(warp["tl"], warp["bl"]), _dist(warp["tr"], warp["br"]))))
    src = _source_image(art, target_w, target_h, settings)
    if artwork_filter is not None:
        src = artwork_filter(src)
    
    # Pad the source image to prevent edge bleeding/transparency under PIL
    # perspective warp. The padding is edge-replicated and lands outside the
    # frame, which is also how the wide coverage envelope works: it bleeds the
    # artwork's edge colour outwards so a mask that reaches past the frame has
    # no bare pixels, without warping the artwork onto a wider quad. Widening
    # the quad would scale and shift the artwork inside the frame, and would
    # spill real artwork outside the frame the admin is editing.
    pad = 4
    if settings.wide_coverage_envelope:
        pad += max(10, settings.edge_expand + 8) if state.width >= 100 else max(2, settings.edge_expand + 2)
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
        
    # Apply the soft mask at target scale (handles circular, oval, and any arbitrary shape perfectly!)
    soft_mask_np = state.soft_mask[cy0:cy1, cx0:cx1]
    soft_mask_uint8 = np.clip(soft_mask_np * 255.0, 0, 255).astype(np.uint8)
    soft_mask_region_img = Image.fromarray(soft_mask_uint8)
    
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
    local = state.clip_mask[region.y : region.y + h, region.x : region.x + w]
    if ndimage is not None:
        interior = ndimage.binary_erosion(local, structure=np.ones((3, 3), dtype=bool), border_value=0)
    else:
        padded = np.pad(local, 1, mode="constant", constant_values=False)
        interior = np.ones((h, w), dtype=bool)
        for dy in range(3):
            for dx in range(3):
                interior &= padded[dy : dy + h, dx : dx + w]
    edge = (local & ~interior).astype(np.float32)
    alpha = np.clip(_blur_float_field(edge, settings.inner_shadow_size) * max(1, settings.inner_shadow_size * 1.2) * (settings.inner_shadow_strength / 100.0), 0, 0.85)
    alpha[~local] = 0
    out = np.zeros((h, w, 4), dtype=np.uint8)
    out[:, :, 3] = np.round(alpha * 255).astype(np.uint8)
    return Image.fromarray(out)
