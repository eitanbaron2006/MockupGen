import json
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from services.detection_service import DetectionError, validate_proposal
from services.detection_service import build_provider
from services.frame_refinement_service import refine_artwork_area
from services.classic_detection_service import ClassicDetectionProvider
from services.local_detection_service import discover_local_models
from services.vertex_model_service import list_vertex_detection_models
from services.vertex_detection_service import VertexDetectionProvider


def test_detection_proposal_validation_rejects_area_outside_image():
    with pytest.raises(DetectionError):
        validate_proposal(
            {
                "artwork_area": {"x": 90, "y": 20, "width": 30, "height": 40},
                "confidence": 0.9,
            },
            image_width=100,
            image_height=100,
            provider="vertex",
        )


class FakeModels:
    def __init__(self):
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        payload = {
            "box_2d": [100, 200, 750, 800],
            "label": "inner picture opening",
        }
        return type("Response", (), {"text": json.dumps([payload])})()


class FakeClient:
    def __init__(self):
        self.models = FakeModels()


def test_vertex_provider_returns_validated_proposal_and_uses_selected_model(
    tmp_path: Path,
):
    background = tmp_path / "background.png"
    Image.new("RGB", (300, 400), (250, 245, 238)).save(background)
    client = FakeClient()
    provider = VertexDetectionProvider(
        project_id="vertextai-project-497513",
        location="global",
        model="gemini-2.5-flash",
        client=client,
        refine=False,
    )

    proposal = provider.detect(background)

    assert proposal.artwork_area == {"x": 60, "y": 40, "width": 180, "height": 260}
    assert proposal.provider == "vertex"
    assert client.models.calls[0]["model"] == "gemini-2.5-flash"


def test_vertex_refinement_does_not_replace_good_ai_box_with_collapsed_inner_text(
    tmp_path: Path, monkeypatch
):
    background = tmp_path / "background.png"
    Image.new("RGB", (1000, 1000), (250, 245, 238)).save(background)
    client = FakeClient()
    provider = VertexDetectionProvider(
        project_id="vertextai-project-497513",
        model="gemini-2.5-flash",
        client=client,
        refine=True,
    )
    monkeypatch.setattr(
        "services.vertex_detection_service.refine_artwork_area",
        lambda _path, _area: {"x": 300, "y": 300, "width": 120, "height": 120},
    )

    proposal = provider.detect(background)

    assert proposal.artwork_area == {"x": 200, "y": 100, "width": 600, "height": 650}
    assert proposal.provider == "vertex"
    assert "ignored" in proposal.reason


def test_vertex_provider_exposes_raw_artwork_area(tmp_path: Path, monkeypatch):
    background = tmp_path / "background.png"
    Image.new("RGB", (1000, 1000), (250, 245, 238)).save(background)
    client = FakeClient()
    
    # We want a provider with refinement=True, where candidate refinement actually succeeds/differs from raw
    provider = VertexDetectionProvider(
        project_id="vertextai-project-497513",
        model="gemini-2.5-flash",
        client=client,
        refine=True,
    )
    
    refined_mock_area = {"x": 210, "y": 110, "width": 580, "height": 630}
    monkeypatch.setattr(
        "services.vertex_detection_service.refine_artwork_area",
        lambda _path, _area: refined_mock_area,
    )
    
    proposal = provider.detect(background)
    
    # Raw box from fake client is box_2d: [100, 200, 750, 800] -> x: 200, y: 100, width: 600, height: 650
    assert proposal.raw_artwork_area == {"x": 200, "y": 100, "width": 600, "height": 650}
    assert proposal.artwork_area == refined_mock_area
    assert proposal.provider == "vertex+edges"


def test_edge_refinement_snaps_approximate_ai_area_to_dashed_rectangle(tmp_path: Path):
    image_path = tmp_path / "dashed-area.png"
    image = Image.new("RGB", (600, 700), (244, 238, 226))
    draw = ImageDraw.Draw(image)
    expected = {"x": 155, "y": 130, "width": 280, "height": 410}
    for x in range(expected["x"], expected["x"] + expected["width"], 12):
        draw.line((x, expected["y"], min(x + 7, 435), expected["y"]), fill=(80, 80, 80), width=2)
        draw.line((x, 540, min(x + 7, 435), 540), fill=(80, 80, 80), width=2)
    for y in range(expected["y"], expected["y"] + expected["height"], 12):
        draw.line((expected["x"], y, expected["x"], min(y + 7, 540)), fill=(80, 80, 80), width=2)
        draw.line((435, y, 435, min(y + 7, 540)), fill=(80, 80, 80), width=2)
    image.save(image_path)

    refined = refine_artwork_area(
        image_path, {"x": 145, "y": 145, "width": 305, "height": 380}
    )

    assert abs(refined["x"] - expected["x"]) <= 2
    assert abs(refined["y"] - expected["y"]) <= 2
    assert abs(refined["x"] + refined["width"] - 435) <= 2
    assert abs(refined["y"] + refined["height"] - 540) <= 2


