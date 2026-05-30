import json
import math
import random
import base64
import re
from io import BytesIO
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageEnhance

from services.image_utils import ImageProcessingError, fit_artwork, load_mask, load_rgba
from services.image_utils import get_perspective_coefficients
from services.green_frame_mockup_service import (
    detection_from_mask,
    detect_green_frames,
    parse_green_frame_settings,
    render_green_frame_mockup,
)


class TemplateNotFoundError(FileNotFoundError):
    pass


class InvalidTemplateError(ValueError):
    pass


class RenderValidationError(ValueError):
    pass


@dataclass(frozen=True)
class RenderResult:
    mode: str
    template_id: str
    output_url: str
    width: int
    height: int

    def as_response(self) -> dict[str, Any]:
        return {
            "success": True,
            "mode": self.mode,
            "template_id": self.template_id,
            "output_url": self.output_url,
            "width": self.width,
            "height": self.height,
        }


def _safe_template_folder(templates_folder: Path, template_id: str) -> Path:
    if not template_id or Path(template_id).name != template_id:
        raise TemplateNotFoundError(template_id)
    template_folder = templates_folder / template_id
    if not template_folder.is_dir():
        raise TemplateNotFoundError(template_id)
    return template_folder


def _safe_asset_path(template_folder: Path, asset_name: str) -> Path:
    if not isinstance(asset_name, str) or not asset_name:
        raise InvalidTemplateError("Template references a missing image asset")
    asset_path = (template_folder / asset_name).resolve()
    if template_folder.resolve() not in asset_path.parents or not asset_path.is_file():
        raise InvalidTemplateError(f"Template asset not found: {asset_name}")
    return asset_path


def _optional_asset_path(template_folder: Path, asset_name: Any) -> Path | None:
    if asset_name in (None, ""):
        return None
    if not isinstance(asset_name, str):
        raise InvalidTemplateError("Template references an invalid optional image asset")
    asset_path = (template_folder / asset_name).resolve()
    if template_folder.resolve() not in asset_path.parents:
        raise InvalidTemplateError(f"Template asset path is invalid: {asset_name}")
    if not asset_path.is_file():
        return None
    return asset_path


def load_manifest(templates_folder: Path, template_id: str) -> tuple[Path, dict[str, Any]]:
    template_folder = _safe_template_folder(templates_folder, template_id)
    manifest_path = template_folder / "manifest.json"
    if not manifest_path.is_file():
        raise TemplateNotFoundError(template_id)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise InvalidTemplateError("Invalid template manifest") from error

    required_fields = {
        "template_id",
        "name",
        "canvas_width",
        "canvas_height",
        "artwork_area",
        "background",
        "supported_modes",
    }
    if not isinstance(manifest, dict) or not required_fields.issubset(manifest):
        raise InvalidTemplateError("Invalid template manifest")
    if manifest["template_id"] != template_id:
        raise InvalidTemplateError("Template ID does not match its directory")
    _validated_canvas_and_area(manifest)
    _safe_asset_path(template_folder, manifest["background"])
    _optional_asset_path(template_folder, manifest.get("foreground"))
    return template_folder, manifest


def _validated_canvas_and_area(
    manifest: dict[str, Any],
) -> tuple[tuple[int, int], dict[str, Any]]:
    try:
        canvas = (int(manifest["canvas_width"]), int(manifest["canvas_height"]))
        area_source = manifest["artwork_area"]
        if "corners" in area_source:
            corners = area_source["corners"]
            normalized_corners = [{"x": int(p["x"]), "y": int(p["y"])} for p in corners]
            xs = [p["x"] for p in normalized_corners]
            ys = [p["y"] for p in normalized_corners]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            area = {
                "x": min_x,
                "y": min_y,
                "width": max_x - min_x,
                "height": max_y - min_y,
                "corners": normalized_corners
            }
        else:
            area = {
                "x": int(area_source["x"]),
                "y": int(area_source["y"]),
                "width": int(area_source["width"]),
                "height": int(area_source["height"]),
            }
    except (KeyError, TypeError, ValueError) as error:
        raise InvalidTemplateError("Invalid canvas or artwork area in manifest") from error

    if canvas[0] <= 0 or canvas[1] <= 0 or area["width"] <= 0 or area["height"] <= 0:
        raise InvalidTemplateError("Canvas and artwork area must be positive")
    if (
        area["x"] < 0
        or area["y"] < 0
        or area["x"] + area["width"] > canvas[0]
        or area["y"] + area["height"] > canvas[1]
    ):
        raise InvalidTemplateError("Artwork area must fit inside the canvas")
    return canvas, area


def list_templates(templates_folder: Path) -> list[dict[str, Any]]:
    templates: list[dict[str, Any]] = []
    if not templates_folder.is_dir():
        return templates
    for template_folder in sorted(path for path in templates_folder.iterdir() if path.is_dir()):
        try:
            _, manifest = load_manifest(templates_folder, template_folder.name)
        except (TemplateNotFoundError, InvalidTemplateError):
            continue
        preview_name = manifest.get("preview", "preview.png")
        try:
            _safe_asset_path(template_folder, preview_name)
        except InvalidTemplateError:
            continue
        orientation = manifest.get("orientation")
        if not orientation and "artwork_area" in manifest:
            from services.catalog_service import orientation_for_size
            orientation = orientation_for_size(
                int(manifest["artwork_area"]["width"]),
                int(manifest["artwork_area"]["height"])
            )
            
        templates.append(
            {
                "template_id": manifest["template_id"],
                "name": manifest["name"],
                "preview_url": f"/templates/{template_folder.name}/{preview_name}",
                "supported_modes": manifest["supported_modes"],
                "orientation": orientation,
                "product_type": manifest.get("product_type"),
            }
        )
    return templates


def select_template_for_artwork(
    templates_folder: Path, product_type: str, artwork_path: Path
) -> str | None:
    artwork = load_rgba(artwork_path)
    artwork_ratio = artwork.width / artwork.height
    candidates: list[tuple[float, str]] = []
    if not templates_folder.is_dir():
        return None
    for template_folder in templates_folder.iterdir():
        if not template_folder.is_dir():
            continue
        try:
            _, manifest = load_manifest(templates_folder, template_folder.name)
        except (TemplateNotFoundError, InvalidTemplateError):
            continue
        if str(manifest.get("product_type", "")).lower() != product_type.lower():
            continue
        area = manifest["artwork_area"]
        area_ratio = int(area["width"]) / int(area["height"])
        candidates.append((abs(math.log(artwork_ratio / area_ratio)), template_folder.name))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][1]


