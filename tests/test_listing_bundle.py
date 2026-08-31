import json
import sys
from pathlib import Path

import pytest
from PIL import Image, features

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from test_mockup_api import build_client, image_bytes  # noqa: E402


def write_room_template(
    templates_folder: Path,
    template_id: str,
    *,
    canvas: tuple[int, int] = (200, 300),
    area: tuple[int, int, int, int] = (40, 60, 80, 120),
    product_type: str = "wall-art",
    orientation: str = "portrait",
) -> Path:
    """A room-sized classic template, big enough that a crop is a real crop."""
    template_folder = templates_folder / template_id
    template_folder.mkdir(parents=True)
    width, height = canvas
    x, y, area_width, area_height = area
    background = Image.new("RGBA", canvas, (210, 205, 195, 255))
    background.save(template_folder / "background.png")
    background.save(template_folder / "preview.png")
    manifest = {
        "template_id": template_id,
        "name": f"Room {template_id}",
        "product_type": product_type,
        "canvas_width": width,
        "canvas_height": height,
        "artwork_area": {"x": x, "y": y, "width": area_width, "height": area_height},
        "fit_mode": "cover",
        "background": "background.png",
        "preview": "preview.png",
        "supported_modes": ["simple"],
        "output_format": "png",
        "orientation": orientation,
    }
    (template_folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return template_folder


def post_bundle(client, spec: dict | None = None, *, size: tuple[int, int] = (400, 600)):
    data: dict = {"artwork": (image_bytes(size, (20, 90, 160, 255)), "art.png")}
    if spec is not None:
        data["spec"] = json.dumps(spec)
    return client.post(
        "/api/mockups/listing-bundle", data=data, content_type="multipart/form-data"
    )


def by_role(payload: dict) -> dict:
    return {item["role"]: item for item in payload["items"]}


def output_image(paths: dict, item: dict) -> Image.Image:
    return Image.open(paths["OUTPUT_FOLDER"] / item["output_url"].rsplit("/", 1)[-1])


def test_bundle_returns_a_full_listing_set(tmp_path):
    client, paths = build_client(tmp_path)
    write_room_template(paths["TEMPLATES_FOLDER"], "room_a")
    write_room_template(paths["TEMPLATES_FOLDER"], "room_b")

    response = post_bundle(client)

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert [item["role"] for item in payload["items"]] == [
        "hero",
        "closeup",
        "scale",
        "size_guide",
    ]
    assert all(item["success"] for item in payload["items"])
    roles = by_role(payload)
    # The second room must be a different room, or the listing shows one
    # picture twice.
    assert roles["scale"]["template_id"] != roles["hero"]["template_id"]
    for item in payload["items"]:
        assert (paths["OUTPUT_FOLDER"] / item["output_url"].rsplit("/", 1)[-1]).is_file()


def test_closeup_is_a_crop_of_the_hero_around_the_frame(tmp_path):
    client, paths = build_client(tmp_path)
    write_room_template(paths["TEMPLATES_FOLDER"], "room_a", area=(40, 60, 80, 120))

    payload = post_bundle(client, {"roles": ["hero", "closeup"]}).get_json()
    roles = by_role(payload)

    hero = output_image(paths, roles["hero"])
    closeup = output_image(paths, roles["closeup"])
    assert hero.size == (200, 300)
    assert closeup.width < hero.width and closeup.height < hero.height
    crop = roles["closeup"]["crop"]
    # The whole frame survives the crop, with scene left around it.
    assert crop["x"] < 40 and crop["y"] < 60
    assert crop["x"] + crop["width"] > 120
    assert crop["y"] + crop["height"] > 180
    assert (closeup.width, closeup.height) == (crop["width"], crop["height"])


def test_closeup_reuses_the_hero_render_instead_of_rendering_twice(tmp_path):
    client, paths = build_client(tmp_path)
    write_room_template(paths["TEMPLATES_FOLDER"], "room_a")

    payload = post_bundle(client, {"roles": ["hero", "closeup"]}).get_json()

    # One render on disk plus the cropped file it produced -- not two renders.
    assert len(list(paths["OUTPUT_FOLDER"].glob("mockup_*"))) == 2
    roles = by_role(payload)
    assert roles["closeup"]["template_id"] == roles["hero"]["template_id"]


def test_size_guide_picks_the_family_the_artwork_actually_fits(tmp_path):
    client, paths = build_client(tmp_path)
    write_room_template(paths["TEMPLATES_FOLDER"], "room_a")

    portrait = by_role(post_bundle(client, {"roles": ["size_guide"]}, size=(400, 600)).get_json())
    assert portrait["size_guide"]["size_family"] == "2:3"
    assert portrait["size_guide"]["unit"] == "in"
    assert [size["label"] for size in portrait["size_guide"]["sizes"]][:2] == ["4x6", "8x12"]

    square = by_role(post_bundle(client, {"roles": ["size_guide"]}, size=(500, 500)).get_json())
    assert square["size_guide"]["size_family"] == "1:1"

    four_by_five = by_role(
        post_bundle(client, {"roles": ["size_guide"]}, size=(800, 1000)).get_json()
    )
    assert four_by_five["size_guide"]["size_family"] == "4:5"


def test_size_guide_turns_sizes_to_match_a_landscape_artwork(tmp_path):
    client, paths = build_client(tmp_path)
    write_room_template(paths["TEMPLATES_FOLDER"], "room_a")

    guide = by_role(
        post_bundle(client, {"roles": ["size_guide"]}, size=(600, 400)).get_json()
    )["size_guide"]

    assert guide["size_family"] == "2:3"
    first = guide["sizes"][0]
    assert (first["width"], first["height"]) == (6, 4)
    assert all(size["width"] > size["height"] for size in guide["sizes"])
    with output_image(paths, guide) as image:
        assert image.size == (2000, 2000)


def test_seller_supplied_sizes_replace_the_standard_family(tmp_path):
    client, paths = build_client(tmp_path)
    write_room_template(paths["TEMPLATES_FOLDER"], "room_a")

    payload = post_bundle(
        client,
        {
            "roles": ["size_guide"],
            "sizes": [
                {"label": "A3", "width": 29.7, "height": 42},
                {"label": "A2", "width": 42, "height": 59.4},
            ],
        },
    ).get_json()

    guide = by_role(payload)["size_guide"]
    assert guide["size_family"] == "custom"
    assert [size["label"] for size in guide["sizes"]] == ["A3", "A2"]


def test_one_failed_role_still_delivers_the_rest(tmp_path):
    client, paths = build_client(tmp_path)
    write_room_template(paths["TEMPLATES_FOLDER"], "room_a")

    response = post_bundle(client, {"templates": {"scale": "no_such_template"}})

    assert response.status_code == 207
    payload = response.get_json()
    assert payload["success"] is False
    roles = by_role(payload)
    assert roles["scale"]["success"] is False
    assert "not found" in roles["scale"]["error"].lower()
    assert all(roles[role]["success"] for role in ("hero", "closeup", "size_guide"))


def test_unknown_role_and_missing_artwork_are_refused(tmp_path):
    client, paths = build_client(tmp_path)
    write_room_template(paths["TEMPLATES_FOLDER"], "room_a")

    unknown = post_bundle(client, {"roles": ["hero", "poster"]})
    assert unknown.status_code == 400
    assert "poster" in unknown.get_json()["error"]

    empty = client.post(
        "/api/mockups/listing-bundle", data={}, content_type="multipart/form-data"
    )
    assert empty.status_code == 400


def test_bundle_reports_no_template_instead_of_failing_the_request(tmp_path):
    client, _ = build_client(tmp_path)

    response = post_bundle(client, {"roles": ["hero", "size_guide"]})

    assert response.status_code == 207
    roles = by_role(response.get_json())
    assert roles["hero"]["success"] is False
    # The chart needs no template, so it is still delivered.
    assert roles["size_guide"]["success"] is True


def test_crop_box_falls_back_to_the_whole_canvas(tmp_path):
    from services.listing_bundle_service import closeup_crop_box

    assert closeup_crop_box([], (400, 300)) == (0, 0, 400, 300)
    tiny = [{"x": 0, "y": 0, "width": 1, "height": 1}]
    assert closeup_crop_box(tiny, (1, 1)) == (0, 0, 1, 1)
    box = closeup_crop_box(
        [
            {"x": 10, "y": 10, "width": 20, "height": 20},
            {"x": 100, "y": 100, "width": 80, "height": 80},
        ],
        (400, 400),
        padding=0.25,
    )
    # The largest frame wins: it carries the most pixels for a close-up.
    assert box == (80, 80, 200, 200)


@pytest.mark.skipif(not features.check("avif"), reason="Pillow build without AVIF")
def test_saved_avif_is_really_avif(tmp_path):
    from services.simple_mockup_service import save_render_image

    name = save_render_image(Image.new("RGBA", (8, 8), (10, 20, 30, 255)), tmp_path, "avif", 80)

    assert name.endswith(".avif")
    with Image.open(tmp_path / name) as saved:
        assert saved.format == "AVIF"


def test_bundle_honours_the_requested_output_format(tmp_path):
    client, paths = build_client(tmp_path)
    write_room_template(paths["TEMPLATES_FOLDER"], "room_a")

    payload = post_bundle(client, {"roles": ["hero", "closeup"], "format": "webp"}).get_json()

    for item in payload["items"]:
        assert item["output_url"].endswith(".webp")
        with Image.open(paths["OUTPUT_FOLDER"] / item["output_url"].rsplit("/", 1)[-1]) as saved:
            assert saved.format == "WEBP"

    rejected = post_bundle(client, {"format": "tiff"})
    assert rejected.status_code == 400


def test_size_guide_image_is_drawn_at_full_resolution(tmp_path):
    client, paths = build_client(tmp_path)
    write_room_template(paths["TEMPLATES_FOLDER"], "room_a")

    guide = by_role(post_bundle(client, {"roles": ["size_guide"]}).get_json())["size_guide"]

    assert (guide["width"], guide["height"]) == (2000, 2000)
    with output_image(paths, guide) as image:
        colours = {pixel for _, pixel in image.convert("RGB").getcolors(maxcolors=200000)}
        # Background, outlines, labels and the ghosted artwork -- not a blank page.
        assert len(colours) > 20


def test_uploaded_artwork_is_not_left_in_the_response(tmp_path):
    client, paths = build_client(tmp_path)
    write_room_template(paths["TEMPLATES_FOLDER"], "room_a")

    payload = post_bundle(client).get_json()

    assert payload["artwork_ratio"] == pytest.approx(400 / 600, rel=1e-3)
    body = json.dumps(payload)
    assert str(paths["UPLOAD_FOLDER"]) not in body
    assert "art.png" not in body
