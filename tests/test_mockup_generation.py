import sys
from pathlib import Path

from PIL import Image, ImageDraw

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from test_mockup_api import build_client  # noqa: E402


def room_with_green(frames: int = 1, *, shadow: bool = False, size=(900, 600)) -> Image.Image:
    """A stand-in for what the model returns: a wall with flat green openings."""
    image = Image.new("RGB", size, (214, 209, 200))
    draw = ImageDraw.Draw(image)
    width = 260
    gap = 40
    total = frames * width + (frames - 1) * gap
    left = (size[0] - total) // 2
    for index in range(frames):
        x0 = left + index * (width + gap)
        draw.rectangle((x0 - 12, 118, x0 + width + 12, 482), fill=(92, 68, 46))
        draw.rectangle((x0, 130, x0 + width, 470), fill=(0, 255, 0))
    if shadow:
        pixels = image.load()
        for y in range(130, 470):
            for x in range(left, left + 90):
                red, green, blue = pixels[x, y]
                pixels[x, y] = (red, int(green * 0.55), blue)
    return image


def studio(tmp_path):
    return build_client(tmp_path, ADMIN_PASSWORD="test-admin", SECRET_KEY="test-secret")


def login(client) -> str:
    response = client.post("/api/admin/login", json={"password": "test-admin"})
    assert response.status_code == 200
    return response.get_json()["csrf_token"]


def a_category(client, name="Main Vertical") -> int:
    from flask import current_app

    with client.application.app_context():
        return current_app.extensions["catalog_service"].get_or_create_category(name)["id"]


def enable_vertex(client, monkeypatch, image=None):
    import routes.admin_routes as admin_routes

    monkeypatch.setitem(client.application.config, "ENABLE_AI_MODE", True)
    monkeypatch.setitem(client.application.config, "VERTEX_PROJECT_ID", "test-project")
    seen: dict = {}

    def fake_generate(**kwargs):
        seen.update(kwargs)
        return image if image is not None else room_with_green()

    monkeypatch.setattr(admin_routes, "generate_mockup", fake_generate)
    return seen


def test_a_flat_green_opening_is_measured_and_kept(tmp_path, monkeypatch):
    """A mockup is a room with a readable green in it, and that is checked.

    The model is asked for flat chroma green and cannot be taken at its word,
    so the picture goes through the studio's own detector before it is filed:
    how many openings, how flat each one is, how far from chroma-key it sits.
    """
    client, paths = studio(tmp_path)
    csrf = login(client)
    seen = enable_vertex(client, monkeypatch, room_with_green(2))
    category = a_category(client)

    made = client.post(
        "/api/admin/mockups/generate",
        json={"category_id": category, "scene": "living", "frames": 2, "ratio": "2:3"},
        headers={"X-CSRF-Token": csrf},
    )

    assert made.status_code == 201
    payload = made.get_json()
    assert payload["kept"] is True
    assert payload["report"]["usable"] is True
    assert payload["report"]["found_frames"] == 2
    assert all(frame["flat"] and frame["on_colour"] for frame in payload["report"]["frames"])
    assert payload["image"].startswith("data:image/png;base64,")

    # It lands as an ordinary draft template, indistinguishable from an import.
    template = payload["template"]
    assert template["status"] == "draft"
    drafts = Path(client.application.config["DRAFT_TEMPLATES_FOLDER"])
    assert (drafts / template["template_id"] / "background.png").is_file()
    # ...and the MAIN naming rule applies to it like any other template.
    assert template["name"].startswith("MAIN-")

    # The scene and the green demand both reached the model.
    assert "linen sofa" in seen["prompt"]
    assert "chroma-key green" in seen["prompt"]
    assert "2 empty picture frames" in seen["prompt"]


def test_a_mockup_whose_green_cannot_be_read_is_not_filed_as_one(tmp_path, monkeypatch):
    """A shadow across an opening passes a glance and fails a render.

    It comes back with its numbers instead of being kept, because a template
    the detector cannot read is not a mockup -- but it is still shown, since
    whether to keep it is the admin's decision and not this endpoint's.
    """
    client, _ = studio(tmp_path)
    csrf = login(client)
    enable_vertex(client, monkeypatch, room_with_green(1, shadow=True))
    category = a_category(client, "Vertical Wall Art")

    made = client.post(
        "/api/admin/mockups/generate",
        json={"category_id": category, "scene": "bedroom", "frames": 1},
        headers={"X-CSRF-Token": csrf},
    )

    payload = made.get_json()
    assert payload["kept"] is False
    assert payload["template"] is None
    assert payload["report"]["usable"] is False
    assert any("not flat" in problem for problem in payload["report"]["problems"])
    assert payload["image"].startswith("data:image/png;base64,")

    # ...and kept anyway when the admin says so.
    forced = client.post(
        "/api/admin/mockups/generate",
        json={"category_id": category, "scene": "bedroom", "frames": 1, "force": True},
        headers={"X-CSRF-Token": csrf},
    ).get_json()
    assert forced["kept"] is True
    assert forced["template"]["status"] == "draft"


