"""Where a newly imported mockup belongs, and what it should be called.

Filing a mockup by hand means reading two things off the picture: what shape
the opening is, and how many openings there are. Detection already answers
both, so the studio can do it -- and the name it gives says the same two things
out loud.

The scheme is the one this catalog already used, read back off 72 templates
that all agreed: ``<letter><frames>-<index>``. The letter is the shape, the
number is how many openings the mockup has, the index counts within that
family. Only the letters changed with the vocabulary: portrait is P, wide is W,
square is S, and a set whose openings are not all one shape is M.
"""
from __future__ import annotations

import re
from typing import Any

# The shape words the studio uses, and the letter each one is filed under.
SHAPE_LETTERS = {
    "portrait": "P",
    "wide": "W",
    "square": "S",
    "mixed": "M",
}

# ...and the shelf each shape belongs on, single-frame or set.
SHELF_NAMES = {
    ("portrait", False): "Portrait",
    ("wide", False): "Wide",
    ("square", False): "Square",
    ("portrait", True): "Portrait Sets",
    ("wide", True): "Wide Sets",
    ("square", True): "Square Sets",
    ("mixed", True): "Mixed Sets",
    # A single opening cannot be mixed; if it ever is, it is a plain one.
    ("mixed", False): "Mixed Sets",
}

_NAME_PATTERN = re.compile(r"^(?:MAIN-)?([A-Z]+)(\d+)-(\d+)$")


def shape_of(width: float, height: float) -> str:
    """One opening's shape, in the words the studio files by."""
    if not width or not height:
        return "square"
    ratio = width / height
    if ratio > 1.15:
        return "wide"
    if ratio < 0.87:
        return "portrait"
    return "square"


def classify(artwork_area: dict[str, Any] | None, raw_artwork_area: dict[str, Any] | None) -> dict[str, Any]:
    """What the detector found, read as a shelf and a name.

    A set is described by the shapes of its own openings rather than by the
    box around them: three tall frames in a row make a wide bounding box and a
    portrait set, which is the distinction the old names already drew.
    """
    regions = []
    if isinstance(raw_artwork_area, dict) and isinstance(raw_artwork_area.get("regions"), list):
        regions = [region for region in raw_artwork_area["regions"] if isinstance(region, dict)]
    frames = len(regions) or 1

    if regions:
        shapes = {
            shape_of(float(region.get("width", 0)), float(region.get("height", 0)))
            for region in regions
        }
        shape = shapes.pop() if len(shapes) == 1 else "mixed"
    else:
        area = artwork_area if isinstance(artwork_area, dict) else {}
        shape = shape_of(float(area.get("width", 0)), float(area.get("height", 0)))

    return {
        "shape": shape,
        "frames": frames,
        "letter": SHAPE_LETTERS.get(shape, "M"),
        "shelf": SHELF_NAMES.get((shape, frames > 1), "Mixed Sets"),
    }


def next_name(existing_names: list[str], letter: str, frames: int) -> str:
    """The next free name in that family, e.g. P1-24 or M5-3.

    The index counts within the letter and frame count, so a name says what the
    mockup is before it is opened, and adding one never renames another.
    """
    highest = 0
    for name in existing_names:
        match = _NAME_PATTERN.match(str(name or "").strip())
        if not match:
            continue
        if match.group(1) != letter or int(match.group(2)) != frames:
            continue
        highest = max(highest, int(match.group(3)))
    return f"{letter}{frames}-{highest + 1}"