def _mask_for_artwork(
    template_folder: Path,
    mask_name: str,
    canvas_size: tuple[int, int],
    area: dict[str, int],
) -> Image.Image:
    mask_image = load_mask(_safe_asset_path(template_folder, mask_name))
    area_size = (area["width"], area["height"])
    if mask_image.size == canvas_size:
        return mask_image.crop(
            (
                area["x"],
                area["y"],
                area["x"] + area["width"],
                area["y"] + area["height"],
            )
        )
    if mask_image.size == area_size:
        return mask_image
    raise InvalidTemplateError("Mask size must match the canvas or artwork area")


def _full_canvas_mask(
    template_folder: Path,
    mask_name: str,
    canvas_size: tuple[int, int],
    area: dict[str, Any],
) -> Image.Image:
    mask_image = load_mask(_safe_asset_path(template_folder, mask_name))
    if mask_image.size == canvas_size:
        return mask_image
    if mask_image.size == (area["width"], area["height"]):
        full_mask = Image.new("L", canvas_size, 0)
        full_mask.paste(mask_image, (area["x"], area["y"]))
        return full_mask
    raise InvalidTemplateError("Mask size must match the canvas or artwork area")


def _parse_hex_color(value: Any, fallback: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        return fallback
    return (
        int(value[1:3], 16),
        int(value[3:5], 16),
        int(value[5:7], 16),
        255,
    )


def _green_frame_options(effects: dict | None, fallback_fit_mode: str) -> dict[str, Any]:
    options = {}
    if effects and isinstance(effects.get("green_frame_mockups"), dict):
        options = effects["green_frame_mockups"]
    fit_mode = str(options.get("fit_mode") or fallback_fit_mode or "cover").lower()
    if fit_mode not in {"cover", "contain", "stretch"}:
        fit_mode = "cover"
    return {
        "use_perspective": bool(options.get("use_perspective", True)),
        "use_vector_clip": bool(options.get("use_vector_clip", True)),
        "fit_mode": fit_mode,
        "artwork_scale": min(3.0, max(0.1, float(options.get("artwork_scale", 1.0)))),
        "offset_x": min(1.0, max(-1.0, float(options.get("offset_x", 0.0)))),
        "offset_y": min(1.0, max(-1.0, float(options.get("offset_y", 0.0)))),
        "edge_expand": min(24, max(0, int(options.get("edge_expand", 1)))),
        "mask_build_quality": min(3, max(1, int(options.get("mask_build_quality", 2)))),
        "feather_radius": min(12.0, max(0.0, float(options.get("feather_radius", 2.0)))),
        "edge_aa_radius": min(6.0, max(0.0, float(options.get("edge_aa_radius", 2.0)))),
        "aa_scale": min(8, max(1, int(options.get("aa_scale", 4)))),
        "enable_inner_shadow": bool(options.get("enable_inner_shadow", True)),
        "inner_shadow_strength": min(1.0, max(0.0, float(options.get("inner_shadow_strength", 0.35)))),
        "inner_shadow_size": min(30.0, max(1.0, float(options.get("inner_shadow_size", 10.0)))),
        "contain_bg_color": _parse_hex_color(options.get("contain_bg_color"), (255, 255, 255, 255)),
    }


def _is_green_frame_raw(raw_artwork_area: dict | None) -> bool:
    return isinstance(raw_artwork_area, dict) and raw_artwork_area.get("mode") == "green_frames_mockups"


def _mask_regions(mask: Image.Image, min_pixels: int = 8) -> list[dict[str, int]]:
    binary = mask.point(lambda p: 255 if p > 0 else 0)
    width, height = binary.size
    pixels = binary.load()
    visited = bytearray(width * height)
    regions: list[dict[str, int]] = []
    for y in range(height):
        for x in range(width):
            idx = y * width + x
            if visited[idx] or not pixels[x, y]:
                continue
            stack = [(x, y)]
            visited[idx] = 1
            min_x = max_x = x
            min_y = max_y = y
            count = 0
            while stack:
                cx, cy = stack.pop()
                count += 1
                min_x = min(min_x, cx)
                max_x = max(max_x, cx)
                min_y = min(min_y, cy)
                max_y = max(max_y, cy)
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if nx < 0 or ny < 0 or nx >= width or ny >= height:
                        continue
                    nidx = ny * width + nx
                    if visited[nidx] or not pixels[nx, ny]:
                        continue
                    visited[nidx] = 1
                    stack.append((nx, ny))
            if count >= min_pixels:
                regions.append(
                    {
                        "x": min_x,
                        "y": min_y,
                        "width": max_x - min_x + 1,
                        "height": max_y - min_y + 1,
                        "area": count,
                    }
                )
    return sorted(regions, key=lambda r: (r["y"], r["x"]))


def _normalize_green_regions(raw_artwork_area: dict | None, mask: Image.Image) -> list[dict[str, Any]]:
    raw_regions = raw_artwork_area.get("regions") if isinstance(raw_artwork_area, dict) else None
    regions = [region for region in raw_regions or [] if isinstance(region, dict)]
    if not regions:
        regions = _mask_regions(mask)
    normalized = []
    for region in regions:
        try:
            x = int(region["x"])
            y = int(region["y"])
            width = int(region.get("width", region.get("w")))
            height = int(region.get("height", region.get("h")))
        except (KeyError, TypeError, ValueError):
            continue
        if width <= 0 or height <= 0:
            continue
        item: dict[str, Any] = {"x": x, "y": y, "width": width, "height": height}
        corners = region.get("corners") or region.get("inner_corners")
        if isinstance(corners, list) and len(corners) == 4:
            item["corners"] = [{"x": float(p["x"]), "y": float(p["y"])} for p in corners]
        inner_corners = region.get("inner_corners")
        if isinstance(inner_corners, list) and len(inner_corners) == 4:
            item["inner_corners"] = [{"x": float(p["x"]), "y": float(p["y"])} for p in inner_corners]
        outer_corners = region.get("outer_corners")
        if isinstance(outer_corners, list) and len(outer_corners) == 4:
            item["outer_corners"] = [{"x": float(p["x"]), "y": float(p["y"])} for p in outer_corners]
        normalized.append(item)
    return normalized


def _prepare_green_source(artwork: Image.Image, size: tuple[int, int], options: dict[str, Any]) -> Image.Image:
    background = options["contain_bg_color"] if options["fit_mode"] == "contain" else (0, 0, 0, 0)
    base = Image.new("RGBA", size, background)
    scaled_size = (
        max(1, int(round(size[0] * options["artwork_scale"]))),
        max(1, int(round(size[1] * options["artwork_scale"]))),
    )
    fitted = fit_artwork(artwork, scaled_size, options["fit_mode"])
    x = int(round((size[0] - fitted.width) / 2 + options["offset_x"] * size[0] / 2))
    y = int(round((size[1] - fitted.height) / 2 + options["offset_y"] * size[1] / 2))
    base.alpha_composite(fitted, dest=(x, y))
    return base


def _region_corners(region: dict[str, Any]) -> list[dict[str, float]]:
    if "corners" in region:
        return region["corners"]
    return _region_box_corners(region)


def _region_box_corners(region: dict[str, Any]) -> list[dict[str, float]]:
    x = float(region["x"])
    y = float(region["y"])
    width = float(region["width"])
    height = float(region["height"])
    return [
        {"x": x, "y": y},
        {"x": x + width, "y": y},
        {"x": x + width, "y": y + height},
        {"x": x, "y": y + height},
    ]


def _distance(a: dict[str, float], b: dict[str, float]) -> float:
    return math.hypot(float(a["x"]) - float(b["x"]), float(a["y"]) - float(b["y"]))


def _expanded_quad(corners: list[dict[str, float]], amount: float) -> list[dict[str, float]]:
    center_x = sum(float(p["x"]) for p in corners) / 4
    center_y = sum(float(p["y"]) for p in corners) / 4
    expanded = []
    for point in corners:
        dx = float(point["x"]) - center_x
        dy = float(point["y"]) - center_y
        length = math.hypot(dx, dy) or 1.0
        expanded.append(
            {
                "x": float(point["x"]) + dx / length * amount,
                "y": float(point["y"]) + dy / length * amount,
            }
        )
    return expanded


def _green_region_mask_box(full_mask: Image.Image, region: dict[str, Any]) -> tuple[int, int, int, int]:
    x0 = max(0, int(region["x"]))
    y0 = max(0, int(region["y"]))
    x1 = min(full_mask.width, int(region["x"] + region["width"]))
    y1 = min(full_mask.height, int(region["y"] + region["height"]))
    if x1 <= x0 or y1 <= y0:
        return (x0, y0, x1, y1)
    mask_pixels = full_mask.load()
    found = False
    min_x, min_y, max_x, max_y = x1, y1, x0, y0
    for y in range(y0, y1):
        for x in range(x0, x1):
            if mask_pixels[x, y] <= 0:
                continue
            found = True
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)
    if not found:
        return (x0, y0, x1, y1)
    return (min_x, min_y, max_x + 1, max_y + 1)