def test_edge_refinement_prefers_inner_artwork_boundary_over_outer_frame(tmp_path: Path):
    image_path = tmp_path / "framed-opening.png"
    image = Image.new("RGB", (700, 700), (244, 238, 226))
    draw = ImageDraw.Draw(image)
    draw.rectangle((125, 105, 575, 600), outline=(35, 35, 35), width=5)
    expected = {"x": 195, "y": 185, "width": 310, "height": 335}
    draw.rectangle((195, 185, 505, 520), outline=(85, 85, 85), width=2)
    image.save(image_path)

    refined = refine_artwork_area(
        image_path, {"x": 125, "y": 105, "width": 450, "height": 495}
    )

    assert abs(refined["x"] - expected["x"]) <= 2
    assert abs(refined["y"] - expected["y"]) <= 2
    assert abs(refined["width"] - expected["width"]) <= 3
    assert abs(refined["height"] - expected["height"]) <= 3


def test_classic_detection_uses_visible_inner_boundary_without_ai(tmp_path: Path):
    image_path = tmp_path / "classic-opening.png"
    image = Image.new("RGB", (700, 700), (244, 238, 226))
    draw = ImageDraw.Draw(image)
    draw.rectangle((125, 105, 575, 600), outline=(35, 35, 35), width=5)
    draw.rectangle((195, 185, 505, 520), outline=(85, 85, 85), width=2)
    image.save(image_path)

    proposal = ClassicDetectionProvider().detect(image_path)

    # Geometric edges snap to the opening, so the proposal sits on the inner
    # stroke rather than a few pixels inside it, which would show as a rim.
    assert abs(proposal.artwork_area["x"] - 196) <= 3
    assert abs(proposal.artwork_area["y"] - 186) <= 3
    assert proposal.provider == "classic"
    assert proposal.raw_artwork_area["mode"] == "geometry"
    # One frame in the image means the layer picker, not the multi-frame path.
    assert "regions" not in proposal.raw_artwork_area


def test_classic_green_frames_mode_detects_skewed_green_mockup_region(tmp_path: Path):
    image_path = tmp_path / "green-frame.png"
    image = Image.new("RGB", (500, 400), (238, 229, 214))
    draw = ImageDraw.Draw(image)
    expected = [(120, 80), (390, 105), (360, 310), (95, 280)]
    draw.polygon(expected, fill=(0, 255, 0))
    image.save(image_path)

    proposal = ClassicDetectionProvider().detect(image_path, mode="green_frames_mockups")

    assert proposal.provider == "classic"
    assert "green frame" in proposal.reason.lower()
    assert proposal.raw_artwork_area["mode"] == "green_frames_mockups"
    corners = proposal.artwork_area["corners"]
    assert len(corners) == 4
    for actual, (exp_x, exp_y) in zip(corners, expected):
        assert abs(actual["x"] - exp_x) <= 4
        assert abs(actual["y"] - exp_y) <= 4
    assert proposal.raw_artwork_area["green_pixels"] > 1000


def test_classic_green_frames_mode_rejects_images_without_green_region(tmp_path: Path):
    image_path = tmp_path / "no-green.png"
    Image.new("RGB", (300, 300), (238, 229, 214)).save(image_path)

    with pytest.raises(DetectionError):
        ClassicDetectionProvider().detect(image_path, mode="green_frames_mockups")


def _draw_blank_frame(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
    """A wall frame: dark border, lighter mat, blank white opening."""
    left, top, right, bottom = box
    draw.rectangle(box, fill=(255, 255, 255), outline=(60, 48, 38), width=6)
    draw.rectangle((left + 10, top + 10, right - 10, bottom - 10), outline=(150, 140, 130), width=2)


def test_classic_auto_detection_returns_every_frame_of_a_multi_frame_mockup(tmp_path: Path):
    image_path = tmp_path / "gallery-wall.png"
    image = Image.new("RGB", (900, 700), (232, 226, 216))
    draw = ImageDraw.Draw(image)
    boxes = [(80, 90, 320, 430), (350, 90, 590, 430), (620, 90, 860, 430)]
    for box in boxes:
        _draw_blank_frame(draw, box)
    image.save(image_path)

    proposal = ClassicDetectionProvider().detect(image_path, mode="geometry")

    regions = proposal.raw_artwork_area["regions"]
    assert len(regions) == len(boxes)
    # Regions come back ordered top-to-bottom then left-to-right.
    for region, (left, top, right, bottom) in zip(regions, boxes):
        assert abs(region["x"] - left) <= 16
        assert abs(region["y"] - top) <= 16
        assert abs(region["width"] - (right - left)) <= 32
        assert abs(region["height"] - (bottom - top)) <= 32
        assert len(region["corners"]) == 4
    assert "3 artwork frames" in proposal.reason


def test_build_provider_passes_green_frames_default_to_classic_provider():
    provider = build_provider(
        {
            "DETECTION_PROVIDER": "classic",
            "CLASSIC_INTERNAL_MODE": "green_frames_mockups",
            "CLASSIC_GREEN_EDGE_EXPAND": "4",
        },
        {},
    )

    assert isinstance(provider, ClassicDetectionProvider)
    assert provider.default_mode == "green_frames_mockups"
    assert provider.green_edge_expand == 4


def test_classic_green_frame_mask_expansion_fills_beyond_raw_green_edges(tmp_path: Path):
    image_path = tmp_path / "green-mask.png"
    image = Image.new("RGB", (80, 80), (238, 229, 214))
    draw = ImageDraw.Draw(image)
    draw.rectangle((25, 25, 54, 54), fill=(0, 255, 0))
    image.save(image_path)

    mask = ClassicDetectionProvider(default_mode="green_frames_mockups", green_edge_expand=3).build_green_frame_mask(image_path)

    assert mask.getpixel((25, 25)) == 255
    assert mask.getpixel((22, 25)) == 255
    assert mask.getpixel((21, 25)) == 0


def test_classic_green_frames_mode_reports_all_detected_regions(tmp_path: Path):
    image_path = tmp_path / "multi-green-frame.png"
    image = Image.new("RGB", (220, 120), (238, 229, 214))
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 80, 90), fill=(0, 255, 0))
    draw.rectangle((130, 25, 190, 95), fill=(0, 255, 0))
    image.save(image_path)

    proposal = ClassicDetectionProvider().detect(image_path, mode="green_frames_mockups")

    regions = proposal.raw_artwork_area["regions"]
    assert len(regions) == 2
    assert all(len(region["corners"]) == 4 for region in regions)