def test_the_wrong_number_of_openings_is_a_problem_worth_naming(tmp_path, monkeypatch):
    client, _ = studio(tmp_path)
    csrf = login(client)
    enable_vertex(client, monkeypatch, room_with_green(1))
    category = a_category(client, "Vertical Wall Art")

    payload = client.post(
        "/api/admin/mockups/generate",
        json={"category_id": category, "scene": "living", "frames": 3},
        headers={"X-CSRF-Token": csrf},
    ).get_json()

    assert payload["kept"] is False
    assert "asked for 3" in " ".join(payload["report"]["problems"])


def test_generation_is_refused_rather_than_pretended(tmp_path, monkeypatch):
    client, _ = studio(tmp_path)
    csrf = login(client)
    category = a_category(client)

    monkeypatch.setitem(client.application.config, "ENABLE_AI_MODE", False)
    assert client.post(
        "/api/admin/mockups/generate",
        json={"category_id": category},
        headers={"X-CSRF-Token": csrf},
    ).status_code == 503

    enable_vertex(client, monkeypatch)
    assert client.post(
        "/api/admin/mockups/generate",
        json={"scene": "living"},
        headers={"X-CSRF-Token": csrf},
    ).status_code == 400
    assert client.post(
        "/api/admin/mockups/generate",
        json={"category_id": category, "scene": "no-such-room"},
        headers={"X-CSRF-Token": csrf},
    ).status_code == 404


def test_the_rooms_and_the_wording_are_offered_and_remembered(tmp_path, monkeypatch):
    client, _ = studio(tmp_path)
    csrf = login(client)
    seen = enable_vertex(client, monkeypatch)
    category = a_category(client)

    offered = client.get("/api/admin/mockups/scenes").get_json()
    assert [scene["key"] for scene in offered["scenes"]][:2] == ["living", "bedroom"]
    assert offered["enabled"] is True

    client.post(
        "/api/admin/mockups/generate",
        json={"category_id": category, "prompt": "A hallway of my own, {frames} frame.", "frames": 1},
        headers={"X-CSRF-Token": csrf},
    )
    assert seen["prompt"].startswith("A hallway of my own, 1 frame.")
    # The green demand survives any rewrite: without it there is no mockup.
    assert "chroma-key green" in seen["prompt"]
    assert client.get("/api/admin/mockups/scenes").get_json()["prompt"].startswith("A hallway of my own")

    # A room merely tried is not a rewrite of the studio's wording.
    client.post(
        "/api/admin/mockups/generate",
        json={"category_id": category, "scene": "office"},
        headers={"X-CSRF-Token": csrf},
    )
    assert client.get("/api/admin/mockups/scenes").get_json()["prompt"].startswith("A hallway of my own")


def test_the_green_inspector_reads_the_pixels_not_the_promise():
    from services.mockup_generation_service import (
        UNIFORMITY_LIMIT,
        UNIFORMITY_NOTICE,
        inspect_green,
    )

    clean = inspect_green(room_with_green(2), expected_frames=2)
    assert clean["usable"] is True
    assert [frame["flat"] for frame in clean["frames"]] == [True, True]
    assert max(frame["uniformity"] for frame in clean["frames"]) < 1

    shaded = inspect_green(room_with_green(1, shadow=True), expected_frames=1)
    assert shaded["usable"] is False
    assert shaded["frames"][0]["uniformity"] > UNIFORMITY_LIMIT

    # The thresholds were calibrated on real generations, where the model's own
    # compression reads about 15 on a clean opening: a hairline limit rejected
    # pictures that were perfectly usable, so unevenness that still keys is a
    # remark rather than a refusal.
    assert UNIFORMITY_NOTICE < UNIFORMITY_LIMIT
    assert clean["warnings"] == []

    # A room with no green at all is not a mockup, and says so.
    empty = inspect_green(Image.new("RGB", (400, 300), (210, 205, 195)), expected_frames=1)
    assert empty["found_frames"] == 0
    assert empty["usable"] is False
