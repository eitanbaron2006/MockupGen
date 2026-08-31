"""What a shop listing is made of, decided by the admin instead of guessed.

Automatic template selection scores one thing: how close a template's frame is
to the artwork's aspect ratio. It has no idea that the MAIN categories hold the
image Etsy shows as the product's thumbnail in search -- so left alone it will
spend a hero mockup on a filler slot. A listing set is the admin saying, once
per product type, which mockups a listing gets.

A set is three things, and order is not one of them:

- one ``hero`` mockup   -- the main image, and only a MAIN template may be it
- up to 18 ``mockup``s  -- the rest of the listing, pinned or drawn from a category
- one ``size_guide``    -- the print-size chart, from the library

The MAIN rule is enforced here rather than left to whoever builds the set.
"""
from __future__ import annotations

from typing import Any, Callable

KIND_MOCKUP = "mockup"
KIND_SIZE_GUIDE = "size_guide"
ITEM_KINDS = (KIND_MOCKUP, KIND_SIZE_GUIDE)

# What the listing is for, as the shop-side app that calls the API names it --
# not the shelves the mockups are filed on.
PRODUCT_TYPES = (
    "Printable Wall Art",
    "PNG Artwork Pack",
    "Lightroom Presets",
    "Digital Planners",
)

# A listing carries one main image; the rest is how far Etsy itself will go.
MAX_MOCKUPS = 18
MAX_COUNT = 12

ORIENTATIONS = ("portrait", "landscape", "square", "any")


class ListingSetError(ValueError):
    """A set that cannot be saved as written."""


def normalize_items(
    raw: Any,
    *,
    is_main_template: Callable[[str], bool],
    is_main_category: Callable[[Any], bool],
) -> list[dict[str, Any]]:
    """Check a set an admin is saving, and return it in one shape.

    Everything the renderer later relies on is settled here -- kinds, pins,
    counts, one hero, one chart, and the MAIN rule -- so a saved set is a set
    that can run.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ListingSetError("items must be a list")

    items: list[dict[str, Any]] = []
    heroes = 0
    charts = 0
    mockups = 0

    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ListingSetError(f"items[{index}] must be an object")
        kind = str(entry.get("kind") or "").strip().lower()
        if kind not in ITEM_KINDS:
            raise ListingSetError(
                f"items[{index}].kind must be one of {', '.join(ITEM_KINDS)}"
            )

        if kind == KIND_SIZE_GUIDE:
            charts += 1
            if charts > 1:
                raise ListingSetError("A listing carries one size guide")
            item: dict[str, Any] = {"kind": kind}
            guide_id = entry.get("guide_id")
            if guide_id not in (None, ""):
                try:
                    item["guide_id"] = int(guide_id)
                except (TypeError, ValueError) as error:
                    raise ListingSetError(f"items[{index}].guide_id must be a number") from error
            items.append(item)
            continue

        hero = bool(entry.get("hero"))
        if hero:
            heroes += 1
            if heroes > 1:
                raise ListingSetError("A listing has one main image, so a set has one hero")
        template_id = str(entry.get("template_id") or "").strip()
        category_id = entry.get("category_id")
        item = {"kind": kind, "hero": hero}

        if template_id and category_id not in (None, ""):
            raise ListingSetError(
                f"items[{index}]: pin a mockup or draw from a category, not both"
            )
        if template_id:
            if is_main_template(template_id) and not hero:
                raise ListingSetError(
                    "A MAIN mockup is the image Etsy shows in search, so it can only "
                    "be the hero"
                )
            if hero and not is_main_template(template_id):
                raise ListingSetError(
                    "The hero is the listing's main image and comes from a MAIN category"
                )
            item["template_id"] = template_id
            mockups += 0 if hero else 1
        elif category_id not in (None, ""):
            if hero:
                raise ListingSetError("The hero is one chosen mockup, not a category")
            try:
                item["category_id"] = int(category_id)
            except (TypeError, ValueError) as error:
                raise ListingSetError(f"items[{index}].category_id must be a number") from error
            if is_main_category(item["category_id"]):
                raise ListingSetError(
                    "The MAIN categories hold main images and cannot fill the rest "
                    "of the listing"
                )
            count = entry.get("count", 1)
            try:
                item["count"] = int(count)
            except (TypeError, ValueError) as error:
                raise ListingSetError(f"items[{index}].count must be a number") from error
            if not 1 <= item["count"] <= MAX_COUNT:
                raise ListingSetError(f"items[{index}].count must be between 1 and {MAX_COUNT}")
            mockups += item["count"]
        else:
            raise ListingSetError(
                f"items[{index}]: choose a mockup or a category for it to come from"
            )
        items.append(item)

    if mockups > MAX_MOCKUPS:
        raise ListingSetError(f"A listing takes at most {MAX_MOCKUPS} mockups beside the hero")
    return items


def resolve_items(
    items: list[dict[str, Any]],
    *,
    category_templates: Callable[[int], list[dict[str, Any]]],
    rotation: int = 0,
) -> list[dict[str, Any]]:
    """Turn a saved set into the exact pictures to make.

    The hero leads, because that is the image the shop shows first; the rest
    follow in the order they were chosen. A slot that cannot be filled -- an
    emptied category -- comes back as a job carrying its own error, so the rest
    of the listing is still built around it.
    """
    jobs: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        kind = item["kind"]
        base = {"item": index, "kind": kind}

        if kind == KIND_SIZE_GUIDE:
            jobs.append({**base, "guide_id": item.get("guide_id")})
            continue

        if item.get("template_id"):
            jobs.append({**base, "template_id": item["template_id"], "hero": bool(item.get("hero"))})
            continue

        pool = [
            template for template in category_templates(item["category_id"])
            if str(template.get("status", "active")) == "active"
        ]
        if not pool:
            jobs.append({**base, "error": "No active mockups left in that category"})
            continue
        pool.sort(key=lambda template: str(template.get("name") or template["template_id"]))
        # Rotating by the artwork keeps two listings from opening with the same
        # picture, while one artwork always rebuilds the same set.
        start = rotation % len(pool)
        wanted = min(int(item.get("count", 1)), len(pool))
        for offset in range(wanted):
            template = pool[(start + offset) % len(pool)]
            jobs.append({**base, "template_id": template["template_id"], "hero": False})

    jobs.sort(key=lambda job: (0 if job.get("hero") else 1, job["item"]))
    return jobs


def rotation_for(key: str) -> int:
    """A stable number for one artwork, so its set is the same every time."""
    total = 0
    for character in str(key):
        total = (total * 31 + ord(character)) % 1_000_003
    return total