def test_local_model_discovery_uses_reported_models_instead_of_fixed_options(monkeypatch):
    calls: list[str] = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"id": "qwen-vl-installed"}, {"id": "llava-installed"}]}

    def fake_get(url, **_kwargs):
        calls.append(url)
        return Response()

    monkeypatch.setattr("services.local_detection_service.requests.get", fake_get)

    models = discover_local_models("http://localhost:1234/v1/chat/completions")

    assert calls[0] == "http://localhost:1234/v1/models"
    assert [model["id"] for model in models] == ["qwen-vl-installed", "llava-installed"]


def test_vertex_model_discovery_lists_live_vision_compatible_models_only():
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "publisherModels": [
                    {"name": "publishers/google/models/gemini-3.5-flash", "launchStage": "GA"},
                    {"name": "publishers/google/models/gemini-3.1-pro-preview", "launchStage": "PUBLIC_PREVIEW"},
                    {"name": "publishers/google/models/gemini-3.1-flash-image-preview", "launchStage": "PUBLIC_PREVIEW"},
                    {"name": "publishers/google/models/gemini-2.5-flash-tts", "launchStage": "GA"},
                    {"name": "publishers/google/models/gemini-embedding-2", "launchStage": "GA"},
                ]
            }

    class Session:
        def get(self, _url, timeout=30):
            return Response()

    models = list_vertex_detection_models(session=Session())

    assert [model["id"] for model in models] == [
        "gemini-3.5-flash",
        "gemini-3.1-pro-preview",
    ]


def test_vertex_provider_handles_skewed_corners_response(tmp_path: Path):
    background = tmp_path / "background.png"
    Image.new("RGB", (300, 400), (250, 245, 238)).save(background)
    
    class FakeCornersModels:
        def generate_content(self, **kwargs):
            payload = {
                "corners": [
                    {"x": 100, "y": 100},
                    {"x": 900, "y": 150},
                    {"x": 800, "y": 800},
                    {"x": 200, "y": 750}
                ],
                "label": "inner picture opening in perspective",
            }
            return type("Response", (), {"text": json.dumps([payload])})()

    class FakeCornersClient:
        def __init__(self):
            self.models = FakeCornersModels()

    client = FakeCornersClient()
    provider = VertexDetectionProvider(
        project_id="vertextai-project-497513",
        location="global",
        model="gemini-2.5-flash",
        client=client,
        refine=False,
    )

    proposal = provider.detect(background)

    assert proposal.artwork_area["x"] == 30
    assert proposal.artwork_area["y"] == 40
    assert proposal.artwork_area["width"] == 240
    assert proposal.artwork_area["height"] == 280
    
    corners = proposal.artwork_area["corners"]
    assert len(corners) == 4
    assert corners[0] == {"x": 30, "y": 40}
    assert corners[1] == {"x": 270, "y": 60}
    assert corners[2] == {"x": 240, "y": 320}
    assert corners[3] == {"x": 60, "y": 300}
    
    assert proposal.provider == "vertex"
    assert "perspective" in proposal.reason