def _apply_region_inner_shadow(layer: Image.Image, alpha: Image.Image, options: dict[str, Any]) -> Image.Image:
    if not options["enable_inner_shadow"] or options["inner_shadow_strength"] <= 0:
        return layer
    edge = ImageChops.subtract(alpha, alpha.filter(ImageFilter.MinFilter(3)))
    shadow = edge.filter(ImageFilter.GaussianBlur(radius=options["inner_shadow_size"]))
    shadow = shadow.point(lambda p: min(220, int(p * options["inner_shadow_strength"] * 3)))
    shadow_layer = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    shadow_layer.putalpha(shadow)
    return Image.alpha_composite(layer, shadow_layer)


def _render_green_frame_mockup(
    *,
    background: Image.Image,
    artwork: Image.Image,
    template_folder: Path,
    mask_name: str,
    canvas_size: tuple[int, int],
    area: dict[str, Any],
    raw_artwork_area: dict | None,
    fit_mode: str,
    realism: bool,
    effects: dict | None,
) -> Image.Image:
    settings = parse_green_frame_settings(effects, fit_mode)
    detection = detect_green_frames(background, settings)
    raw_regions = raw_artwork_area.get("regions") if isinstance(raw_artwork_area, dict) else None
    raw_has_perspective = any(
        isinstance(region, dict) and (region.get("corners") or region.get("inner_corners"))
        for region in (raw_regions or [])
    )
    if not detection.regions or (raw_regions and not raw_has_perspective):
        full_mask = _full_canvas_mask(template_folder, mask_name, canvas_size, area)
        detection = detection_from_mask(full_mask, raw_artwork_area, settings)
    if not detection.regions:
        raise InvalidTemplateError("Green frame mask has no usable regions")
    composed = render_green_frame_mockup(background, artwork, settings, detection)
    return composed

    options = _green_frame_options(effects, fit_mode)
    full_mask = _full_canvas_mask(template_folder, mask_name, canvas_size, area)
    if options["edge_expand"] > 0:
        full_mask = full_mask.filter(ImageFilter.MaxFilter(options["edge_expand"] * 2 + 1))
    regions = _normalize_green_regions(raw_artwork_area, full_mask)
    if not regions:
        raise InvalidTemplateError("Green frame mask has no usable regions")

    composed = background.copy()
    for region in regions:
        box = _green_region_mask_box(full_mask, region)
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        width = box[2] - box[0]
        height = box[3] - box[1]

        region_mask = full_mask.crop(box)
        blur_radius = max(options["feather_radius"], options["edge_aa_radius"])
        if blur_radius > 0:
            region_mask = region_mask.filter(ImageFilter.GaussianBlur(radius=blur_radius))

        if options["use_perspective"] and "corners" in region:
            inner_corners = region.get("inner_corners") or _region_corners(region)
            outer_corners = region.get("outer_corners") or _region_box_corners(region)
            if options["use_vector_clip"]:
                corners = _expanded_quad(
                    outer_corners,
                    max(2.0, float(options["edge_expand"]) + 2.0),
                )
            else:
                corners = inner_corners
            target_width = max(
                2,
                int(round(max(_distance(corners[0], corners[1]), _distance(corners[3], corners[2])))),
            )
            target_height = max(
                2,
                int(round(max(_distance(corners[0], corners[3]), _distance(corners[1], corners[2])))),
            )
            source = _prepare_green_source(artwork, (target_width, target_height), options)
            if realism:
                source = _apply_realism_filter(source, effects)
            src_coords = [
                (0.0, 0.0),
                (float(source.width - 1), 0.0),
                (float(source.width - 1), float(source.height - 1)),
                (0.0, float(source.height - 1)),
            ]
            dst_coords = [(float(p["x"]), float(p["y"])) for p in corners]
            coefficients = get_perspective_coefficients(src_coords, dst_coords)
            layer = source.transform(
                canvas_size,
                Image.Transform.PERSPECTIVE,
                coefficients,
                Image.Resampling.BICUBIC,
            )
            alpha = Image.new("L", canvas_size, 0)
            alpha.paste(region_mask, box)
            layer.putalpha(ImageChops.multiply(layer.getchannel("A"), alpha))
            layer = _apply_region_inner_shadow(layer, alpha, options)
            composed = Image.alpha_composite(composed, layer)
        else:
            source = _prepare_green_source(artwork, (width, height), options)
            if realism:
                source = _apply_realism_filter(source, effects)
            source.putalpha(ImageChops.multiply(source.getchannel("A"), region_mask))
            source = _apply_region_inner_shadow(source, region_mask, options)
            composed.alpha_composite(source, dest=(box[0], box[1]))
    return composed


