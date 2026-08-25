"""Sort templates into the existing wall-art categories by their frames.

A template belongs with the shape of the frames it holds: portrait frames in
the vertical category, landscape in the horizontal, square in the square. The
category slug is also the `product_type` the public API filters on and the
automatic picker matches against, so this is what makes a request for
`horizontal-wall-art` return templates that actually are.

Templates in a Main category are left alone. Run without arguments to see the
moves, with --apply to make them.
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from config import Config
from services.catalog_service import CatalogService
from scripts.rename_templates import _frame_boxes, _orientation

TARGET_BY_SHAPE = {
    "V": "vertival-wall-art-frame",
    "H": "horizontal-wall-art",
    "S": "square-wall-art",
}


def shape_of(record: dict) -> tuple[str, str]:
    """The shape the template's frames make, and how that was decided."""
    boxes = _frame_boxes(record)
    if not boxes:
        return "", "no frames"
    counts = Counter(_orientation(width, height) for width, height in boxes)
    (top, top_count), *rest = counts.most_common()
    if rest and rest[0][1] == top_count:
        # A tie between shapes: fall back to the shape of the whole artwork area.
        area = record.get("artwork_area") or {}
        if area.get("width") and area.get("height"):
            fallback = _orientation(float(area["width"]), float(area["height"]))
            return fallback, f"mixed {dict(counts)}, artwork area is {fallback}"
        return top, f"mixed {dict(counts)}"
    detail = f"{top_count} of {sum(counts.values())} frames" if len(counts) > 1 else f"{top_count} frames"
    return top, detail


def plan(catalog: CatalogService) -> list[dict]:
    categories = {category["slug"]: category for category in catalog.list_categories()}
    by_id = {category["id"]: category for category in categories.values()}
    moves = []
    for record in sorted(catalog.list_templates(), key=lambda item: item["name"]):
        current = by_id.get(record.get("category_id"))
        current_name = current["name"] if current else "(none)"
        if current_name.strip().lower().startswith("main"):
            continue
        shape, why = shape_of(record)
        target_slug = TARGET_BY_SHAPE.get(shape)
        target = categories.get(target_slug) if target_slug else None
        if not target:
            moves.append({"record": record, "from": current_name, "to": None, "why": why})
            continue
        if current and current["id"] == target["id"]:
            continue
        moves.append({"record": record, "from": current_name, "to": target, "why": why})
    return moves


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="move the templates")
    parser.add_argument("--database", default=Config.DATABASE_PATH)
    args = parser.parse_args()

    catalog = CatalogService(Path(args.database))
    moves = plan(catalog)
    for move in moves:
        target = move["to"]["name"] if move["to"] else "?? no category for this shape"
        print(f"  {move['record']['name']:<7} {move['from']:<26} -> {target:<26} ({move['why']})")
    print(f"\n{len(moves)} templates to move")
    if not args.apply:
        print("dry run -- pass --apply to move them")
        return 0
    for move in moves:
        if move["to"]:
            catalog.update_template(move["record"]["template_id"], {"category_id": move["to"]["id"]})
    print("templates moved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