def test_vertex_provider_refines_skewed_corners(tmp_path: Path, monkeypatch):
    background = tmp_path / "background.png"
    Image.new("RGB", (300, 400), (250, 245, 238)).save(background)
    
    class FakeCornersModels:
        def generate_content(self, **kwargs):
            payload = {
                "corners": [
                    {"x": 100, "y": 100},
                    {"x": 900, "y": 150},
                    {"x": 800, "y": 800},
                    {"x": 200, "y": 750}
                ],
                "label": "inner picture opening in perspective",
            }
            return type("Response", (), {"text": json.dumps([payload])})()

    class FakeCornersClient:
        def __init__(self):
            self.models = FakeCornersModels()

    monkeypatch.setattr(
        "services.vertex_detection_service.refine_perspective_corners",
        lambda _path, corners, **_kwargs: [
            {"x": corners[0]["x"] - 5, "y": corners[0]["y"] - 5},
            corners[1],
            corners[2],
            corners[3]
        ]
    )

    client = FakeCornersClient()
    provider = VertexDetectionProvider(
        project_id="vertextai-project-497513",
        location="global",
        model="gemini-2.5-flash",
        client=client,
        refine=True,
    )

    proposal = provider.detect(background)
    
    corners = proposal.artwork_area["corners"]
    assert corners[0] == {"x": 25, "y": 35}
    assert corners[1] == {"x": 270, "y": 60}
    assert corners[2] == {"x": 240, "y": 320}
    assert corners[3] == {"x": 60, "y": 300}
    
    assert proposal.artwork_area["x"] == 25
    assert proposal.artwork_area["y"] == 35
    assert proposal.artwork_area["width"] == 245
    assert proposal.artwork_area["height"] == 285
    
    assert "snapped to visible edges" in proposal.reason


def test_refine_perspective_corners_prefers_inner_opening_and_rejects_boundary(tmp_path: Path):
    from services.frame_refinement_service import refine_perspective_corners
    
    image_path = tmp_path / "double-edge.png"
    # Create an image representing a double edge:
    # Outer high-contrast edge at y = 100 (gradient 255)
    # Inner lower-contrast edge at y = 113 (gradient 120)
    image = Image.new("RGB", (300, 300), (244, 238, 226))
    draw = ImageDraw.Draw(image)
    
    # Draw outer horizontal high-contrast wood border
    draw.line((0, 100, 300, 100), fill=(20, 20, 20), width=4)
    # Draw inner horizontal lower-contrast opening boundary
    draw.line((0, 113, 300, 113), fill=(100, 100, 100), width=2)
    image.save(image_path)
    
    # Case 1: Start at y = 113 (inner opening). Search radius = 10.
    # The search range is y = 103 to 123.
    # The outer edge is at 100, which is outside the range.
    # The inner edge at 113 is inside the range and should snap cleanly.
    corners_1 = [{"x": 150, "y": 113}]
    refined_1 = refine_perspective_corners(image_path, corners_1, search_radius=10)
    assert abs(refined_1[0]["y"] - 113) <= 1 # Stays at inner opening!
    
    # Case 2: Start at y = 113 (inner opening). Search radius = 15.
    # The search range is y = 98 to 128.
    # Both the outer edge (y = 100) and inner edge (y = 113) are inside the window.
    # Since Top-Left corner (index 0) prefers larger Y coordinate (inner opening),
    # the snapper must prefer the inner edge (113) over the outer edge (100)
    # despite the outer edge having much higher contrast!
    corners_2 = [{"x": 150, "y": 113}]
    refined_2 = refine_perspective_corners(image_path, corners_2, search_radius=15)
    assert abs(refined_2[0]["y"] - 113) <= 1 # Perfectly prefers the inner opening over the outer frame!
    
    # Case 3: Start at y = 125. Search radius = 10.
    # The search range is y = 115 to 135.
    # The inner edge at 113 is outside the range but its gradient bleeds to 115 (boundary).
    # Since the peak is outside the window, it gets pulled to the boundary 115 (shift = 10).
    # The boundary safety gate must reject this shift and reset to the raw coordinates.
    corners_3 = [{"x": 150, "y": 125}]
    refined_3 = refine_perspective_corners(image_path, corners_3, search_radius=10)
    assert refined_3[0]["y"] == 125 # Safety gate falls back to raw coordinate!


# ─── Uniform-region detection tests ──────────────────────────────────────────

def test_uniform_region_detection_finds_white_screen_on_dark_bezel(tmp_path: Path):
    """
    A phone-style mockup: dark charcoal bezel surrounding a flat white screen.
    _detect_uniform_region_pil must find the screen area even when there is no
    explicit border drawn around it — just a stark colour contrast.

    Tolerances are intentionally based on overlap rather than exact coordinates:
    the function works at 80x80 internal resolution (step=4), so each grid step
    equals ~30 px at 600x900 scale. What matters is that a region is found that
    meaningfully overlaps the actual screen area.
    """
    from services.frame_refinement_service import _detect_uniform_region_pil

    image = Image.new("RGB", (600, 900), (45, 45, 45))   # Dark bezel
    draw = ImageDraw.Draw(image)
    screen_x1, screen_y1, screen_x2, screen_y2 = 80, 140, 520, 760
    draw.rectangle((screen_x1, screen_y1, screen_x2, screen_y2), fill=(245, 245, 245))

    result = _detect_uniform_region_pil(image)

    assert result is not None, "Should detect the uniform white screen on a dark bezel"
    rx2 = result["x"] + result["width"]
    ry2 = result["y"] + result["height"]
    assert result["x"] < screen_x2 and rx2 > screen_x1, "Detected region must overlap screen horizontally"
    assert result["y"] < screen_y2 and ry2 > screen_y1, "Detected region must overlap screen vertically"
    # Overlap must cover at least 25 % of the actual screen
    overlap_w = min(rx2, screen_x2) - max(result["x"], screen_x1)
    overlap_h = min(ry2, screen_y2) - max(result["y"], screen_y1)
    screen_area = (screen_x2 - screen_x1) * (screen_y2 - screen_y1)
    assert overlap_w * overlap_h >= 0.25 * screen_area, "Overlap with screen must be at least 25 %"


