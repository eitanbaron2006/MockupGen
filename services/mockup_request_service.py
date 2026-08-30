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

import math
from pathlib import Path
from typing import Any, Callable, Mapping

from PIL import Image

from services.image_utils import ImageProcessingError, store_uploaded_artwork
from services.simple_mockup_service import (
    InvalidTemplateError,
    RenderValidationError,
    TemplateNotFoundError,
    load_manifest,
    merge_template_record,
    render_simple_mockup,
)
from services.template_selection_service import (
    SelectionCriteria,
    select_best_template,
    template_frames,
)

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


def _artwork_specs(item: dict, item_id: str) -> list[dict]:
    """Normalize 'artworks' entries to {'file': str, 'frame': int | None}.

    Each entry is either a file field name, or an object that optionally pins
    the artwork to a numbered template frame: {"file": "left", "frame": 2}.
    """
    raw = item.get("artworks", item.get("artwork"))
    if isinstance(raw, (str, dict)):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise RequestValidationError(
            f"Item '{item_id}': 'artworks' must be a file field name or a non-empty list"
        )
    if len(raw) > MAX_ARTWORKS_PER_ITEM:
        raise RequestValidationError(
            f"Item '{item_id}': at most {MAX_ARTWORKS_PER_ITEM} artworks per item"
        )
    specs: list[dict] = []
    seen_frames: set[int] = set()
    for entry in raw:
        if isinstance(entry, str) and entry:
            specs.append({"file": entry, "frame": None})
            continue
        if isinstance(entry, dict):
            file_key = entry.get("file")
            frame = entry.get("frame")
            if isinstance(file_key, str) and file_key:
                if frame is not None:
                    try:
                        frame = int(frame)
                    except (TypeError, ValueError) as error:
                        raise RequestValidationError(
                            f"Item '{item_id}': 'frame' must be a positive integer"
                        ) from error
                    if frame < 1:
                        raise RequestValidationError(
                            f"Item '{item_id}': 'frame' must be a positive integer"
                        )
                    if frame in seen_frames:
                        raise RequestValidationError(
                            f"Item '{item_id}': frame {frame} is assigned more than once"
                        )
                    seen_frames.add(frame)
                specs.append({"file": file_key, "frame": frame})
                continue
        raise RequestValidationError(
            f"Item '{item_id}': each artwork must be a file field name or "
            "an object like {\"file\": \"name\", \"frame\": 1}"
        )
    return specs


def _align_artworks_to_frames(
    specs: list[dict],
    paths: list[Path],
    frames: list[dict],
    item_id: str,
) -> list[Path]:
    """Order the artworks so index i lands in template frame i+1.

    Explicit 'frame' assignments win; the rest are auto-placed on the open
    frames with the closest aspect ratio; frames still left over repeat the
    artwork list (matching the renderer's fill behavior).
    """
    frame_count = len(frames)
    explicit = [spec for spec in specs if spec["frame"] is not None]
    for spec in explicit:
        if spec["frame"] > max(1, frame_count):
            raise RenderValidationError(
                f"Item '{item_id}': frame {spec['frame']} does not exist "
                f"(template has {max(1, frame_count)} frame(s))"
            )
    if frame_count <= 1 or len(paths) <= 1:
        return paths
    if len(paths) > frame_count:
        raise RenderValidationError(
            f"Item '{item_id}': {len(paths)} artworks but the template has only {frame_count} frames"
        )

    slots: list[Path | None] = [None] * frame_count
    unassigned: list[Path] = []
    for spec, path in zip(specs, paths):
        if spec["frame"] is not None:
            slots[spec["frame"] - 1] = path
        else:
            unassigned.append(path)

    open_frames = [index for index in range(frame_count) if slots[index] is None]
    if unassigned:
        candidates = []
        for path_index, path in enumerate(unassigned):
            artwork_ratio = _artwork_ratio(path)
            for frame_index in open_frames:
                frame_ratio = float(frames[frame_index].get("ratio") or 1.0)
                distance = abs(math.log(max(artwork_ratio, 0.01) / max(frame_ratio, 0.01)))
                candidates.append((distance, path_index, frame_index))
        candidates.sort()
        used_paths: set[int] = set()
        used_frames: set[int] = set()
        for _, path_index, frame_index in candidates:
            if path_index in used_paths or frame_index in used_frames:
                continue
            slots[frame_index] = unassigned[path_index]
            used_paths.add(path_index)
            used_frames.add(frame_index)

    # Any frames still empty repeat the provided artworks in order.
    cycle_index = 0
    for index in range(frame_count):
        if slots[index] is None:
            slots[index] = paths[cycle_index % len(paths)]
            cycle_index += 1
    return [path for path in slots if path is not None]


def _merge_output(defaults: dict, item: dict) -> tuple[str, int | None]:
    output = {**_ensure_dict(defaults.get("output"), "defaults.output"),
              **_ensure_dict(item.get("output"), "item.output")}
    output_format = str(output.get("format") or "png").lower()
    quality = output.get("quality")
    if quality is not None:
        try:
            quality = int(quality)
        except (TypeError, ValueError) as error:
            raise RequestValidationError(
                "output.quality must be an integer between 1 and 100"
            ) from error
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


def _catalog_records(
    lookup: Callable[[str], dict | None] | None, templates_folder: Path
) -> dict[str, dict] | None:
    """The catalog's live state for every template on disk, for the selectors.

    Automatic selection scores templates by product type, orientation and frame
    count -- all of which the admin edits -- so it has to read the catalog and
    not the manifest snapshots.
    """
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
            artwork_specs = _artwork_specs(item, item_id)
            artwork_keys = [spec["file"] for spec in artwork_specs]

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
                template_id = select_best_template(
                    templates_folder, criteria, _catalog_records(template_record_lookup, templates_folder)
                ) or ""
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

            # Map artworks onto the template's numbered frames: explicit
            # 'frame' picks win, the rest auto-place by aspect-ratio match.
            _, manifest = load_manifest(templates_folder, template_id)
            manifest = merge_template_record(manifest, record)
            raw_artwork_area = manifest.get("raw_artwork_area")
            frames = template_frames(manifest)
            ordered_paths = _align_artworks_to_frames(
                artwork_specs, artwork_paths, frames, item_id
            )

            result = render_simple_mockup(
                template_id=template_id,
                artwork_path=ordered_paths[0],
                artwork_paths=ordered_paths,
                output_format=output_format,
                templates_folder=templates_folder,
                output_folder=output_folder,
                fit_mode=fit_mode,
                realism=realism,
                quality=quality,
                effects=record.get("effects"),
                artwork_area=record.get("artwork_area"),
                raw_artwork_area=raw_artwork_area,
                mask_name=record.get("mask_name"),
            )
            path_to_key = {path: key for key, path in stored_paths.items()}
            frame_assignment = (
                [path_to_key.get(path) for path in ordered_paths]
                if len(artwork_paths) > 1
                else artwork_keys
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
                    "frame_assignment": frame_assignment,
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
