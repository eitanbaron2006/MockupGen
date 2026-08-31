"""One artwork in, a whole listing's worth of images out.

Selling a print on Etsy takes more than one mockup: a hero shot that carries
the listing, a close-up that proves the print quality, a second room so the
buyer sees the piece in context, and a size chart. Sellers build that set by
hand, one render at a time. This module builds it in one call.

Every image comes from machinery that already exists -- automatic template
selection, the simple renderer, the frame slots the detector found -- so a
bundle looks exactly like the mockups the studio makes anywhere else. The
close-up is deliberately *not* a second render: it is a crop of the hero image
around the frame, which is both free and guaranteed to match the shot it came
from.

Roles fail independently. A template that will not render costs the listing
that one picture, never the whole set.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFont

from services.image_utils import ImageProcessingError, load_rgba
from services.simple_mockup_service import (
    InvalidTemplateError,
    RenderValidationError,
    TemplateNotFoundError,
    load_manifest,
    merge_template_record,
    render_simple_mockup,
    save_render_image,
    supported_output_formats,
)
from services.template_selection_service import (
    SelectionCriteria,
    rank_templates,
    template_frames,
)

ROLE_HERO = "hero"
ROLE_CLOSEUP = "closeup"
ROLE_SCALE = "scale"
ROLE_SIZE_GUIDE = "size_guide"

# The order is the order an Etsy listing shows them in.
DEFAULT_ROLES = (ROLE_HERO, ROLE_CLOSEUP, ROLE_SCALE, ROLE_SIZE_GUIDE)
BUNDLE_ROLES = DEFAULT_ROLES

# How much of the surrounding scene the close-up keeps around the frame, as a
# fraction of the frame's own size. Enough to show the wall and the frame edge,
# not so much that it stops being a close-up.
DEFAULT_CLOSEUP_PADDING = 0.28


class BundleValidationError(ValueError):
    """The request itself is wrong -- not one image inside it."""


@dataclass(frozen=True)
class PrintSize:
    label: str
    width: float
    height: float

    @property
    def ratio(self) -> float:
        return self.width / self.height if self.height else 1.0


# Standard retail print sizes, portrait, grouped by the ratio they belong to.
# A print sold at 2:3 cannot be offered at 8x10 without cropping, so the guide
# only ever shows the family the artwork actually fits.
_SIZE_FAMILIES: tuple[tuple[str, str, tuple[PrintSize, ...]], ...] = (
    (
        "2:3",
        "in",
        (
            PrintSize("4x6", 4, 6),
            PrintSize("8x12", 8, 12),
            PrintSize("12x18", 12, 18),
            PrintSize("16x24", 16, 24),
            PrintSize("20x30", 20, 30),
            PrintSize("24x36", 24, 36),
        ),
    ),
    (
        "3:4",
        "in",
        (
            PrintSize("6x8", 6, 8),
            PrintSize("9x12", 9, 12),
            PrintSize("12x16", 12, 16),
            PrintSize("18x24", 18, 24),
        ),
    ),
    (
        "4:5",
        "in",
        (
            PrintSize("4x5", 4, 5),
            PrintSize("8x10", 8, 10),
            PrintSize("11x14", 11, 14),
            PrintSize("16x20", 16, 20),
        ),
    ),
    (
        "5:7",
        "in",
        (
            PrintSize("5x7", 5, 7),
            PrintSize("10x14", 10, 14),
            PrintSize("15x21", 15, 21),
        ),
    ),
    (
        "ISO A",
        "cm",
        (
            PrintSize("A5", 14.8, 21.0),
            PrintSize("A4", 21.0, 29.7),
            PrintSize("A3", 29.7, 42.0),
            PrintSize("A2", 42.0, 59.4),
            PrintSize("A1", 59.4, 84.1),
        ),
    ),
    (
        "1:1",
        "in",
        (
            PrintSize("8x8", 8, 8),
            PrintSize("12x12", 12, 12),
            PrintSize("16x16", 16, 16),
            PrintSize("20x20", 20, 20),
        ),
    ),
)


def size_family_for_ratio(ratio: float) -> tuple[str, str, list[PrintSize]]:
    """The retail sizes closest to an artwork's shape, already turned the right way.

    Ratios are compared in portrait so a 3:2 landscape piece matches the same
    family as its 2:3 portrait twin; the sizes come back rotated to match how
    the artwork is actually oriented.
    """
    if ratio <= 0:
        ratio = 1.0
    landscape = ratio > 1.0
    portrait_ratio = 1 / ratio if landscape else ratio

    def distance(family: tuple[str, str, tuple[PrintSize, ...]]) -> float:
        return abs(family[2][0].ratio - portrait_ratio)

    name, unit, sizes = min(_SIZE_FAMILIES, key=distance)
    if landscape:
        return name, unit, [PrintSize(size.label, size.height, size.width) for size in sizes]
    return name, unit, list(sizes)


def _coerce_sizes(raw: Any) -> list[PrintSize]:
    """Caller-supplied sizes, for a seller who offers their own list."""
    if not isinstance(raw, list) or not raw:
        raise BundleValidationError("sizes must be a non-empty list")
    sizes: list[PrintSize] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise BundleValidationError(f"sizes[{index}] must be an object")
        try:
            width = float(entry["width"])
            height = float(entry["height"])
        except (KeyError, TypeError, ValueError) as error:
            raise BundleValidationError(
                f"sizes[{index}] needs numeric 'width' and 'height'"
            ) from error
        if width <= 0 or height <= 0:
            raise BundleValidationError(f"sizes[{index}] must be positive")
        label = str(entry.get("label") or f"{width:g}x{height:g}")
        sizes.append(PrintSize(label, width, height))
    return sizes


def _artwork_ratio(path: Path) -> float:
    with Image.open(path) as image:
        return image.width / image.height if image.height else 1.0


def _catalog_records(
    lookup: Callable[[str], dict | None] | None, templates_folder: Path
) -> dict[str, dict] | None:
    if lookup is None or not templates_folder.is_dir():
        return None
    records: dict[str, dict] = {}
    for folder in templates_folder.iterdir():
        if not folder.is_dir():
            continue
        record = lookup(folder.name)
        if isinstance(record, dict):
            records[folder.name] = record
    return records


def _render_with_record(
    template_id: str,
    artwork_path: Path,
    *,
    templates_folder: Path,
    output_folder: Path,
    output_format: str,
    quality: int | None,
    realism: bool,
    record: dict[str, Any],
) -> tuple[Any, dict[str, Any]]:
    """Render one template the way the batch API does, and hand back its manifest.

    The manifest comes back because the close-up needs the frame slots, and
    re-reading it there would be a second disk trip for data already in hand.
    """
    _, manifest = load_manifest(templates_folder, template_id)
    manifest = merge_template_record(manifest, record)
    result = render_simple_mockup(
        template_id=template_id,
        artwork_path=artwork_path,
        output_format=output_format,
        templates_folder=templates_folder,
        output_folder=output_folder,
        fit_mode=record.get("fit_mode") or None,
        realism=realism,
        quality=quality,
        effects=record.get("effects"),
        artwork_area=record.get("artwork_area"),
        raw_artwork_area=manifest.get("raw_artwork_area"),
        mask_name=record.get("mask_name"),
    )
    return result, manifest


def closeup_crop_box(
    frames: list[dict[str, Any]],
    canvas: tuple[int, int],
    padding: float = DEFAULT_CLOSEUP_PADDING,
) -> tuple[int, int, int, int]:
    """Where to crop a rendered mockup so the artwork fills the shot.

    The biggest frame wins: on a set template every slot holds the same
    artwork, and the largest one carries the most pixels, so the close-up is
    the sharpest one available.
    """
    width, height = canvas
    if not frames:
        return (0, 0, width, height)
    slot = max(frames, key=lambda frame: frame["width"] * frame["height"])
    margin_x = slot["width"] * padding
    margin_y = slot["height"] * padding
    left = max(0, int(round(slot["x"] - margin_x)))
    top = max(0, int(round(slot["y"] - margin_y)))
    right = min(width, int(round(slot["x"] + slot["width"] + margin_x)))
    bottom = min(height, int(round(slot["y"] + slot["height"] + margin_y)))
    if right - left < 2 or bottom - top < 2:
        return (0, 0, width, height)
    return (left, top, right, bottom)


def _font(size: int) -> Any:
    """A readable font wherever this runs, including a bare Docker image.

    Pillow's bundled fallback scales since 10.1, which matters because the slim
    base image ships no system fonts at all; a real face is used when one
    happens to be installed.
    """
    for candidate in ("DejaVuSans.ttf", "Arial.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def render_size_guide(
    artwork_path: Path,
    *,
    sizes: list[PrintSize],
    unit: str = "in",
    ratio_label: str = "",
    canvas: tuple[int, int] = (2000, 2000),
    background: tuple[int, int, int] = (250, 249, 246),
    ink: tuple[int, int, int] = (32, 32, 32),
) -> Image.Image:
    """The size chart buyers look for: nested outlines, largest to smallest.

    Every rectangle shares the bottom-left corner, so the sizes read as one
    print growing rather than a row of unrelated boxes, and each label sits in
    its own rectangle's top-right corner where no other label can reach it.
    """
    if not sizes:
        raise BundleValidationError("A size guide needs at least one size")
    ordered = sorted(sizes, key=lambda size: size.width * size.height, reverse=True)
    width, height = canvas
    image = Image.new("RGB", canvas, background)
    draw = ImageDraw.Draw(image)

    margin = int(min(width, height) * 0.07)
    header = int(min(width, height) * 0.16)
    footer = int(min(width, height) * 0.05)
    area_width = width - margin * 2
    area_height = height - header - margin - footer
    biggest = ordered[0]
    scale = min(area_width / biggest.width, area_height / biggest.height)

    # Centre the nest in the space under the title: a landscape family is
    # limited by width and a portrait one by height, and whichever is not the
    # limit used to leave the chart pinned to a corner with a hole beside it.
    stack_width = biggest.width * scale
    stack_height = biggest.height * scale
    origin_x = int(margin + (area_width - stack_width) / 2)
    origin_y = int(header + (area_height + stack_height) / 2)

    title_font = _font(int(min(width, height) * 0.045))
    label_font = _font(int(min(width, height) * 0.026))
    note_font = _font(int(min(width, height) * 0.021))

    draw.text((margin, int(margin * 0.7)), "PRINT SIZES", font=title_font, fill=ink)
    if ratio_label:
        draw.text(
            (margin, int(margin * 0.7) + int(min(width, height) * 0.06)),
            f"Ratio {ratio_label}",
            font=note_font,
            fill=(110, 110, 110),
        )

    # The artwork itself, ghosted inside the largest size, so the chart is
    # recognisably *this* print and not a generic diagram.
    box_width = max(1, int(round(biggest.width * scale)))
    box_height = max(1, int(round(biggest.height * scale)))
    try:
        artwork = load_rgba(artwork_path).convert("RGB")
        preview = artwork.resize((box_width, box_height), Image.LANCZOS)
        image.paste(
            Image.blend(preview, Image.new("RGB", preview.size, (255, 255, 255)), 0.62),
            (origin_x, origin_y - box_height),
        )
    except (ImageProcessingError, OSError, ValueError):
        pass  # A chart without the ghost is still a usable chart.

    # Two passes: every outline first, then every label. Drawn in one pass, a
    # smaller rectangle's border cut straight through the label of the size
    # around it.
    boxes = []
    for index, size in enumerate(ordered):
        box_width = max(1, int(round(size.width * scale)))
        box_height = max(1, int(round(size.height * scale)))
        top = origin_y - box_height
        line = max(2, int(min(width, height) * (0.005 if index == 0 else 0.0035)))
        boxes.append((size, origin_x + box_width, top, line))
        draw.rectangle(
            (origin_x, top, origin_x + box_width, origin_y),
            outline=ink if index == 0 else (90, 90, 90),
            width=line,
        )

    for size, right, top, line in boxes:
        label = f"{size.label} {unit}" if unit else size.label
        pad = max(4, line * 3)
        anchor_x = right - pad
        anchor_y = top + pad
        text_box = draw.textbbox((anchor_x, anchor_y), label, font=label_font, anchor="ra")
        draw.rectangle(
            (text_box[0] - pad, text_box[1] - pad, text_box[2] + pad, text_box[3] + pad),
            fill=background,
        )
        draw.text((anchor_x, anchor_y), label, font=label_font, fill=ink, anchor="ra")

    draw.text(
        (margin, height - footer),
        "Sizes shown to scale with each other, not to actual size.",
        font=note_font,
        fill=(120, 120, 120),
    )
    return image


def build_listing_bundle(
    artwork_path: Path,
    *,
    templates_folder: Path,
    output_folder: Path,
    roles: list[str] | tuple[str, ...] | None = None,
    selection: dict[str, Any] | None = None,
    templates: dict[str, str] | None = None,
    sizes: Any = None,
    output_format: str = "png",
    quality: int | None = None,
    realism: bool = True,
    closeup_padding: float = DEFAULT_CLOSEUP_PADDING,
    template_record_lookup: Callable[[str], dict | None] | None = None,
) -> dict[str, Any]:
    """Build the images one listing needs, from a single artwork."""
    requested = [str(role) for role in (roles if roles else DEFAULT_ROLES)]
    unknown = [role for role in requested if role not in BUNDLE_ROLES]
    if unknown:
        raise BundleValidationError(
            f"Unknown bundle role(s): {', '.join(unknown)}. "
            f"Supported: {', '.join(BUNDLE_ROLES)}"
        )
    if not requested:
        raise BundleValidationError("At least one role is required")
    output_format = (output_format or "png").lower()
    if output_format not in supported_output_formats():
        raise BundleValidationError(f"Unsupported output format: {output_format}")

    selection = selection if isinstance(selection, dict) else {}
    picks = {str(key): str(value) for key, value in (templates or {}).items() if value}
    ratio = _artwork_ratio(artwork_path)

    keywords = selection.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    criteria = SelectionCriteria(
        product_type=selection.get("product_type") or None,
        set_size=1,
        aspect_ratios=[ratio],
        orientation=selection.get("orientation") or None,
        keywords=[kw for kw in keywords if isinstance(kw, str)],
    )
    records = _catalog_records(template_record_lookup, templates_folder)
    ranked_ids = [
        candidate["template_id"] for candidate in rank_templates(templates_folder, criteria, records)
    ]

    def lookup_record(template_id: str) -> dict[str, Any]:
        if records is not None and template_id in records:
            return records[template_id]
        record = template_record_lookup(template_id) if template_record_lookup else None
        return record if isinstance(record, dict) else {}

    hero_id = picks.get(ROLE_HERO) or (ranked_ids[0] if ranked_ids else "")
    # The second room has to be a different room, or the listing shows the same
    # picture twice; only when nothing else exists does it fall back to the hero.
    scale_id = picks.get(ROLE_SCALE) or next(
        (candidate for candidate in ranked_ids if candidate != hero_id), hero_id
    )
    role_templates = {
        ROLE_HERO: hero_id,
        ROLE_CLOSEUP: picks.get(ROLE_CLOSEUP) or hero_id,
        ROLE_SCALE: scale_id,
    }

    items: list[dict[str, Any]] = []
    rendered: dict[str, tuple[Any, dict[str, Any]]] = {}

    def render_once(template_id: str) -> tuple[Any, dict[str, Any]]:
        # The close-up shares the hero's render instead of paying for a second one.
        if template_id not in rendered:
            rendered[template_id] = _render_with_record(
                template_id,
                artwork_path,
                templates_folder=templates_folder,
                output_folder=output_folder,
                output_format=output_format,
                quality=quality,
                realism=realism,
                record=lookup_record(template_id),
            )
        return rendered[template_id]

    for role in requested:
        try:
            if role == ROLE_SIZE_GUIDE:
                if sizes is None:
                    family, unit, family_sizes = size_family_for_ratio(ratio)
                else:
                    family, unit, family_sizes = "custom", "in", _coerce_sizes(sizes)
                guide = render_size_guide(
                    artwork_path,
                    sizes=family_sizes,
                    unit=unit,
                    ratio_label="" if family == "custom" else family,
                )
                name = save_render_image(guide, output_folder, output_format, quality)
                items.append(
                    {
                        "role": role,
                        "success": True,
                        "output_url": f"/outputs/{name}",
                        "width": guide.width,
                        "height": guide.height,
                        "size_family": family,
                        "unit": unit,
                        "sizes": [
                            {"label": size.label, "width": size.width, "height": size.height}
                            for size in family_sizes
                        ],
                    }
                )
                continue

            template_id = role_templates[role]
            if not template_id:
                raise RenderValidationError("No template matches the requested criteria")

            result, manifest = render_once(template_id)
            if role != ROLE_CLOSEUP:
                items.append(
                    {
                        "role": role,
                        "success": True,
                        "template_id": result.template_id,
                        "output_url": result.output_url,
                        "width": result.width,
                        "height": result.height,
                        "selection": "manual" if picks.get(role) else "auto",
                    }
                )
                continue

            source_path = Path(output_folder) / result.output_url.rsplit("/", 1)[-1]
            with Image.open(source_path) as full:
                box = closeup_crop_box(template_frames(manifest), full.size, closeup_padding)
                crop = full.crop(box)
                crop.load()
            name = save_render_image(crop, output_folder, output_format, quality)
            items.append(
                {
                    "role": role,
                    "success": True,
                    "template_id": template_id,
                    "output_url": f"/outputs/{name}",
                    "width": crop.width,
                    "height": crop.height,
                    "crop": {
                        "x": box[0],
                        "y": box[1],
                        "width": box[2] - box[0],
                        "height": box[3] - box[1],
                    },
                    "selection": "manual" if picks.get(role) else "auto",
                }
            )
        except (
            TemplateNotFoundError,
            InvalidTemplateError,
            RenderValidationError,
            ImageProcessingError,
            BundleValidationError,
        ) as error:
            message = str(error) or error.__class__.__name__
            if isinstance(error, TemplateNotFoundError):
                message = f"Template not found: {error}"
            items.append({"role": role, "success": False, "error": message})
        except Exception as error:  # one picture must never cost the whole listing
            items.append(
                {"role": role, "success": False, "error": str(error) or "Rendering failed"}
            )

    return {
        "success": all(item["success"] for item in items),
        "artwork_ratio": round(ratio, 4),
        "items": items,
    }