def test_uniform_region_detection_returns_none_for_single_colour_image():
    """
    A completely flat single-colour image has no distinct uniform region —
    _detect_uniform_region_pil must return None to avoid false positives.
    """
    from services.frame_refinement_service import _detect_uniform_region_pil

    image = Image.new("RGB", (600, 600), (200, 200, 200))  # Uniform grey
    assert _detect_uniform_region_pil(image) is None


def test_global_frame_detect_uniformity_bonus_preserves_correct_frame_detection(tmp_path: Path):
    """
    Regression guard: the 20 % uniformity bonus added to _global_frame_detect
    must not cause it to ignore a real frame with strong edges.  Uses the same
    double-border image already exercised by the classic detection test.
    """
    from services.frame_refinement_service import _global_frame_detect

    image = Image.new("RGB", (700, 700), (244, 238, 226))
    draw = ImageDraw.Draw(image)
    draw.rectangle((125, 105, 575, 600), outline=(35, 35, 35), width=5)   # outer frame
    draw.rectangle((195, 185, 505, 520), fill=(250, 250, 250))             # white placeholder

    result = _global_frame_detect(image)

    assert result is not None, "_global_frame_detect should detect a valid frame"
    # The outer frame edges dominate; the detected box should cover the inner area
    assert 80 <= result["x"] <= 260, f"x={result['x']} outside expected range"
    assert 60 <= result["y"] <= 260, f"y={result['y']} outside expected range"
    assert result["width"] > 200, "Frame width should be substantial"
    assert result["height"] > 200, "Frame height should be substantial"


def test_vertex_provider_detects_multiple_frames_and_builds_mask(tmp_path: Path):
    background = tmp_path / "background.png"
    Image.new("RGB", (1000, 1000), (240, 240, 240)).save(background)

    class FakeMultiFrameModels:
        def generate_content(self, **kwargs):
            payload = [
                {
                    "corners": [
                        {"x": 100, "y": 200},
                        {"x": 450, "y": 200},
                        {"x": 450, "y": 800},
                        {"x": 100, "y": 800},
                    ],
                    "label": "Left Frame 1",
                },
                {
                    "corners": [
                        {"x": 550, "y": 200},
                        {"x": 900, "y": 200},
                        {"x": 900, "y": 800},
                        {"x": 550, "y": 800},
                    ],
                    "label": "Right Frame 2",
                },
            ]
            return type("Response", (), {"text": json.dumps(payload)})()

    class FakeMultiClient:
        def __init__(self):
            self.models = FakeMultiFrameModels()

    client = FakeMultiClient()
    provider = VertexDetectionProvider(
        project_id="vertextai-project-497513",
        location="global",
        model="gemini-2.5-flash",
        client=client,
        refine=False,
    )

    proposal = provider.detect(background)

    assert proposal.confidence == 0.95
    assert "Detected 2 artwork frames" in proposal.reason
    raw = proposal.raw_artwork_area
    assert raw is not None
    assert raw["mode"] == "green_frames_mockups"
    assert raw["frame_count"] == 2
    assert len(raw["regions"]) == 2

    # Frame 1
    assert raw["regions"][0]["x"] == 100
    assert raw["regions"][0]["y"] == 200
    assert raw["regions"][0]["width"] == 350
    assert raw["regions"][0]["height"] == 600

    # Frame 2
    assert raw["regions"][1]["x"] == 550
    assert raw["regions"][1]["y"] == 200
    assert raw["regions"][1]["width"] == 350
    assert raw["regions"][1]["height"] == 600

    # Test mask generation
    mask = provider.build_green_frame_mask(background, regions=raw["regions"])
    assert mask.size == (1000, 1000)
    # Check pixels inside frame 1 & frame 2 are 255 (white)
    assert mask.getpixel((200, 400)) == 255
    assert mask.getpixel((700, 400)) == 255
    # Check pixel between frames is 0 (black)
    assert mask.getpixel((500, 400)) == 0


def test_a_single_frame_mockup_is_not_split_by_incidental_rectangles(tmp_path: Path):
    """One artwork frame beside windows and panels stays one frame.

    Room scenes are full of flat rectangles -- window panes, door panels, a
    skirting board. They pass the same flatness test as a placeholder, so size
    relative to the real frame is what separates them.
    """
    image_path = tmp_path / "room.png"
    image = Image.new("RGB", (900, 700), (232, 226, 216))
    draw = ImageDraw.Draw(image)

    # The artwork frame: a large bordered opening filled with chroma green.
    draw.rectangle((150, 90, 560, 600), fill=(60, 48, 38))
    draw.rectangle((162, 102, 548, 588), fill=(0, 255, 0))
    # Incidental geometry: two small flat panes in a window frame.
    for top in (120, 330):
        draw.rectangle((700, top, 820, top + 170), fill=(80, 80, 80))
        draw.rectangle((708, top + 8, 812, top + 162), fill=(236, 240, 244))
    image.save(image_path)

    proposal = ClassicDetectionProvider().detect(image_path, mode="geometry")

    assert "regions" not in proposal.raw_artwork_area
    area = proposal.artwork_area
    assert abs(area["x"] - 162) <= 8
    assert abs(area["y"] - 102) <= 8
    assert abs(area["width"] - 386) <= 16
    assert abs(area["height"] - 486) <= 16


