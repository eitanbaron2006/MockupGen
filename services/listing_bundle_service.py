"""One artwork in, a whole listing's worth of images out.

Selling a print takes more than one mockup: the main shot Etsy shows in search,
the piece in a few rooms, and a size chart. Sellers build that set by hand, one
render at a time. This module builds it in one call.

What goes in the set is not guessed. A listing set the admin saved says which
mockups a listing gets and in what order (see ``listing_set_service``); this
module only carries those decisions out. Where no set is named it falls back to
choosing by aspect-ratio fit, and even then it honours the one rule the ranking
cannot know: a MAIN template is the listing's main image, so it fills the hero
slot and nothing else.

The size chart costs nothing to draw: it is a ready-made guide from the
library, matched to the artwork's ratio, rather than a drawing made up on the
spot.

Jobs fail independently. A template that will not render costs the listing that
one picture, never the whole set.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from services.image_utils import ImageProcessingError
from services.listing_set_service import KIND_MOCKUP, KIND_SIZE_GUIDE
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
from services.template_selection_service import SelectionCriteria, rank_templates


class BundleValidationError(ValueError):
    """The request itself is wrong -- not one picture inside it."""


@dataclass(frozen=True)
class PrintSize:
    label: str
    width: float
    height: float

    @property
    def ratio(self) -> float:
        return self.width / self.height if self.height else 1.0


# The retail ratios a print is sold at. A guide in the library is tagged with
# one of these, and an artwork is matched to the family its own shape fits: a
# 2:3 print cannot be offered at 8x10 without cropping.
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

SIZE_FAMILY_NAMES = tuple(name for name, _, _ in _SIZE_FAMILIES)

# A chart drawn for a 2:3 portrait is not the chart for a 3:2 landscape, and
# naming the ratio the way round it is drawn says so without a second field.
_LANDSCAPE_KEYS = {
    "2:3": "3:2",
    "3:4": "4:3",
    "4:5": "5:4",
    "5:7": "7:5",
    "ISO A": "ISO A landscape",
    "1:1": "1:1",
}
_PORTRAIT_KEYS = {"ISO A": "ISO A portrait"}

GUIDE_RATIOS = (
    "2:3", "3:2",
    "3:4", "4:3",
    "4:5", "5:4",
    "5:7", "7:5",
    "ISO A portrait", "ISO A landscape",
    "1:1",
)


def orientation_for_guide_ratio(key: str) -> str:
    """Which way round a chart is drawn, read off the ratio it is named for."""
    cleaned = str(key or "").strip()
    if cleaned == "1:1":
        return "square"
    if cleaned in _LANDSCAPE_KEYS.values():
        return "landscape"
    return "portrait"


_RATIO_SAMPLES = {
    "2:3": 2 / 3, "3:2": 3 / 2,
    "3:4": 3 / 4, "4:3": 4 / 3,
    "4:5": 4 / 5, "5:4": 5 / 4,
    "5:7": 5 / 7, "7:5": 7 / 5,
    "ISO A portrait": 0.7071, "ISO A landscape": 1.4142,
    "1:1": 1.0,
}


def guide_ratio_shape(key: str) -> float:
    """An artwork ratio standing for a library key, to look its sizes up with."""
    return _RATIO_SAMPLES.get(str(key or "").strip(), 1.0)


def guide_ratio_key(ratio: float) -> str:
    """The library key an artwork of this shape needs."""
    family, _, _ = size_family_for_ratio(ratio)
    orientation = orientation_for_ratio(ratio)
    if orientation == "landscape":
        return _LANDSCAPE_KEYS.get(family, family)
    return _PORTRAIT_KEYS.get(family, family)


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


def artwork_ratio(path: Path) -> float:
    with Image.open(path) as image:
        return image.width / image.height if image.height else 1.0


def orientation_for_ratio(ratio: float) -> str:
    if ratio > 1.15:
        return "landscape"
    if ratio < 0.85:
        return "portrait"
    return "square"


def catalog_records(
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


def auto_jobs(
    ratio: float,
    *,
    templates_folder: Path,
    records: dict[str, dict] | None = None,
    selection: dict[str, Any] | None = None,
    is_main_template: Callable[[str], bool] = lambda _template_id: False,
    templates: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """The listing to build when the caller named no set.

    Ranking knows only how well a frame fits the artwork, so the rest is filled
    in here: the best-fitting MAIN template leads, because that is the picture
    Etsy puts in search results, and the other slots take the best fits that
    are not MAIN.
    """
    selection = selection if isinstance(selection, dict) else {}
    picks = {str(key): str(value) for key, value in (templates or {}).items() if value}
    keywords = selection.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    criteria = SelectionCriteria(
        product_type=selection.get("product_type") or None,
        set_size=1,
        aspect_ratios=[ratio],
        orientation=selection.get("orientation") or None,
        keywords=[keyword for keyword in keywords if isinstance(keyword, str)],
    )
    ranked = [
        candidate["template_id"]
        for candidate in rank_templates(templates_folder, criteria, records)
    ]
    supporting = [template_id for template_id in ranked if not is_main_template(template_id)]

    hero = picks.get("hero") or next(
        (template_id for template_id in ranked if is_main_template(template_id)),
        ranked[0] if ranked else "",
    )
    rest = [
        template_id for template_id in supporting if template_id != hero
    ][:2]

    jobs: list[dict[str, Any]] = [
        {"item": 0, "kind": KIND_MOCKUP, "template_id": hero, "hero": True},
    ]
    for offset, template_id in enumerate(rest):
        jobs.append({"item": offset + 1, "kind": KIND_MOCKUP, "template_id": template_id, "hero": False})
    if not rest:
        jobs.append({
            "item": 1,
            "kind": KIND_MOCKUP,
            "error": "No mockup left for this slot -- MAIN templates are kept for the main image",
        })
    jobs.append({"item": len(jobs), "kind": KIND_SIZE_GUIDE, "guide_id": None})
    for job in jobs:
        if job["kind"] == KIND_MOCKUP and not job.get("template_id") and not job.get("error"):
            job["error"] = "No template matches the requested criteria"
    return jobs


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
) -> Any:
    """Render one template exactly the way the batch API does."""
    _, manifest = load_manifest(templates_folder, template_id)
    manifest = merge_template_record(manifest, record)
    return render_simple_mockup(
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


def pick_size_guide(
    guides: list[dict[str, Any]],
    *,
    ratio: float,
    guide_id: int | None = None,
) -> dict[str, Any] | None:
    """The chart in the library that matches this artwork.

    A guide is tagged with the ratio it is drawn for, the way round it is drawn
    -- 2:3 and 3:2 are different charts -- so one lookup answers both.
    """
    if guide_id is not None:
        return next((guide for guide in guides if int(guide.get("id", -1)) == int(guide_id)), None)
    key = guide_ratio_key(ratio)
    return next((guide for guide in guides if str(guide.get("ratio")) == key), None)


def build_listing_bundle(
    artwork_path: Path,
    *,
    jobs: list[dict[str, Any]],
    templates_folder: Path,
    output_folder: Path,
    output_format: str = "png",
    quality: int | None = None,
    realism: bool = True,
    template_record_lookup: Callable[[str], dict | None] | None = None,
    records: dict[str, dict] | None = None,
    size_guides: list[dict[str, Any]] | None = None,
    guide_asset_path: Callable[[dict[str, Any]], Path] | None = None,
    generate_size_guide: Callable[[float, str], Image.Image] | None = None,
) -> dict[str, Any]:
    """Make every picture a listing set asks for, from one artwork."""
    output_format = (output_format or "png").lower()
    if output_format not in supported_output_formats():
        raise BundleValidationError(f"Unsupported output format: {output_format}")
    if not jobs:
        raise BundleValidationError("This set has nothing in it")

    ratio = artwork_ratio(artwork_path)
    guides = list(size_guides or [])
    items: list[dict[str, Any]] = []
    rendered: dict[str, tuple[Any, dict[str, Any]]] = {}

    def lookup_record(template_id: str) -> dict[str, Any]:
        if records is not None and template_id in records:
            return records[template_id]
        record = template_record_lookup(template_id) if template_record_lookup else None
        return record if isinstance(record, dict) else {}

    def render_once(template_id: str) -> Any:
        # One template used twice in a listing is still one render.
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

    for job in jobs:
        entry = {
            "item": job.get("item"),
            "kind": job["kind"],
            "label": "Size guide" if job["kind"] == KIND_SIZE_GUIDE else "",
        }
        try:
            if job.get("error"):
                raise RenderValidationError(job["error"])

            if job["kind"] == KIND_SIZE_GUIDE:
                guide = pick_size_guide(guides, ratio=ratio, guide_id=job.get("guide_id"))
                family, unit, _ = size_family_for_ratio(ratio)
                if guide and guide_asset_path:
                    with Image.open(guide_asset_path(guide)) as chart:
                        chart.load()
                        name = save_render_image(chart, output_folder, output_format, quality)
                        width, height = chart.size
                    entry.update(
                        {
                            "success": True,
                            "output_url": f"/outputs/{name}",
                            "width": width,
                            "height": height,
                            "guide_id": guide.get("id"),
                            "guide_name": guide.get("name"),
                            "size_family": guide.get("ratio") or family,
                            "source": guide.get("source", "upload"),
                        }
                    )
                elif generate_size_guide is not None:
                    # Nothing in the library fits this shape, so one is drawn to
                    # order. It is the fallback, not the default: a chart a buyer
                    # measures a wall against is worth having a real one on file.
                    chart = generate_size_guide(ratio, family)
                    name = save_render_image(chart, output_folder, output_format, quality)
                    entry.update(
                        {
                            "success": True,
                            "output_url": f"/outputs/{name}",
                            "width": chart.width,
                            "height": chart.height,
                            "size_family": family,
                            "unit": unit,
                            "source": "ai",
                        }
                    )
                else:
                    raise RenderValidationError(
                        f"No size guide in the library for a {family} print, "
                        "and no generator is configured"
                    )
                items.append(entry)
                continue

            template_id = str(job.get("template_id") or "")
            if not template_id:
                raise RenderValidationError("No mockup for this slot")

            result = render_once(template_id)
            entry.update(
                {
                    "template_id": template_id,
                    # The mockup's own name is the label: a picture in a listing
                    # is identified by the mockup it came from, and naming it
                    # twice only invited the two to disagree.
                    "label": str(lookup_record(template_id).get("name") or template_id),
                    "success": True,
                    "output_url": result.output_url,
                    "width": result.width,
                    "height": result.height,
                    "hero": bool(job.get("hero")),
                }
            )
            items.append(entry)
        except (
            TemplateNotFoundError,
            InvalidTemplateError,
            RenderValidationError,
            ImageProcessingError,
            BundleValidationError,
            OSError,
        ) as error:
            message = str(error) or error.__class__.__name__
            if isinstance(error, TemplateNotFoundError):
                message = f"Template not found: {error}"
            items.append({**entry, "success": False, "error": message})
        except Exception as error:  # one picture must never cost the whole listing
            items.append(
                {**entry, "success": False, "error": str(error) or "Rendering failed"}
            )

    return {
        "success": all(item["success"] for item in items),
        "artwork_ratio": round(ratio, 4),
        "items": items,
    }
