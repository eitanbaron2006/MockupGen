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