def test_flat_colour_placeholders_are_found_without_a_border_ring(tmp_path: Path):
    """A chroma fill sitting flush on the wall is still an artwork area.

    Only some mockups draw a frame around the placeholder. Where none is drawn
    there is no nesting ring to detect, and the fill is far too saturated to
    pass as a neutral stand-in -- but it is dead flat, and nothing in a room is.
    """
    image_path = tmp_path / "flush.png"
    image = Image.new("RGB", (900, 700), (232, 226, 216))
    draw = ImageDraw.Draw(image)
    # Three borderless chroma panels of noticeably different sizes.
    draw.rectangle((90, 90, 330, 560), fill=(0, 255, 0))
    draw.rectangle((380, 150, 560, 500), fill=(0, 255, 0))
    draw.rectangle((610, 210, 740, 440), fill=(0, 255, 0))
    image.save(image_path)

    proposal = ClassicDetectionProvider().detect(image_path, mode="geometry")

    regions = proposal.raw_artwork_area.get("regions") or []
    assert len(regions) == 3, f"expected all three panels, got {len(regions)}"
    for region, (left, top, right, bottom) in zip(
        regions, [(90, 90, 330, 560), (380, 150, 560, 500), (610, 210, 740, 440)]
    ):
        assert abs(region["x"] - left) <= 6
        assert abs(region["y"] - top) <= 6
        assert abs(region["width"] - (right - left)) <= 12
        assert abs(region["height"] - (bottom - top)) <= 12


def _quad_angles(corners: dict[str, dict[str, float]]) -> tuple[float, float]:
    import math

    top = math.degrees(
        math.atan2(corners["tr"]["y"] - corners["tl"]["y"], corners["tr"]["x"] - corners["tl"]["x"])
    )
    bottom = math.degrees(
        math.atan2(corners["br"]["y"] - corners["bl"]["y"], corners["br"]["x"] - corners["bl"]["x"])
    )
    return top, bottom


def test_frame_of_a_rounded_opening_follows_its_edges_instead_of_tilting():
    """A phone screen is a rounded rectangle, and its frame must sit square on it.

    Simplifying the rounded outline to four sides can land them on the corner
    arcs, a few degrees off the real edges. Pushed out far enough to contain
    the opening, that tilted shape wastes area -- and the artwork drawn on it
    comes out visibly rotated inside the screen.
    """
    import numpy as np
    from services.green_frame_mockup_service import GreenRegion, _region_quad

    opening = Image.new("L", (300, 500), 0)
    ImageDraw.Draw(opening).rounded_rectangle((70, 90, 230, 430), radius=48, fill=255)
    mask = np.asarray(opening) > 127
    region = GreenRegion(70, 90, 161, 341, int(mask.sum()))

    corners = _region_quad(mask, region)

    assert corners is not None
    top, bottom = _quad_angles(corners)
    assert abs(top) < 1.0 and abs(bottom) < 1.0, "the frame is tilted off the screen's edges"
    width = corners["tr"]["x"] - corners["tl"]["x"]
    height = corners["bl"]["y"] - corners["tl"]["y"]
    assert width * height < mask.sum() * 1.05, "the frame wastes more than the rounded corners"


def test_frame_of_a_slanted_opening_keeps_its_slant():
    """The tighter shape wins, and for a frame seen at an angle that is its own
    quad -- squaring it off would waste a corner of the mask on every side."""
    import numpy as np
    from services.green_frame_mockup_service import GreenRegion, _region_quad

    opening = Image.new("L", (500, 400), 0)
    ImageDraw.Draw(opening).polygon([(120, 80), (390, 105), (360, 310), (95, 280)], fill=255)
    mask = np.asarray(opening) > 127
    region = GreenRegion(95, 80, 296, 231, int(mask.sum()))

    corners = _region_quad(mask, region)

    assert corners is not None
    for key, (x, y) in zip(("tl", "tr", "br", "bl"), [(120, 80), (390, 105), (360, 310), (95, 280)]):
        assert abs(corners[key]["x"] - x) <= 4 and abs(corners[key]["y"] - y) <= 4


