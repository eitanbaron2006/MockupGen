import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from services.detection_service import DetectionError, validate_proposal
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

    assert abs(proposal.artwork_area["x"] - 195) <= 3
    assert abs(proposal.artwork_area["y"] - 185) <= 3
    assert proposal.provider == "classic"


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