def _apply_inner_shadow(artwork_layer: Image.Image, config: dict) -> Image.Image:
    if not config or not config.get("enabled", False):
        return artwork_layer

    width, height = artwork_layer.size

    top = int(config.get("top", 0))
    right = int(config.get("right", 0))
    bottom = int(config.get("bottom", 0))
    left = int(config.get("left", 0))
    opacity = float(config.get("opacity", 0.4))
    blur = int(config.get("blur", 15))

    left = min(max(0, left), width)
    right = min(max(0, right), width)
    top = min(max(0, top), height)
    bottom = min(max(0, bottom), height)

    if top <= 0 and right <= 0 and bottom <= 0 and left <= 0:
        return artwork_layer

    # Build composite mask
    mask = Image.new("L", (width, height), 0)

    # Left edge shadow
    if left > 0:
        left_grad = bytes(int(255 * (1.0 - x / left)) for x in range(left))
        grad_small = Image.frombytes("L", (left, 1), left_grad)
        grad_large = grad_small.resize((left, height), Image.Resampling.BILINEAR)
        edge_mask = Image.new("L", (width, height), 0)
        edge_mask.paste(grad_large, (0, 0))
        mask = ImageChops.lighter(mask, edge_mask)

    # Right edge shadow
    if right > 0:
        right_grad = bytes(int(255 * (x / right)) for x in range(right))
        grad_small = Image.frombytes("L", (right, 1), right_grad)
        grad_large = grad_small.resize((right, height), Image.Resampling.BILINEAR)
        edge_mask = Image.new("L", (width, height), 0)
        edge_mask.paste(grad_large, (width - right, 0))
        mask = ImageChops.lighter(mask, edge_mask)

    # Top edge shadow
    if top > 0:
        top_grad = bytes(int(255 * (1.0 - y / top)) for y in range(top))
        grad_small = Image.frombytes("L", (1, top), top_grad)
        grad_large = grad_small.resize((width, top), Image.Resampling.BILINEAR)
        edge_mask = Image.new("L", (width, height), 0)
        edge_mask.paste(grad_large, (0, 0))
        mask = ImageChops.lighter(mask, edge_mask)

    # Bottom edge shadow
    if bottom > 0:
        bottom_grad = bytes(int(255 * (y / bottom)) for y in range(bottom))
        grad_small = Image.frombytes("L", (1, bottom), bottom_grad)
        grad_large = grad_small.resize((width, bottom), Image.Resampling.BILINEAR)
        edge_mask = Image.new("L", (width, height), 0)
        edge_mask.paste(grad_large, (0, height - bottom))
        mask = ImageChops.lighter(mask, edge_mask)

    if blur > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(radius=blur))

    if opacity != 1.0:
        mask = mask.point(lambda p: int(p * opacity))

    shadow_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shadow_layer.paste((0, 0, 0, 255), (0, 0), mask=mask)

    orig_alpha = artwork_layer.getchannel("A")
    artwork_layer = Image.alpha_composite(artwork_layer, shadow_layer)
    artwork_layer.putalpha(ImageChops.multiply(artwork_layer.getchannel("A"), orig_alpha))

    return artwork_layer


def _apply_glass_reflection(artwork_layer: Image.Image, config: dict) -> Image.Image:
    if not config or not config.get("enabled", False):
        return artwork_layer

    width, height = artwork_layer.size
    opacity = float(config.get("opacity", 0.15))
    ref_type = config.get("type", "diagonal")

    if opacity <= 0:
        return artwork_layer

    # Generate a 16x16 grid for high smoothness and bilinear scale it to the artwork size
    grid_size = 16
    grad_data = []

    if ref_type == "double_glare":
        # Double diagonal glare bands at x+y = 8 and x+y = 20 on a 16x16 grid
        for row in range(grid_size):
            for col in range(grid_size):
                d = row + col  # ranges from 0 to 30
                intensity1 = max(0.0, 1.0 - abs(d - 8) / 3.5)
                intensity2 = max(0.0, 1.0 - abs(d - 20) / 4.5)
                val = int(255 * max(intensity1 * 0.9, intensity2 * 0.6))
                grad_data.append(val)
    else:
        # Standard diagonal sheen (default)
        for row in range(grid_size):
            for col in range(grid_size):
                val = int(255 * ((row + col) / 30.0))
                grad_data.append(val)

    grad_small = Image.frombytes("L", (grid_size, grid_size), bytes(grad_data))
    grad_large = grad_small.resize((width, height), Image.Resampling.BILINEAR)

    # Scale the mask by opacity
    if opacity != 1.0:
        grad_large = grad_large.point(lambda p: int(p * opacity))

    reflection_layer = Image.new("RGBA", (width, height), (255, 255, 255, 0))
    reflection_layer.paste((255, 255, 255, 255), (0, 0), mask=grad_large)

    orig_alpha = artwork_layer.getchannel("A")
    artwork_layer = Image.alpha_composite(artwork_layer, reflection_layer)
    artwork_layer.putalpha(ImageChops.multiply(artwork_layer.getchannel("A"), orig_alpha))

    return artwork_layer


def _apply_matte_finish(artwork_layer: Image.Image, opts: dict) -> Image.Image:
    # Extract settings
    shadow_lift = float(opts.get("shadow_lift", 0.08))  # default 8% shadow lift
    contrast = float(opts.get("contrast", -0.15))       # default -15% contrast

    # Convert to RGBA
    img = artwork_layer.convert("RGBA")
    r, g, b, a = img.split()

    # Build LUT for point mapping
    # shadow lift elevates the lower bounds from 0 to lift_val
    lift_val = shadow_lift * 255.0
    contrast_val = int(contrast * 255.0)
    factor = (259.0 * (contrast_val + 255.0)) / (255.0 * (259.0 - contrast_val))

    lut = []
    for i in range(256):
        # 1. Apply contrast around mid-tone 128
        val = factor * (i - 128.0) + 128.0
        # 2. Lift shadows (compressing lower end)
        val = val * (255.0 - lift_val) / 255.0 + lift_val
        # 3. Clamp between 0 and 255
        lut.append(max(0, min(255, int(val))))

    r = r.point(lut)
    g = g.point(lut)
    b = b.point(lut)

    return Image.merge("RGBA", (r, g, b, a))