def test_frame_points_does_not_leak_through_a_gap_in_a_hard_bezel(tmp_path: Path):
    """A seed-point fill stops at a hard edge.

    The fill compares each pixel with its neighbour, which is what lets it
    follow shading inside an opening -- and also what let it slip through a
    soft spot in a frame's bezel and swallow the wall behind it. A pixel that
    sits on a hard edge now blocks the fill, so the opening stays the opening.
    """
    import numpy as np
    from services.green_frame_mockup_service import (
        GreenFrameSettings, detect_frames_from_points, green_detection_raw)

    opening = (60, 60, 180, 260)
    canvas = np.full((320, 240, 3), 246, np.uint8)              # wall, near the opening's own colour
    canvas[50:270, 50:190] = 60                                 # the frame's hard bezel
    canvas[opening[1]:opening[3], opening[0]:opening[2]] = 250   # the blank opening
    # A soft spot in the bezel: a couple of rows where it fades to the wall.
    canvas[150:152, 46:60] = np.linspace(250, 246, 14, dtype=np.uint8)[None, :, None]
    image = Image.fromarray(canvas, "RGB").convert("RGBA")

    state = detect_frames_from_points(
        image, [{"x": 120, "y": 150}], 20, GreenFrameSettings(tolerance=20, min_area=80)
    )

    assert len(state.regions) == 1
    region = green_detection_raw(state, 0)["regions"][0]
    assert region["width"] <= (opening[2] - opening[0]) + 12, "the fill escaped through the bezel"
    assert region["x"] >= opening[0] - 12, "the fill escaped through the bezel"


def test_frame_points_keeps_the_angle_of_a_frame_that_is_not_level(tmp_path: Path):
    """A mockup shot at a slight angle keeps that angle.

    Snapping the region's bounding box to the frame's edges squares it off, so
    a frame a couple of degrees off level came back level -- and the artwork
    drawn in it sat crooked against the frame around it. Where a side of the
    fill is a straight line, the region is trimmed back to it.
    """
    import math
    import numpy as np
    import cv2
    from services.green_frame_mockup_service import (
        GreenFrameSettings, detect_frames_from_points, green_detection_raw)

    tilt = 3.0
    canvas = np.full((420, 520, 3), 236, np.uint8)                     # wall
    corners = np.array([[90, 90], [430, 90], [430, 330], [90, 330]], np.float32)
    centre = corners.mean(axis=0)
    rotation = cv2.getRotationMatrix2D(tuple(centre), tilt, 1.0)
    tilted = cv2.transform(corners[None], rotation)[0]
    cv2.fillConvexPoly(canvas, np.round(tilted).astype(np.int32), (70, 62, 55))   # the frame
    inner = centre + (tilted - centre) * 0.9
    cv2.fillConvexPoly(canvas, np.round(inner).astype(np.int32), (250, 250, 250))  # the opening
    image = Image.fromarray(canvas, "RGB").convert("RGBA")

    state = detect_frames_from_points(
        image, [{"x": int(centre[0]), "y": int(centre[1])}], 20,
        GreenFrameSettings(tolerance=20, min_area=80),
    )

    assert len(state.regions) == 1
    frame = green_detection_raw(state, 0)["regions"][0]["corners"]
    top = math.degrees(math.atan2(frame[1]["y"] - frame[0]["y"], frame[1]["x"] - frame[0]["x"]))
    bottom = math.degrees(math.atan2(frame[2]["y"] - frame[3]["y"], frame[2]["x"] - frame[3]["x"]))
    assert abs(top + tilt) <= 1.0, f"the top edge came back at {top:+.2f} degrees, not {-tilt:+.2f}"
    assert abs(bottom + tilt) <= 1.0, f"the bottom edge came back at {bottom:+.2f} degrees"


def test_green_detection_state_keeps_its_masks_as_bits():
    """A detection is cached per template, and every mask in it is canvas-sized.

    Five full-canvas arrays came to 17MB an entry and 208MB for a full cache at
    1254x1254 -- close to five gigabytes at 6000x6000. The alpha channel among
    them was written and never read again, and numpy spends a whole byte on a
    bool, so the three yes/no masks are kept as bits. Nothing about the result
    changes: packbits round-trips exactly, and this test is what says so.
    """
    import numpy as np

    from services.green_frame_mockup_service import GreenFrameDetection

    width, height = 37, 23  # deliberately not a multiple of 8
    rng = np.random.default_rng(7)
    raw = rng.random((height, width)) > 0.5
    detect = rng.random((height, width)) > 0.5
    clip = rng.random((height, width)) > 0.5
    soft = rng.random((height, width)).astype(np.float32)

    state = GreenFrameDetection(width, height, [], raw, detect, clip, soft, int(raw.sum()))

    # What the callers read is what was put in, bit for bit.
    for got, expected in ((state.raw_mask, raw), (state.detect_mask, detect), (state.clip_mask, clip)):
        assert got.dtype == np.dtype(bool)
        assert got.shape == (height, width)
        assert np.array_equal(got, expected)
    assert np.array_equal(state.soft_mask, soft)

    # A byte per eight pixels, not one per pixel.
    packed = state._raw_bits.nbytes + state._detect_bits.nbytes + state._clip_bits.nbytes
    assert packed <= (raw.nbytes + detect.nbytes + clip.nbytes) / 7

    # The channel that was never read is not carried at all.
    assert not hasattr(state, "green_alpha_mask")


