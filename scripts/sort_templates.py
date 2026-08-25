"""Sort templates into the existing wall-art categories by their frames.

A template belongs with the shape of the frames it holds, and with how many of
them it holds: one portrait frame is a vertical wall art template, three are a
vertical set, and a mockup whose frames do not share one shape -- a laptop
beside a phone, a gallery wall of mixed frames -- is a varied set. The category
slug is also the `product_type` the public API filters on and the automatic
picker matches against, so this is what makes a request for
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

SINGLE_BY_SHAPE = {
    "V": "vertival-wall-art-frame",
    "H": "horizontal-wall-art",
    "S": "square-wall-art",
}
SET_BY_SHAPE = {
    "V": "vertival-wall-art-frame-sets",
    "H": "horizontal-wall-art-frame-sets",
}
VARIED_SET = "varient-wall-art-frame-sets"


def target_slug(record: dict) -> tuple[str, str]:
    """Which category the template belongs in, and why."""
    boxes = _frame_boxes(record)
    if not boxes:
        return "", "no frames"
    counts = Counter(_orientation(width, height) for width, height in boxes)
    shapes = sorted(counts)
    if len(boxes) == 1:
        shape = shapes[0]
        return SINGLE_BY_SHAPE.get(shape, ""), f"one {shape} frame"
    if len(shapes) == 1 and shapes[0] in SET_BY_SHAPE:
        return SET_BY_SHAPE[shapes[0]], f"{len(boxes)} {shapes[0]} frames"
    # Frames that do not share one shape -- and a set of square frames, which
    # has no category of its own -- belong with the varied sets.
    return VARIED_SET, f"{len(boxes)} frames, " + ", ".join(f"{count}{shape}" for shape, count in sorted(counts.items()))


def plan(catalog: CatalogService) -> list[dict]:
    categories = {category["slug"]: category for category in catalog.list_categories()}
    by_id = {category["id"]: category for category in categories.values()}
    moves = []
    for record in sorted(catalog.list_templates(), key=lambda item: item["name"]):
        current = by_id.get(record.get("category_id"))
        current_name = current["name"] if current else "(none)"
        if current_name.strip().lower().startswith("main"):
            continue
        slug, why = target_slug(record)
        target = categories.get(slug) if slug else None
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
