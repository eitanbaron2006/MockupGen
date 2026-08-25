"""Drop corner lists that carry no coordinates.

Older detections left entries like [{}, {"x": null}, {"x": null, "y": null}]
behind in raw_artwork_area. They describe nothing, and drawing them fills the
console with "Expected number, NaN". The area's own box still describes the
frame, so the empty list is simply removed.
"""
import argparse
import json
import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from config import Config
from services.catalog_service import CatalogService


def _is_point(value) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("x"), (int, float))
        and isinstance(value.get("y"), (int, float))
    )


def _clean(area: dict) -> tuple[dict, list[str]]:
    """`area` without the corner lists that hold no coordinates."""
    removed: list[str] = []
    cleaned = dict(area)
    for key in ("corners", "original_corners", "inner_corners", "outer_corners"):
        corners = cleaned.get(key)
        if isinstance(corners, list) and corners and not all(_is_point(point) for point in corners):
            cleaned.pop(key)
            removed.append(key)
    regions = cleaned.get("regions")
    if isinstance(regions, list):
        fixed_regions = []
        for index, region in enumerate(regions):
            if not isinstance(region, dict):
                fixed_regions.append(region)
                continue
            region_clean, region_removed = _clean(region)
            removed.extend(f"regions[{index}].{name}" for name in region_removed)
            fixed_regions.append(region_clean)
        cleaned["regions"] = fixed_regions
    return cleaned, removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the cleaned areas back")
    parser.add_argument("--database", default=Config.DATABASE_PATH)
    args = parser.parse_args()

    catalog = CatalogService(Path(args.database))
    touched = 0
    for record in sorted(catalog.list_templates(), key=lambda item: item["name"]):
        changes = {}
        for field in ("artwork_area", "raw_artwork_area"):
            area = record.get(field)
            if not isinstance(area, dict):
                continue
            cleaned, removed = _clean(area)
            if removed:
                print(f"  {record['name']:<7} {record['template_id']}  drops {field}: {', '.join(removed)}")
                changes[field] = cleaned
        if not changes:
            continue
        touched += 1
        if args.apply:
            catalog.update_template(record["template_id"], changes)
    print(f"\n{touched} templates carry corner lists with no coordinates")
    if touched and not args.apply:
        print("dry run -- pass --apply to clean them")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