def _apply_color_tint(artwork_layer: Image.Image, opts: dict) -> Image.Image:
    temperature = float(opts.get("temperature", 25.0))  # positive for warm, negative for cool
    intensity = float(opts.get("intensity", 0.2))        # opacity/influence

    # Convert to RGBA
    img = artwork_layer.convert("RGBA")
    r, g, b, a = img.split()

    # Temperature tint adjustment
    # Warmness: increase R and G, decrease B
    # Coolness: increase B, decrease R and G
    r_shift = int(temperature * intensity * 0.5)
    g_shift = int(temperature * intensity * 0.25)
    b_shift = int(-temperature * intensity * 0.5)

    r_lut = [max(0, min(255, i + r_shift)) for i in range(256)]
    g_lut = [max(0, min(255, i + g_shift)) for i in range(256)]
    b_lut = [max(0, min(255, i + b_shift)) for i in range(256)]

    r = r.point(r_lut)
    g = g.point(g_lut)
    b = b.point(b_lut)

    return Image.merge("RGBA", (r, g, b, a))


def _apply_gobo_shadow(artwork_layer: Image.Image, opts: dict) -> Image.Image:
    opacity = float(opts.get("opacity", 0.3))
    scale = float(opts.get("scale", 1.0))
    if opacity <= 0.001:
        return artwork_layer

    width, height = artwork_layer.size

    # Create a small 128x128 canvas to draw the blinds pattern
    gobo = Image.new("L", (128, 128), 255)
    draw = ImageDraw.Draw(gobo)

    # Draw vertical stripes
    band_width = int(24 * scale)
    step = int(42 * scale)
    if step <= 0:
        step = 42
    if band_width <= 0:
        band_width = 24

    for i in range(0, 128, step):
        # Draw soft shadow band by coloring it dark
        draw.rectangle([i, 0, i + band_width, 128], fill=int(255 - 120 * opacity))

    # Rotate the pattern diagonal by 35 degrees (standard photoreal angle)
    gobo = gobo.rotate(35, resample=Image.Resampling.BILINEAR, expand=False, fillcolor=255)
    # Resize to full artwork size with bilinear scaling (naturally softens/blurs details)
    gobo_full = gobo.resize((width, height), Image.Resampling.BILINEAR)

    # Apply a nice Gaussian blur to make the shadow extremely soft and sunny
    blur_radius = max(8, int(width / 35))
    gobo_full = gobo_full.filter(ImageFilter.GaussianBlur(blur_radius))

    # Multiply L channel with artwork colors
    # Multi-channel multiply with a single L channel shadow mask
    r, g, b, a = artwork_layer.convert("RGBA").split()
    r = ImageChops.multiply(r, gobo_full)
    g = ImageChops.multiply(g, gobo_full)
    b = ImageChops.multiply(b, gobo_full)

    return Image.merge("RGBA", (r, g, b, a))


def _apply_photoshop_adjustments(img: Image.Image, opts: dict) -> Image.Image:
    if not opts or not opts.get("enabled", False):
        return img
    
    # 1. Brightness
    brightness = float(opts.get("brightness", 0.0))
    if brightness != 0.0:
        factor = 1.0 + brightness
        img = ImageEnhance.Brightness(img).enhance(max(0.0, factor))
        
    # 2. Contrast
    contrast = float(opts.get("contrast", 0.0))
    if contrast != 0.0:
        factor = 1.0 + contrast
        img = ImageEnhance.Contrast(img).enhance(max(0.0, factor))
        
    # 3. Saturation
    saturation = float(opts.get("saturation", 0.0))
    if saturation != 0.0:
        factor = 1.0 + saturation
        img = ImageEnhance.Color(img).enhance(max(0.0, factor))
        
    # 4. Color Filters (LUT curves)
    color_filter = opts.get("color_filter", "none")
    if color_filter != "none":
        img = img.convert("RGBA")
        r, g, b, a = img.split()
        if color_filter == "dramatic_bw":
            gray = img.convert("L")
            gray = ImageEnhance.Contrast(gray).enhance(1.4)
            img = Image.merge("RGBA", (gray, gray, gray, a))
        elif color_filter == "vintage":
            r_lut = [max(0, min(255, int(i * 1.05 + 5))) for i in range(256)]
            g_lut = [max(0, min(255, int(i * 1.02))) for i in range(256)]
            b_lut = [max(0, min(255, int(i * 0.88 + 12))) for i in range(256)]
            r = r.point(r_lut)
            g = g.point(g_lut)
            b = b.point(b_lut)
            img = Image.merge("RGBA", (r, g, b, a))
            img = ImageEnhance.Color(img).enhance(0.9)
        elif color_filter == "cinematic":
            r_lut = [max(0, min(255, int(i * 1.1 - 6 if i > 128 else i * 0.9))) for i in range(256)]
            g_lut = [max(0, min(255, int(i * 1.02))) for i in range(256)]
            b_lut = [max(0, min(255, int(i * 0.88 + 10 if i > 128 else i * 1.08 + 10))) for i in range(256)]
            r = r.point(r_lut)
            g = g.point(g_lut)
            b = b.point(b_lut)
            img = Image.merge("RGBA", (r, g, b, a))
            img = ImageEnhance.Contrast(img).enhance(1.15)
        elif color_filter == "cool_nordic":
            r_lut = [max(0, min(255, int(i * 0.94))) for i in range(256)]
            g_lut = [max(0, min(255, int(i * 0.98))) for i in range(256)]
            b_lut = [max(0, min(255, int(i * 1.06 + 8))) for i in range(256)]
            r = r.point(r_lut)
            g = g.point(g_lut)
            b = b.point(b_lut)
            img = Image.merge("RGBA", (r, g, b, a))
            img = ImageEnhance.Color(img).enhance(0.85)
        elif color_filter == "warm_sunset":
            r_lut = [max(0, min(255, int(i * 1.1 + 12))) for i in range(256)]
            g_lut = [max(0, min(255, int(i * 1.05 + 4))) for i in range(256)]
            b_lut = [max(0, min(255, int(i * 0.9))) for i in range(256)]
            r = r.point(r_lut)
            g = g.point(g_lut)
            b = b.point(b_lut)
            img = Image.merge("RGBA", (r, g, b, a))
            img = ImageEnhance.Color(img).enhance(1.05)
            
    return img


