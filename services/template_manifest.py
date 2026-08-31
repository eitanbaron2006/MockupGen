"""The manifest a published template is rendered from.

`manifest.json` is the snapshot a template was published with: what the render
needs and nothing more. It was built in two places -- when a template is
published, and when a draft is rendered for a preview -- with the same fields
written out twice and drifting apart field by field. This is that one shape,
written once.

Reading it back is `simple_mockup_service.load_manifest`; the catalog, not this
file, is where a template's live state lives.
"""

import json
from pathlib import Path
from typing import Any


def build_manifest(
    template: dict[str, Any],
    *,
    background: str | None = None,
    preview: str | None = None,
    foreground: str | None = None,
    mask: str | None = None,
) -> dict[str, Any]:
    """The manifest for one template, with the asset names the caller knows.

    Publishing copies the files under fixed names and passes those; rendering a
    draft passes what the catalog recorded. Everything else is the same either
    way, which is the point of having one builder.
    """
    return {
        "template_id": template["template_id"],
        "name": template.get("name") or template["template_id"],
        "product_type": template.get("product_type"),
        "canvas_width": template["canvas_width"],
        "canvas_height": template["canvas_height"],
        "artwork_area": template["artwork_area"],
        "fit_mode": template.get("fit_mode") or "cover",
        "orientation": template.get("orientation"),
        "background": background or template.get("background_name") or "background.png",
        "foreground": foreground if foreground is not None else template.get("foreground_name"),
        "mask": mask if mask is not None else template.get("mask_name"),
        "preview": preview or template.get("preview_name") or "preview.png",
        "supported_modes": ["simple"],
        "output_format": "png",
        "effects": template.get("effects"),
        "raw_artwork_area": template.get("raw_artwork_area"),
        "detection_provider": template.get("detection_provider"),
        "detection_confidence": template.get("detection_confidence"),
    }


def write_manifest(template_folder: Path, manifest: dict[str, Any]) -> Path:
    """Write it beside the template's images, formatted to be read by a person."""
    path = template_folder / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