def test_reshape_opening_grows_per_side_and_stays_inside_the_drawn_frame():
    """Two things the detected green cannot say on its own.

    A screen photographed at an angle leaves a sliver of green along one edge
    that the artwork has to cover -- that is what the per-side amounts are for.
    And a frame the user has dragged in is a frame they want smaller, so the
    opening is held inside the frames as they now stand: before this, editing a
    frame on a green mockup moved the artwork and left the opening where the
    green was, which read as the edit doing nothing.
    """
    import numpy as np

    from services.green_frame_mockup_service import (
        GreenFrameDetection,
        GreenFrameSettings,
        reshape_opening,
    )

    canvas = (40, 40)
    green = np.zeros(canvas, dtype=bool)
    green[10:20, 10:20] = True  # the detected opening
    soft = green.astype(np.float32)
    state = GreenFrameDetection(40, 40, [], green, green, green, soft, int(green.sum()))

    settings = GreenFrameSettings(mask_expand_top=3, mask_expand_right=5)
    reshape_opening(state, settings, None)

    opening = state.clip_mask
    ys, xs = np.where(opening)
    assert ys.min() == 7 and xs.max() == 24  # three up, five to the right
    assert ys.max() == 19 and xs.min() == 10  # the other two sides stay put
    # What the opening gained is fully open, not half-feathered.
    assert state.soft_mask[8, 15] == 1.0

    # A frame pulled in cuts the opening down to it.
    frame = np.zeros(canvas, dtype=bool)
    frame[10:20, 10:15] = True
    state = GreenFrameDetection(40, 40, [], green, green, green, soft, int(green.sum()))
    reshape_opening(state, GreenFrameSettings(), frame)
    assert state.clip_mask.sum() == frame.sum()
    assert not state.clip_mask[15, 17]
    assert state.soft_mask[15, 17] == 0.0

    # Nothing asked for, nothing changed.
    state = GreenFrameDetection(40, 40, [], green, green, green, soft, int(green.sum()))
    reshape_opening(state, GreenFrameSettings(), None)
    assert np.array_equal(state.clip_mask, green)


def test_green_settings_carry_the_new_controls_into_the_render():
    """The panel's numbers have to reach the render, and the cache has to know."""
    from pathlib import Path

    from services.green_frame_mockup_service import parse_green_frame_settings

    settings = parse_green_frame_settings(
        {
            "green_frame_mockups": {
                "tolerance": 140,
                "mask_expand_left": 4,
                "mask_expand_right": -3,
                "mask_expand_top": 999,
                "mask_expand_bottom": 7,
            }
        }
    )
    assert settings.tolerance == 140
    # The panel reaches as far as the colour space does: 255*sqrt(3) = 441.67,
    # the longest distance between two colours in RGB. Past that every pixel in
    # the picture already scores as green, so there is nothing further to allow.
    assert parse_green_frame_settings({"green_frame_mockups": {"tolerance": 442}}).tolerance == 442
    assert parse_green_frame_settings({"green_frame_mockups": {"tolerance": 900}}).tolerance == 442
    assert settings.mask_expand_left == 4
    assert settings.mask_expand_right == -3
    assert settings.mask_expand_top == 150  # clamped
    assert settings.mask_expand_bottom == 7

    # A detection cached under one set of amounts must not answer for another.
    source = Path("services/simple_mockup_service.py").read_text(encoding="utf-8")
    key = source.split("def _green_detection_cache_key(", 1)[1].split(chr(10) + "def ", 1)[0]
    for field in ("mask_expand_left", "mask_expand_right", "mask_expand_top", "mask_expand_bottom"):
        assert f"settings.{field}" in key, field


def test_a_tolerance_that_counts_everything_falls_back_to_the_recorded_shape():
    """Wide open is the default, and wide open has to stay safe.

    At 442 every pixel in any picture scores as green, which is exactly what
    makes it a good default -- the opening is then decided by the frames as
    drawn rather than by how well the green survived the photograph. But a
    detection that covers the whole canvas says nothing about where the opening
    is, so the render reads the recorded shape instead: the mask beside the
    template, or failing that the frames. Without that, an opening whose shape
    the frames cannot describe -- an ellipse, a frame behind a plant -- would
    be squared off.
    """
    from pathlib import Path

    from services.green_frame_mockup_service import parse_green_frame_settings

    # The panel ships wide open.
    assert parse_green_frame_settings({}).tolerance == 442
    assert parse_green_frame_settings({"green_frame_mockups": {"tolerance": 120}}).tolerance == 120

    source = Path("services/simple_mockup_service.py").read_text(encoding="utf-8")
    render = source.split("def _render_green_frame_mockup(", 1)[1]
    assert "undiscriminating = detection.green_count >= 0.9" in render
    assert "if not detection.regions or undiscriminating" in render

    # Detection itself is not run wide open: it is what has to tell green from
    # everything else in the first place. It has its own tolerance, set apart
    # from the render's and editable in the admin's engine settings.
    import config

    assert 10 <= config.Config.CLASSIC_GREEN_TOLERANCE < 442
    classic = Path("services/classic_detection_service.py").read_text(encoding="utf-8")
    assert "tolerance=self.green_tolerance" in classic
    detection = Path("services/detection_service.py").read_text(encoding="utf-8")
    assert '"CLASSIC_GREEN_TOLERANCE"' in detection
