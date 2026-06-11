"""Flexible multi-item mockup render requests.

One API request carries a JSON spec plus the uploaded artwork files. Each
item in the spec produces one mockup:

- a single artwork renders into a single-frame template,
- multiple artworks in the same item form a set and render together into one
  multi-frame (green screen) template — wall sets, multi-device scenes, etc.

Templates are chosen manually (``template_id``) or automatically from
``selection`` hints (product type, orientation, keywords) combined with the
artworks' aspect ratios and the set size. Failures are isolated per item.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from PIL import Image

from services.image_utils import ImageProcessingError, store_uploaded_artwork
from services.simple_mockup_service import (
    InvalidTemplateError,
    RenderValidationError,
    TemplateNotFoundError,
    render_simple_mockup,
)
from services.template_selection_service import SelectionCriteria, select_best_template


MAX_ITEMS_PER_REQUEST = 20
MAX_ARTWORKS_PER_ITEM = 12


class RequestValidationError(ValueError):
    """The request spec itself is malformed (rejected before rendering)."""


def _ensure_dict(value: Any, label: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RequestValidationError(f"{label} must be an object")
    return value


def _artwork_keys(item: dict, item_id: str) -> list[str]:
    raw = item.get("artworks", item.get("artwork"))
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw or not all(isinstance(key, str) and key for key in raw):
        raise RequestValidationError(
            f"Item '{item_id}': 'artworks' must be a file field name or a non-empty list of them"
        )
    if len(raw) > MAX_ARTWORKS_PER_ITEM:
        raise RequestValidationError(
            f"Item '{item_id}': at most {MAX_ARTWORKS_PER_ITEM} artworks per item"
        )
    return raw


def _merge_output(defaults: dict, item: dict) -> tuple[str, int | None]:
    output = {**_ensure_dict(defaults.get("output"), "defaults.output"),
              **_ensure_dict(item.get("output"), "item.output")}
    output_format = str(output.get("format") or "png").lower()
    quality = output.get("quality")
    if quality is not None:
        try:
            quality = int(quality)
        except (TypeError, ValueError):
            raise RequestValidationError("output.quality must be an integer between 1 and 100")
    return output_format, quality


def _resolve_flag(item: dict, defaults: dict, key: str, fallback: bool) -> bool:
    for source in (item, defaults):
        if key in source:
            return bool(source[key])
    return fallback


def _artwork_ratio(path: Path) -> float:
    with Image.open(path) as image:
        return image.width / image.height if image.height else 1.0


def _build_criteria(item: dict, artwork_paths: list[Path]) -> SelectionCriteria:
    selection = _ensure_dict(item.get("selection"), "item.selection")
    keywords = selection.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [keywords]
    if not isinstance(keywords, list):
        raise RequestValidationError("selection.keywords must be a string or list of strings")
    mockup_kind = selection.get("mockup_kind")
    if isinstance(mockup_kind, str) and mockup_kind.strip():
        keywords = list(keywords) + [mockup_kind]
    return SelectionCriteria(
        product_type=selection.get("product_type") or None,
        set_size=len(artwork_paths),
        aspect_ratios=[_artwork_ratio(path) for path in artwork_paths],
        orientation=selection.get("orientation") or None,
        keywords=[kw for kw in keywords if isinstance(kw, str)],
    )


def execute_batch_render(
    spec: Any,
    files: Mapping[str, Any],
    *,
    templates_folder: Path,
    output_folder: Path,
    upload_folder: Path,
    template_record_lookup: Callable[[str], dict | None] | None = None,
    default_realism: bool = True,
) -> dict[str, Any]:
    spec = _ensure_dict(spec, "spec")
    items = spec.get("items")
    if not isinstance(items, list) or not items:
        raise RequestValidationError("spec.items must be a non-empty list")
    if len(items) > MAX_ITEMS_PER_REQUEST:
        raise RequestValidationError(f"At most {MAX_ITEMS_PER_REQUEST} items per request")
    defaults = _ensure_dict(spec.get("defaults"), "spec.defaults")

    stored_paths: dict[str, Path] = {}
    results: list[dict[str, Any]] = []

    for index, raw_item in enumerate(items):
        item_id = ""
        try:
            item = _ensure_dict(raw_item, f"items[{index}]")
            item_id = str(item.get("id") or f"item_{index + 1}")
            artwork_keys = _artwork_keys(item, item_id)

            artwork_paths: list[Path] = []
            for key in artwork_keys:
                if key not in stored_paths:
                    upload = files.get(key)
                    if upload is None or not getattr(upload, "filename", ""):
                        raise RequestValidationError(
                            f"Item '{item_id}': uploaded file '{key}' is missing from the request"
                        )
                    stored_paths[key] = store_uploaded_artwork(upload, upload_folder)
                artwork_paths.append(stored_paths[key])

            template_id = str(item.get("template_id") or "").strip()
            selection_meta: dict[str, Any] = {"mode": "manual" if template_id else "auto"}
            if not template_id:
                criteria = _build_criteria(item, artwork_paths)
                template_id = select_best_template(templates_folder, criteria) or ""
                if not template_id:
                    raise RenderValidationError(
                        "No template matches the requested criteria"
                    )
                selection_meta["criteria"] = {
                    "set_size": criteria.set_size,
                    "product_type": criteria.product_type,
                    "orientation": criteria.orientation,
                    "keywords": criteria.keywords,
                }

            output_format, quality = _merge_output(defaults, item)
            fit_mode = str(item.get("fit_mode") or defaults.get("fit_mode") or "").lower() or None
            realism = _resolve_flag(item, defaults, "realism", default_realism)

            record = template_record_lookup(template_id) if template_record_lookup else None
            record = record if isinstance(record, dict) else {}
            if not fit_mode and record.get("fit_mode"):
                fit_mode = record.get("fit_mode")

            result = render_simple_mockup(
                template_id=template_id,
                artwork_path=artwork_paths[0],
                artwork_paths=artwork_paths,
                output_format=output_format,
                templates_folder=templates_folder,
                output_folder=output_folder,
                fit_mode=fit_mode,
                realism=realism,
                quality=quality,
                effects=record.get("effects"),
                artwork_area=record.get("artwork_area"),
                raw_artwork_area=record.get("raw_artwork_area"),
                mask_name=record.get("mask_name"),
            )
            results.append(
                {
                    "id": item_id,
                    "success": True,
                    "template_id": result.template_id,
                    "output_url": result.output_url,
                    "width": result.width,
                    "height": result.height,
                    "artworks": artwork_keys,
                    "selection": selection_meta,
                }
            )
        except RequestValidationError:
            raise
        except (
            TemplateNotFoundError,
            InvalidTemplateError,
            RenderValidationError,
            ImageProcessingError,
        ) as error:
            message = str(error) or error.__class__.__name__
            if isinstance(error, TemplateNotFoundError):
                message = f"Template not found: {error}"
            results.append({"id": item_id or f"item_{index + 1}", "success": False, "error": message})
        except Exception as error:  # render must never take down sibling items
            results.append(
                {"id": item_id or f"item_{index + 1}", "success": False, "error": str(error) or "Rendering failed"}
            )

    return {
        "success": all(item["success"] for item in results),
        "items": results,
    }
