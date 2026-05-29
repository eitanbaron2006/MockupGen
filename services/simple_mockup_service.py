import json
import math
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image, ImageChops, ImageFilter

from services.image_utils import ImageProcessingError, fit_artwork, load_mask, load_rgba


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


def _apply_realism_filter(artwork_layer: Image.Image) -> Image.Image:
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
    
    # 4. Diagonal Ambient Sheen (Glass reflection highlight, Top-Left to Bottom-Right)
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
) -> RenderResult:
    if output_format.lower() != "png":
        raise RenderValidationError("Only png output format is currently supported")

    template_folder, manifest = load_manifest(templates_folder, template_id)
    if "simple" not in manifest["supported_modes"]:
        raise RenderValidationError("Template does not support simple rendering")

    canvas_size, area = _validated_canvas_and_area(manifest)
    background = load_rgba(_safe_asset_path(template_folder, manifest["background"]))
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

    artwork_layer = fit_artwork(
        artwork,
        (area["width"], area["height"]),
        final_fit_mode,
    )
    mask_name = manifest.get("mask")
    if mask_name:
        mask = _mask_for_artwork(template_folder, mask_name, canvas_size, area)
        artwork_layer.putalpha(ImageChops.multiply(artwork_layer.getchannel("A"), mask))

    # Apply scene integration filters (Print contrast, Paper Grain, Diagonal glass reflection sheen) if enabled
    if realism:
        artwork_layer = _apply_realism_filter(artwork_layer)

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