def _create_window_frame_mask(width: int, height: int, style: str, blur: float) -> Image.Image:
    mask = Image.new("L", (512, 512), 0)
    draw = ImageDraw.Draw(mask)
    
    if style == "window_frame":
        draw.rectangle([40, 40, 230, 230], fill=255)
        draw.rectangle([282, 40, 472, 230], fill=255)
        draw.rectangle([40, 282, 230, 472], fill=255)
        draw.rectangle([282, 282, 472, 472], fill=255)
    elif style == "foliage":
        random_gen = random.Random(42)
        for _ in range(15):
            cx = random_gen.randint(80, 432)
            cy = random_gen.randint(80, 432)
            rx = random_gen.randint(30, 100)
            ry = random_gen.randint(20, 60)
            leaf = Image.new("L", (512, 512), 0)
            leaf_draw = ImageDraw.Draw(leaf)
            leaf_draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=255)
            leaf = leaf.rotate(random_gen.randint(0, 180), fillcolor=0)
            mask = ImageChops.lighter(mask, leaf)
    else:
        draw.polygon([(0, 0), (220, 0), (512, 292), (512, 512), (292, 512), (0, 220)], fill=255)
        
    mask = mask.resize((width, height), Image.Resampling.BILINEAR)
    if blur > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(blur))
    return mask


def _apply_sun_rays(img: Image.Image, rays_type: str, opacity: float, angle: float) -> Image.Image:
    if rays_type == "none" or opacity <= 0.001:
        return img
        
    width, height = img.size
    rays_mask_img = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(rays_mask_img)
    
    center_x = -150
    center_y = -150
    
    max_length = math.sqrt(width**2 + height**2) * 1.8
    num_rays = 20
    base_angle_rad = math.radians(angle)
    
    for i in range(num_rays):
        w_ang = math.radians(1.5 + (i % 4) * 1.2)
        r_ang = math.radians(i * (90.0 / num_rays)) + base_angle_rad
        
        x1 = center_x + max_length * math.cos(r_ang - w_ang)
        y1 = center_y + max_length * math.sin(r_ang - w_ang)
        x2 = center_x + max_length * math.cos(r_ang + w_ang)
        y2 = center_y + max_length * math.sin(r_ang + w_ang)
        
        draw.polygon([(center_x, center_y), (x1, y1), (x2, y2)], fill=255)
        
    blur_radius = max(20, int(width / 22))
    rays_mask_img = rays_mask_img.filter(ImageFilter.GaussianBlur(blur_radius))
    rays_mask_img = rays_mask_img.point(lambda p: int(p * opacity))
    
    if rays_type == "cool_beams":
        light_color = (225, 240, 255, 255)
    else:
        light_color = (255, 232, 185, 255)
        
    light_layer = Image.new("RGBA", (width, height), light_color)
    return Image.composite(light_layer, img.convert("RGBA"), rays_mask_img)


def _apply_global_png_overlay(img: Image.Image, opts: dict) -> Image.Image:
    if not opts or not opts.get("enabled", False):
        return img
        
    data_url = opts.get("image", "")
    opacity = float(opts.get("opacity", 0.5))
    
    if not data_url or opacity <= 0.001:
        return img
        
    try:
        if "," in data_url:
            b64_str = data_url.split(",")[1]
        else:
            b64_str = data_url
            
        overlay_bytes = base64.b64decode(b64_str)
        overlay = Image.open(BytesIO(overlay_bytes)).convert("RGBA")
        overlay = overlay.resize(img.size, Image.Resampling.LANCZOS)
        
        if opacity < 1.0:
            r, g, b, a = overlay.split()
            a = a.point(lambda p: int(p * opacity))
            overlay = Image.merge("RGBA", (r, g, b, a))
            
        return Image.alpha_composite(img.convert("RGBA"), overlay)
    except Exception as e:
        print(f"Error applying global PNG overlay: {e}")
        return img


