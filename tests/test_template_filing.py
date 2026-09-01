import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from services.template_filing_service import classify, next_name, shape_of  # noqa: E402


def region(x, y, width, height):
    return {"x": x, "y": y, "width": width, "height": height}


def test_a_mockup_is_filed_by_the_shape_of_its_openings():
    """The shelf is read off the openings, not off the box around them.

    Three tall frames in a row make a wide bounding box and a portrait set --
    which is the distinction the catalog's own names already drew, on all 72
    templates that carried one.
    """
    single_tall = classify({"x": 0, "y": 0, "width": 600, "height": 900}, None)
    assert single_tall == {"shape": "portrait", "frames": 1, "letter": "P", "shelf": "Portrait"}

    single_wide = classify({"x": 0, "y": 0, "width": 900, "height": 600}, None)
    assert single_wide["shelf"] == "Wide"
    assert single_wide["letter"] == "W"

    assert classify({"x": 0, "y": 0, "width": 700, "height": 700}, None)["shelf"] == "Square"

    # Three portrait frames side by side: a portrait set, not a wide one.
    row_of_three = classify(
        {"x": 0, "y": 0, "width": 1800, "height": 900},
        {"regions": [region(0, 0, 400, 600), region(500, 0, 400, 600), region(1000, 0, 400, 600)]},
    )
    assert row_of_three == {"shape": "portrait", "frames": 3, "letter": "P", "shelf": "Portrait Sets"}

    # Openings of different shapes are a mixed set, whatever the layout.
    mixed = classify(
        {"x": 0, "y": 0, "width": 1800, "height": 900},
        {"regions": [region(0, 0, 400, 600), region(500, 0, 600, 400), region(1200, 0, 500, 500)]},
    )
    assert mixed["shape"] == "mixed"
    assert mixed["shelf"] == "Mixed Sets"
    assert mixed["letter"] == "M"


def test_the_name_says_the_shape_and_how_many_openings():
    """<letter><frames>-<index>, the scheme the catalog already used.

    Reading it back off the live catalog, the number after the letter matched
    the detected frame count on all 72 named templates, so a name tells the
    admin what a mockup is before they open it.
    """
    existing = ["P1-1", "P1-7", "W1-3", "P3-2", "MAIN-P1-9", "AI Living room", "V1-4"]

    # Counts within its own family, and never renumbers what is already there.
    assert next_name(existing, "P", 1) == "P1-10"
    assert next_name(existing, "P", 3) == "P3-3"
    assert next_name(existing, "W", 1) == "W1-4"
    # A family with nothing in it starts at one.
    assert next_name(existing, "M", 5) == "M5-1"
    assert next_name([], "S", 1) == "S1-1"
    # The old letters do not feed the new ones.
    assert next_name(existing, "P", 1) != "P1-5"


def test_shapes_have_a_deadband_so_near_squares_are_squares():
    assert shape_of(1000, 1000) == "square"
    assert shape_of(1000, 950) == "square"
    assert shape_of(1000, 700) == "wide"
    assert shape_of(700, 1000) == "portrait"
    # Nothing measurable is filed as square rather than crashing.
    assert shape_of(0, 0) == "square"
