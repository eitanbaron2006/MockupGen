"""Rename every template to a short code: orientation letter, frame count, index.

    H5-1   five landscape frames, first of its kind
    V1-4   one portrait frame, fourth of its kind
    S2-1   two square frames
    M3-1   three frames that do not share one orientation

The name is a display label -- nothing keys off it -- and it lives in the
catalog, which is where a template's live state belongs. Run without arguments
to see the mapping, with --apply to write it.
"""
import argparse
import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from config import Config
from services.catalog_service import CatalogService


def _orientation(width: float, height: float) -> str:
    if height <= 0:
        return "S"
    ratio = width / height
    if 0.92 <= ratio <= 1.08:
        return "S"
    return "H" if ratio > 1.08 else "V"


def _frame_boxes(record: dict) -> list[tuple[float, float]]:
    raw = record.get("raw_artwork_area")
    regions = raw.get("regions") if isinstance(raw, dict) else None
    boxes: list[tuple[float, float]] = []
    if isinstance(regions, list):
        for region in regions:
            if not isinstance(region, dict):
                continue
            corners = region.get("corners") or region.get("inner_corners")
            if isinstance(corners, list) and len(corners) >= 4:
                xs = [float(point["x"]) for point in corners]
                ys = [float(point["y"]) for point in corners]
                boxes.append((max(xs) - min(xs), max(ys) - min(ys)))
            elif region.get("width") and region.get("height"):
                boxes.append((float(region["width"]), float(region["height"])))
    if boxes:
        return boxes
    area = record.get("artwork_area") or {}
    if area.get("width") and area.get("height"):
        return [(float(area["width"]), float(area["height"]))]
    return []


def code_for(record: dict) -> str:
    boxes = _frame_boxes(record)
    if not boxes:
        return "X0"
    letters = {_orientation(width, height) for width, height in boxes}
    letter = letters.pop() if len(letters) == 1 else "M"
    return f"{letter}{len(boxes)}"


def plan(catalog: CatalogService) -> list[tuple[str, str, str]]:
    """(template_id, current name, new name), numbered within each code."""
    records = sorted(catalog.list_templates(), key=lambda record: record["template_id"])
    counters: dict[str, int] = {}
    rows = []
    for record in records:
        code = code_for(record)
        counters[code] = counters.get(code, 0) + 1
        rows.append((record["template_id"], record["name"], f"{code}-{counters[code]}"))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the new names to the catalog")
    parser.add_argument("--database", default=Config.DATABASE_PATH, help="catalog database path")
    args = parser.parse_args()

    catalog = CatalogService(Path(args.database))
    rows = plan(catalog)
    width = max((len(old) for _, old, _ in rows), default=0)
    for template_id, old, new in rows:
        print(f"{new:<7} {old:<{width}}  {template_id}")
    print(f"\n{len(rows)} templates")
    if not args.apply:
        print("dry run -- pass --apply to write these names")
        return 0
    for template_id, _, new in rows:
        catalog.update_template(template_id, {"name": new})
    print("names written to the catalog")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