def _effect_instances(effects: dict, key: str) -> list[dict]:
    value = effects.get(key)
    if isinstance(value, list):
        return [item for item in value[:2] if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _apply_effects_by_target(img: Image.Image, effects: dict | None, target_name: str) -> Image.Image:
    if not effects:
        return img
        
    img = img.convert("RGBA")
    
    # 1. Inner shadow
    for opts in _effect_instances(effects, "inner_shadow"):
        if not opts.get("enabled", False):
            continue
        if opts.get("target", "artwork") == target_name:
            img = _apply_inner_shadow(img, opts)
            
    # 2. Glass reflection (only if explicitly enabled for this target)
    for opts in _effect_instances(effects, "glass_reflection"):
        if not opts.get("enabled", False):
            continue
        if opts.get("target", "artwork") == target_name:
            img = _apply_glass_reflection(img, opts)
            
    # 3. Faded Matte Finish
    for opts in _effect_instances(effects, "matte_finish"):
        if not opts.get("enabled", False):
            continue
        if opts.get("target", "artwork") == target_name:
            img = _apply_matte_finish(img, opts)
            
    # 4. Ambient light warmth tint
    for opts in _effect_instances(effects, "color_tint"):
        if not opts.get("enabled", False):
            continue
        if opts.get("target", "artwork") == target_name:
            img = _apply_color_tint(img, opts)
            
    # 5. Sunlight blinds shadow (Gobo)
    for opts in _effect_instances(effects, "gobo_shadow"):
        if not opts.get("enabled", False):
            continue
        if opts.get("target", "artwork") == target_name:
            img = _apply_gobo_shadow(img, opts)
            
    # 6. Photoshop Adjustments
    for opts in _effect_instances(effects, "photoshop_adjustments"):
        if not opts.get("enabled", False):
            continue
        if opts.get("target", "all") == target_name:
            img = _apply_photoshop_adjustments(img, opts)
            
    # 7. Global Reflections & Rays
    for opts in _effect_instances(effects, "global_reflections"):
        if not opts.get("enabled", False):
            continue
        if opts.get("target", "all") == target_name:
            window_type = opts.get("window_type", "none")
            window_opacity = float(opts.get("window_opacity", 0.0))
            if window_type != "none" and window_opacity > 0.001:
                width, height = img.size
                window_blur = float(opts.get("window_blur", 20.0))
                blur_px = max(2, int(window_blur * width / 800))
                window_mask = _create_window_frame_mask(width, height, window_type, blur_px)
                window_mask = window_mask.point(lambda p: int(p * window_opacity))
                reflection_layer = Image.new("RGBA", (width, height), (255, 255, 255, 255))
                img = Image.composite(reflection_layer, img, window_mask)
                
            rays_type = opts.get("rays_type", "none")
            rays_opacity = float(opts.get("rays_opacity", 0.0))
            rays_angle = float(opts.get("rays_angle", 0.0))
            if rays_type != "none" and rays_opacity > 0.001:
                img = _apply_sun_rays(img, rays_type, opacity=rays_opacity, angle=rays_angle)
                
    # 8. Global PNG Overlay
    for opts in _effect_instances(effects, "global_png_overlay"):
        if not opts.get("enabled", False):
            continue
        if opts.get("target", "all") == target_name:
            img = _apply_global_png_overlay(img, opts)
            
    return img


def _apply_realism_filter(artwork_layer: Image.Image, effects: dict | None = None) -> Image.Image:
    # 1. Convert to RGBA
    img = artwork_layer.convert("RGBA")
    r, g, b, a = img.split()

    # 2. Black & White Point Compression (Print Mapping)
    # Compress luminance range slightly to map pure screen black/white to physical print (0->8, 255->246)
    lut = [int(i * (246 - 8) / 255 + 8) for i in range(256)]
    r = r.point(lut)
    g = g.point(lut)
    b = b.point(lut)
    img = Image.merge("RGBA", (r, g, b, a))

    # 3. High-Frequency Fine Paper Grain
    width, height = img.size
    # Generate a tiny pre-cached 128x128 noise patch with values in [250, 255]
    noise_bytes = bytes(random.randint(250, 255) for _ in range(128 * 128))
    noise_patch = Image.frombytes("L", (128, 128), noise_bytes)

    # Tile the patch to match target size
    noise_tile = Image.new("L", (width, height))
    for x in range(0, width, 128):
        for y in range(0, height, 128):
            noise_tile.paste(noise_patch, (x, y))

    noise_rgba = Image.merge("RGBA", (noise_tile, noise_tile, noise_tile, Image.new("L", (width, height), 255)))
    img = ImageChops.multiply(img, noise_rgba)

    # Apply premium targeted artwork filters
    if effects:
        img = _apply_effects_by_target(img, effects, "artwork")

    # 8. Glass reflection default sheen logic on artwork target (backward-compatible sheen)
    glass_instances = _effect_instances(effects, "glass_reflection") if effects else []
    custom_glass = glass_instances[0] if glass_instances else {}
    glass_enabled = custom_glass.get("enabled", False)
    glass_target = custom_glass.get("target", "artwork")
    
    if glass_enabled and glass_target == "artwork":
        # Handled in the target dispatcher already, so skip applying it again
        pass
    elif not glass_enabled:
        # Diagonal Ambient Sheen (Glass reflection highlight, Top-Left to Bottom-Right)
        # Generate diagonal gradient starting at 3/255 opacity (1.1%) and ending at 13/255 opacity (5.1%)
        grad_data = []
        for row in range(8):
            for col in range(8):
                val = int(3 + 10 * ((col + row) / 14.0))
                grad_data.append(val)
        grad_small = Image.frombytes("L", (8, 8), bytes(grad_data))
        grad_large = grad_small.resize((width, height), Image.Resampling.BILINEAR)

        sheen = Image.merge("RGBA", (
            Image.new("L", (width, height), 255),
            Image.new("L", (width, height), 255),
            Image.new("L", (width, height), 255),
            grad_large
        ))
        img = Image.alpha_composite(img, sheen)
    return img


def _apply_edge_feathering_ssaa(
    artwork_layer: Image.Image,
    area: dict[str, Any],
    canvas_size: tuple[int, int],
    corners_present: bool = False,
) -> Image.Image:
    SS_FACTOR = 3
    hr_canvas_size = (canvas_size[0] * SS_FACTOR, canvas_size[1] * SS_FACTOR)
    width, height = area["width"], area["height"]
    
    if corners_present:
        from services.image_utils import get_perspective_coefficients
        src_coords = [
            (0.0, 0.0),
            (float(width), 0.0),
            (float(width), float(height)),
            (0.0, float(height))
        ]
        # Scale destination corner coordinates by SS_FACTOR
        hr_dst_coords = [(float(p["x"]) * SS_FACTOR, float(p["y"]) * SS_FACTOR) for p in area["corners"]]
        hr_coefficients = get_perspective_coefficients(src_coords, hr_dst_coords)
        
        # Warp the artwork quad at 3x supersampled resolution (with BICUBIC for high detail)
        hr_artwork_canvas = artwork_layer.transform(
            hr_canvas_size,
            Image.Transform.PERSPECTIVE,
            hr_coefficients,
            Image.Resampling.BICUBIC
        )
        
        # Generate the footprint mask at 3x resolution
        mask_base = Image.new("L", (width, height), 255)
        hr_mask_canvas = mask_base.transform(
            hr_canvas_size,
            Image.Transform.PERSPECTIVE,
            hr_coefficients,
            Image.Resampling.BICUBIC
        )
        
        # Blur the mask at 3x resolution (radius 3.6 pixels corresponds to 1.2 at 1x)
        hr_feathered_mask = hr_mask_canvas.filter(ImageFilter.GaussianBlur(radius=3.6))
        
        # Multiply high-res alpha with the soft blurred quad mask
        hr_r, hr_g, hr_b, hr_a = hr_artwork_canvas.split()
        hr_a_feathered = ImageChops.multiply(hr_a, hr_feathered_mask)
        hr_artwork_canvas = Image.merge("RGBA", (hr_r, hr_g, hr_b, hr_a_feathered))
        
        # Downscale to destination canvas size using LANCZOS (removes all sawtooth aliasing!)
        return hr_artwork_canvas.resize(canvas_size, Image.Resampling.LANCZOS)
    else:
        # Flat placement anti-aliasing and feathering
        hr_artwork_layer = artwork_layer.resize((width * SS_FACTOR, height * SS_FACTOR), Image.Resampling.LANCZOS)
        hr_artwork_canvas = Image.new("RGBA", hr_canvas_size, (0, 0, 0, 0))
        hr_artwork_canvas.alpha_composite(hr_artwork_layer, dest=(area["x"] * SS_FACTOR, area["y"] * SS_FACTOR))
        
        mask_base = Image.new("L", (width * SS_FACTOR, height * SS_FACTOR), 255)
        hr_mask_canvas = Image.new("L", hr_canvas_size, 0)
        hr_mask_canvas.paste(mask_base, (area["x"] * SS_FACTOR, area["y"] * SS_FACTOR))
        
        hr_feathered_mask = hr_mask_canvas.filter(ImageFilter.GaussianBlur(radius=3.6))
        
        hr_r, hr_g, hr_b, hr_a = hr_artwork_canvas.split()
        hr_a_feathered = ImageChops.multiply(hr_a, hr_feathered_mask)
        hr_artwork_canvas = Image.merge("RGBA", (hr_r, hr_g, hr_b, hr_a_feathered))
        
        return hr_artwork_canvas.resize(canvas_size, Image.Resampling.LANCZOS)


def render_simple_mockup(
    *,
    template_id: str,
    artwork_path: Path,
    output_format: str,
    templates_folder: Path,
    output_folder: Path,
    fit_mode: str | None = None,
    realism: bool = True,
    effects: dict | None = None,
    artwork_area: dict | None = None,
    raw_artwork_area: dict | None = None,
    mask_name: str | None = None,
) -> RenderResult:
    if output_format.lower() != "png":
        raise RenderValidationError("Only png output format is currently supported")

    template_folder, manifest = load_manifest(templates_folder, template_id)
    if "simple" not in manifest["supported_modes"]:
        raise RenderValidationError("Template does not support simple rendering")

    if artwork_area:
        manifest = manifest.copy()
        manifest["artwork_area"] = artwork_area
    if mask_name:
        manifest = manifest.copy()
        manifest["mask"] = mask_name

    canvas_size, area = _validated_canvas_and_area(manifest)
    background = load_rgba(_safe_asset_path(template_folder, manifest["background"]))
    if realism:
        active_effects = effects if effects is not None else manifest.get("effects")
        background = _apply_effects_by_target(background, active_effects, "mockup")

    foreground_path = _optional_asset_path(template_folder, manifest.get("foreground"))
    if background.size != canvas_size:
        raise InvalidTemplateError("Background must match canvas size")

    artwork = load_rgba(artwork_path)
    final_fit_mode = fit_mode if fit_mode else str(manifest.get("fit_mode", "cover"))
    if final_fit_mode == "auto":
        artwork_width, artwork_height = artwork.size
        frame_width, frame_height = area["width"], area["height"]
        artwork_ratio = artwork_width / artwork_height
        frame_ratio = frame_width / frame_height
        
        if abs(artwork_ratio - frame_ratio) < 0.03:
            final_fit_mode = "stretch"
        else:
            def get_orientation(ratio):
                if ratio > 1.15:
                    return "landscape"
                if ratio < 0.85:
                    return "portrait"
                return "square"
            
            art_orient = get_orientation(artwork_ratio)
            frame_orient = get_orientation(frame_ratio)
            
            if art_orient == frame_orient:
                final_fit_mode = "cover"
            else:
                final_fit_mode = "stretch"

    mask_name = manifest.get("mask")
    if mask_name and _is_green_frame_raw(raw_artwork_area):
        composed = _render_green_frame_mockup(
            background=background,
            artwork=artwork,
            template_folder=template_folder,
            mask_name=mask_name,
            canvas_size=canvas_size,
            area=area,
            raw_artwork_area=raw_artwork_area,
            fit_mode=final_fit_mode,
            realism=realism,
            effects=active_effects if realism else effects,
        )
        if foreground_path:
            foreground = load_rgba(foreground_path)
            if foreground.size != canvas_size:
                raise InvalidTemplateError("Foreground must match canvas size")
            composed = Image.alpha_composite(composed, foreground)
        if realism:
            composed = _apply_effects_by_target(composed, active_effects, "all")
        output_folder.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_name = f"mockup_{timestamp}_{uuid4().hex}.png"
        composed.save(output_folder / output_name, format="PNG")
        return RenderResult(
            mode="simple",
            template_id=template_id,
            output_url=f"/outputs/{output_name}",
            width=canvas_size[0],
            height=canvas_size[1],
        )

    artwork_layer = fit_artwork(
        artwork,
        (area["width"], area["height"]),
        final_fit_mode,
    )
    if mask_name:
        mask = _mask_for_artwork(template_folder, mask_name, canvas_size, area)
        artwork_layer.putalpha(ImageChops.multiply(artwork_layer.getchannel("A"), mask))

    # Apply scene integration filters (Print contrast, Paper Grain, Custom/Default reflection & shadows) if enabled
    if realism:
        active_effects = effects if effects is not None else manifest.get("effects")
        artwork_layer = _apply_realism_filter(artwork_layer, active_effects)

    if realism:
        # Premium Super Sample Anti-Aliased (SSAA) rendering and edge softening
        artwork_canvas = _apply_edge_feathering_ssaa(
            artwork_layer,
            area,
            canvas_size,
            corners_present=("corners" in area)
        )
    else:
        # Legacy exact 1x pixel mapping path (for backward-compatible unit tests)
        if "corners" in area:
            from services.image_utils import get_perspective_coefficients
            src_coords = [
                (0.0, 0.0),
                (float(area["width"]), 0.0),
                (float(area["width"]), float(area["height"])),
                (0.0, float(area["height"]))
            ]
            dst_coords = [(float(p["x"]), float(p["y"])) for p in area["corners"]]
            coefficients = get_perspective_coefficients(src_coords, dst_coords)
            artwork_canvas = artwork_layer.transform(
                canvas_size,
                Image.Transform.PERSPECTIVE,
                coefficients,
                Image.Resampling.BICUBIC
            )
        else:
            artwork_canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
            artwork_canvas.alpha_composite(artwork_layer, dest=(area["x"], area["y"]))

    composed = Image.alpha_composite(background, artwork_canvas)
    if foreground_path:
        foreground = load_rgba(foreground_path)
        if foreground.size != canvas_size:
            raise InvalidTemplateError("Foreground must match canvas size")
        composed = Image.alpha_composite(composed, foreground)

    if realism:
        active_effects = effects if effects is not None else manifest.get("effects")
        composed = _apply_effects_by_target(composed, active_effects, "all")

    output_folder.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_name = f"mockup_{timestamp}_{uuid4().hex}.png"
    composed.save(output_folder / output_name, format="PNG")
    return RenderResult(
        mode="simple",
        template_id=template_id,
        output_url=f"/outputs/{output_name}",
        width=canvas_size[0],
        height=canvas_size[1],
    )
