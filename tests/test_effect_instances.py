import sys
from pathlib import Path

from PIL import Image


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from services.simple_mockup_service import _apply_effects_by_target


def test_effect_target_accepts_two_instances_for_same_effect_type():
    base = Image.new("RGBA", (2, 2), (100, 100, 100, 255))
    effects = {
        "color_tint": [
            {
                "enabled": True,
                "temperature": 100,
                "intensity": 1,
                "target": "mockup",
            },
            {
                "enabled": True,
                "temperature": 100,
                "intensity": 1,
                "target": "mockup",
            },
        ]
    }

    result = _apply_effects_by_target(base, effects, "mockup")

    assert result.getpixel((0, 0)) == (200, 150, 0, 255)


def test_effect_target_keeps_instances_scoped_to_their_target():
    base = Image.new("RGBA", (2, 2), (100, 100, 100, 255))
    effects = {
        "color_tint": [
            {
                "enabled": True,
                "temperature": 100,
                "intensity": 1,
                "target": "artwork",
            },
            {
                "enabled": True,
                "temperature": -100,
                "intensity": 1,
                "target": "mockup",
            },
        ]
    }

    result = _apply_effects_by_target(base, effects, "mockup")

    assert result.getpixel((0, 0)) == (50, 75, 150, 255)
