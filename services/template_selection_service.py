"""Criteria-based automatic template selection for the mockup API.

Given the artworks a client uploaded (their aspect ratios and how many should
appear together in one mockup) plus optional hints (product type, orientation,
free-text keywords), rank the available templates and pick the best match.

Scoring is additive so new signals can be added without restructuring:
- product_type is a hard filter when provided,
- multi-artwork sets require templates with enough frame slots,
- aspect-ratio distance between artworks and frame slots dominates,
- orientation match and keyword hits act as tie-breaking bonuses.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services.simple_mockup_service import (
    InvalidTemplateError,
    TemplateNotFoundError,
    load_manifest,
    merge_template_record,
)


@dataclass
class SelectionCriteria:
    product_type: str | None = None
    set_size: int = 1
    aspect_ratios: list[float] = field(default_factory=list)
    orientation: str | None = None
    keywords: list[str] = field(default_factory=list)


def _area_ratio(area: Any) -> float | None:
    if not isinstance(area, dict):
        return None
    corners = area.get("corners")
    if isinstance(corners, list) and len(corners) >= 4:
        try:
            xs = [float(p["x"]) for p in corners]
            ys = [float(p["y"]) for p in corners]
        except (KeyError, TypeError, ValueError):
            return None
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
    else:
        try:
            width = float(area.get("width", 0))
            height = float(area.get("height", 0))
        except (TypeError, ValueError):
            return None
    if width <= 0 or height <= 0:
        return None
    return width / height


def _ratio_orientation(ratio: float) -> str:
    if ratio > 1.15:
        return "landscape"
    if ratio < 0.85:
        return "portrait"
    return "square"


def template_frames(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Numbered artwork slots in canonical order (top-to-bottom, left-to-right).

    Multi-frame (green screen) templates expose one slot per detected region;
    classic templates have a single slot defined by their artwork area. The
    ordering matches the renderer's region ordering, so frame numbers shown to
    users map exactly onto where each artwork lands.
    """
    raw = manifest.get("raw_artwork_area")
    regions = raw.get("regions") if isinstance(raw, dict) else None
    slots: list[dict[str, Any]] = []
    if isinstance(regions, list):
        usable = []
        for region in regions:
            if not isinstance(region, dict):
                continue
            ratio = _area_ratio(region)
            if ratio is None:
                continue
            usable.append((region, ratio))
        usable.sort(
            key=lambda entry: (
                round(float(entry[0].get("y", 0)) / 25),
                float(entry[0].get("x", 0)),
            )
        )
        for region, ratio in usable:
            slots.append(
                {
                    "x": int(region.get("x", 0)),
                    "y": int(region.get("y", 0)),
                    "width": int(region.get("width", 0)),
                    "height": int(region.get("height", 0)),
                    "ratio": round(ratio, 4),
                    "orientation": _ratio_orientation(ratio),
                }
            )
    if not slots:
        area = manifest.get("artwork_area")
        ratio = _area_ratio(area)
        if ratio is not None and isinstance(area, dict):
            slots.append(
                {
                    "x": int(area.get("x", 0)),
                    "y": int(area.get("y", 0)),
                    "width": int(area.get("width", 0)),
                    "height": int(area.get("height", 0)),
                    "ratio": round(ratio, 4),
                    "orientation": _ratio_orientation(ratio),
                }
            )
    for index, slot in enumerate(slots):
        slot["frame"] = index + 1
    return slots


def template_frame_ratios(manifest: dict[str, Any]) -> list[float]:
    """Aspect ratio of every artwork slot the template offers."""
    return [slot["ratio"] for slot in template_frames(manifest)]


def _ratio_distance(a: float, b: float) -> float:
    if a <= 0 or b <= 0:
        return 10.0
    return abs(math.log(a / b))


def rank_templates(
    templates_folder: Path,
    criteria: SelectionCriteria,
    records: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Score every usable template against the criteria, best first.

    `records` carries the catalog's live state for each template, which is what
    the admin edits; the manifest on disk is only the snapshot taken when the
    template was published.
    """
    ranked: list[dict[str, Any]] = []
    if not templates_folder.is_dir():
        return ranked
    requested_type = (criteria.product_type or "").strip().lower()
    keywords = [kw.strip().lower() for kw in criteria.keywords if isinstance(kw, str) and kw.strip()]

    for template_folder in sorted(path for path in templates_folder.iterdir() if path.is_dir()):
        try:
            _, manifest = load_manifest(templates_folder, template_folder.name)
        except (TemplateNotFoundError, InvalidTemplateError):
            continue
        manifest = merge_template_record(manifest, (records or {}).get(template_folder.name))
        if "simple" not in (manifest.get("supported_modes") or []):
            continue
        if requested_type and str(manifest.get("product_type") or "").lower() != requested_type:
            continue

        slot_ratios = template_frame_ratios(manifest)
        slots = len(slot_ratios)
        if criteria.set_size > 1 and slots < criteria.set_size:
            continue

        score = 0.0
        if criteria.set_size > 1:
            # Prefer an exact frame count; unused frames repeat artworks.
            score -= (slots - criteria.set_size) * 1.5
        else:
            score -= max(0, slots - 1) * 1.0

        if criteria.aspect_ratios and slot_ratios:
            artwork_sorted = sorted(r for r in criteria.aspect_ratios if r > 0)
            slot_sorted = sorted(slot_ratios)
            if artwork_sorted:
                distances = [
                    _ratio_distance(ratio, slot_sorted[index % len(slot_sorted)])
                    for index, ratio in enumerate(artwork_sorted)
                ]
                score -= (sum(distances) / len(distances)) * 4.0

        if criteria.orientation and str(manifest.get("orientation") or "").lower() == criteria.orientation.lower():
            score += 0.5

        if keywords:
            haystack = f"{manifest.get('name', '')} {manifest.get('product_type', '')}".lower()
            score += sum(0.75 for keyword in keywords if keyword in haystack)

        ranked.append(
            {
                "template_id": manifest["template_id"],
                "score": round(score, 4),
                "frame_slots": slots,
                "product_type": manifest.get("product_type"),
                "orientation": manifest.get("orientation"),
            }
        )

    ranked.sort(key=lambda candidate: (-candidate["score"], candidate["template_id"]))
    return ranked


def select_best_template(
    templates_folder: Path,
    criteria: SelectionCriteria,
    records: dict[str, dict[str, Any]] | None = None,
) -> str | None:
    ranked = rank_templates(templates_folder, criteria, records)
    return ranked[0]["template_id"] if ranked else None
