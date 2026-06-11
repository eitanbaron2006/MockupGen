import json
import sys
from pathlib import Path

from PIL import Image


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from test_mockup_api import (  # noqa: E402
    build_client,
    image_bytes,
    save_image,
    write_template,
)


def write_green_set_template(
    templates_folder: Path,
    template_id: str = "green_set_002",
    *,
    regions: int = 2,
    product_type: str = "wall-art-set",
) -> Path:
    """Green-screen template with N side-by-side frame regions."""
    frame = 8
    gap = 4
    width = gap + regions * (frame + gap)
    height = 16
    template_folder = templates_folder / template_id
    template_folder.mkdir(parents=True)

    background = Image.new("RGBA", (width, height), (200, 20, 20, 255))
    mask = Image.new("L", (width, height), 0)
    region_specs = []
    for index in range(regions):
        x0 = gap + index * (frame + gap)
        for y in range(4, 4 + frame):
            for x in range(x0, x0 + frame):
                background.putpixel((x, y), (0, 255, 0, 255))
                mask.putpixel((x, y), 255)
        region_specs.append({"x": x0, "y": 4, "width": frame, "height": frame, "area": frame * frame})
    background.save(template_folder / "background.png")
    background.save(template_folder / "preview.png")
    mask.save(template_folder / "mask.png")

    manifest = {
        "template_id": template_id,
        "name": f"Wall art set of {regions}",
        "product_type": product_type,
        "canvas_width": width,
        "canvas_height": height,
        "artwork_area": {"x": gap, "y": 4, "width": width - 2 * gap, "height": frame},
        "fit_mode": "stretch",
        "background": "background.png",
        "mask": "mask.png",
        "preview": "preview.png",
        "supported_modes": ["simple"],
        "output_format": "png",
        "orientation": "landscape",
        "raw_artwork_area": {"mode": "green_frames_mockups", "regions": region_specs},
        "effects": {
            "green_frame_mockups": {
                "fit_mode": "stretch",
                "use_perspective": False,
                "feather_radius": 0,
                "edge_aa_radius": 0,
            }
        },
    }
    (template_folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return template_folder


def register_green_template(client, template_id: str, manifest_path: Path):
    """Mirror the manifest's green data into the catalog DB record."""
    from flask import current_app

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    with client.application.app_context():
        catalog = current_app.extensions["catalog_service"]
        category = catalog.create_category(manifest.get("product_type") or "Batch")
        catalog.create_template(
            {
                "template_id": template_id,
                "name": manifest["name"],
                "category_id": category["id"],
                "status": "active",
                "canvas_width": manifest["canvas_width"],
                "canvas_height": manifest["canvas_height"],
                "artwork_area": manifest["artwork_area"],
                "fit_mode": manifest.get("fit_mode", "cover"),
                "orientation": manifest.get("orientation", "landscape"),
                "background_name": "background.png",
                "preview_name": "preview.png",
                "mask_name": "mask.png",
                "raw_artwork_area": manifest.get("raw_artwork_area"),
                "effects": manifest.get("effects"),
            }
        )


def post_batch(client, spec: dict, files: dict):
    data = {"spec": json.dumps(spec)}
    data.update(files)
    return client.post(
        "/api/mockups/render/batch", data=data, content_type="multipart/form-data"
    )


def test_batch_single_item_manual_template(tmp_path):
    client, folders = build_client(tmp_path)
    write_template(folders["TEMPLATES_FOLDER"])

    response = post_batch(
        client,
        {"items": [{"id": "one", "artworks": "art_a", "template_id": "template_001"}]},
        {"art_a": (image_bytes((4, 4), (20, 220, 40, 255)), "a.png")},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    item = payload["items"][0]
    assert item["id"] == "one"
    assert item["template_id"] == "template_001"
    assert item["selection"]["mode"] == "manual"
    generated = folders["OUTPUT_FOLDER"] / Path(item["output_url"]).name
    assert generated.is_file()


def test_batch_multiple_independent_items(tmp_path):
    client, folders = build_client(tmp_path)
    write_template(folders["TEMPLATES_FOLDER"], "template_001")
    write_template(folders["TEMPLATES_FOLDER"], "template_002")

    response = post_batch(
        client,
        {
            "defaults": {"output": {"format": "png"}},
            "items": [
                {"id": "a", "artworks": ["art_a"], "template_id": "template_001"},
                {"id": "b", "artworks": ["art_b"], "template_id": "template_002"},
            ],
        },
        {
            "art_a": (image_bytes((4, 4), (20, 220, 40, 255)), "a.png"),
            "art_b": (image_bytes((4, 4), (20, 40, 220, 255)), "b.png"),
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert [item["id"] for item in payload["items"]] == ["a", "b"]
    assert all(item["success"] for item in payload["items"])


def test_batch_wall_set_renders_both_artworks_in_one_mockup(tmp_path):
    client, folders = build_client(tmp_path)
    folder = write_green_set_template(folders["TEMPLATES_FOLDER"], regions=2)
    register_green_template(client, "green_set_002", folder / "manifest.json")

    response = post_batch(
        client,
        {
            "items": [
                {
                    "id": "set",
                    "artworks": ["left", "right"],
                    "template_id": "green_set_002",
                    "fit_mode": "stretch",
                }
            ]
        },
        {
            "left": (image_bytes((8, 8), (10, 10, 250, 255)), "left.png"),
            "right": (image_bytes((8, 8), (250, 240, 10, 255)), "right.png"),
        },
    )

    assert response.status_code == 200
    item = response.get_json()["items"][0]
    assert item["success"] is True
    generated = folders["OUTPUT_FOLDER"] / Path(item["output_url"]).name
    with Image.open(generated).convert("RGBA") as output:
        # First frame shows the first artwork, second frame the second one.
        assert output.getpixel((8, 8))[:3] == (10, 10, 250)
        assert output.getpixel((20, 8))[:3] == (250, 240, 10)
        # Background between frames is untouched.
        assert output.getpixel((14, 8)) == (200, 20, 20, 255)


def test_batch_auto_selection_picks_set_template_for_two_artworks(tmp_path):
    client, folders = build_client(tmp_path)
    write_template(folders["TEMPLATES_FOLDER"], "template_001")
    folder = write_green_set_template(folders["TEMPLATES_FOLDER"], regions=2)
    register_green_template(client, "green_set_002", folder / "manifest.json")

    response = post_batch(
        client,
        {"items": [{"id": "auto-set", "artworks": ["left", "right"], "fit_mode": "stretch"}]},
        {
            "left": (image_bytes((8, 8), (10, 10, 250, 255)), "left.png"),
            "right": (image_bytes((8, 8), (250, 240, 10, 255)), "right.png"),
        },
    )

    assert response.status_code == 200
    item = response.get_json()["items"][0]
    assert item["success"] is True
    # The single-frame template cannot host a set, so auto selection must pick
    # the two-frame green template.
    assert item["template_id"] == "green_set_002"
    assert item["selection"]["mode"] == "auto"


def test_batch_auto_selection_respects_product_type_and_ratio(tmp_path):
    client, folders = build_client(tmp_path)
    # Wide artwork area (8x8 -> square) vs a wide one to test ratio choice.
    write_template(folders["TEMPLATES_FOLDER"], "template_square")
    wide_folder = folders["TEMPLATES_FOLDER"] / "template_wide"
    wide_folder.mkdir(parents=True)
    manifest = {
        "template_id": "template_wide",
        "name": "Wide frame",
        "product_type": "wide-art",
        "canvas_width": 20,
        "canvas_height": 10,
        "artwork_area": {"x": 1, "y": 1, "width": 16, "height": 8},
        "fit_mode": "cover",
        "background": "background.png",
        "supported_modes": ["simple"],
        "output_format": "png",
    }
    (wide_folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    save_image(wide_folder / "background.png", (20, 10), (200, 20, 20, 255))
    save_image(wide_folder / "preview.png", (20, 10), (200, 20, 20, 255))

    response = post_batch(
        client,
        {
            "items": [
                {
                    "id": "wide",
                    "artworks": ["banner"],
                    "selection": {"product_type": "wide-art"},
                }
            ]
        },
        {"banner": (image_bytes((16, 8), (20, 220, 40, 255)), "banner.png")},
    )

    assert response.status_code == 200
    item = response.get_json()["items"][0]
    assert item["success"] is True
    assert item["template_id"] == "template_wide"


def test_batch_output_format_webp_and_quality(tmp_path):
    client, folders = build_client(tmp_path)
    write_template(folders["TEMPLATES_FOLDER"])

    response = post_batch(
        client,
        {
            "items": [
                {
                    "id": "webp",
                    "artworks": ["art_a"],
                    "template_id": "template_001",
                    "output": {"format": "webp", "quality": 80},
                }
            ]
        },
        {"art_a": (image_bytes((4, 4), (20, 220, 40, 255)), "a.png")},
    )

    assert response.status_code == 200
    item = response.get_json()["items"][0]
    assert item["output_url"].endswith(".webp")
    generated = folders["OUTPUT_FOLDER"] / Path(item["output_url"]).name
    with Image.open(generated) as output:
        assert output.format == "WEBP"


def test_batch_isolates_item_failures(tmp_path):
    client, folders = build_client(tmp_path)
    write_template(folders["TEMPLATES_FOLDER"])

    response = post_batch(
        client,
        {
            "items": [
                {"id": "good", "artworks": ["art_a"], "template_id": "template_001"},
                {"id": "bad", "artworks": ["art_a"], "template_id": "missing_template"},
            ]
        },
        {"art_a": (image_bytes((4, 4), (20, 220, 40, 255)), "a.png")},
    )

    assert response.status_code == 207
    payload = response.get_json()
    assert payload["success"] is False
    by_id = {item["id"]: item for item in payload["items"]}
    assert by_id["good"]["success"] is True
    assert by_id["bad"]["success"] is False
    assert "not found" in by_id["bad"]["error"].lower()


def test_batch_explicit_frame_assignment_overrides_order(tmp_path):
    client, folders = build_client(tmp_path)
    folder = write_green_set_template(folders["TEMPLATES_FOLDER"], regions=2)
    register_green_template(client, "green_set_002", folder / "manifest.json")

    response = post_batch(
        client,
        {
            "items": [
                {
                    "id": "swapped",
                    "artworks": [
                        {"file": "first", "frame": 2},
                        {"file": "second", "frame": 1},
                    ],
                    "template_id": "green_set_002",
                    "fit_mode": "stretch",
                }
            ]
        },
        {
            "first": (image_bytes((8, 8), (10, 10, 250, 255)), "first.png"),
            "second": (image_bytes((8, 8), (250, 240, 10, 255)), "second.png"),
        },
    )

    assert response.status_code == 200
    item = response.get_json()["items"][0]
    assert item["success"] is True
    assert item["frame_assignment"] == ["second", "first"]
    generated = folders["OUTPUT_FOLDER"] / Path(item["output_url"]).name
    with Image.open(generated).convert("RGBA") as output:
        # Frame 1 (left) shows "second", frame 2 (right) shows "first".
        assert output.getpixel((8, 8))[:3] == (250, 240, 10)
        assert output.getpixel((20, 8))[:3] == (10, 10, 250)


def test_batch_auto_frame_assignment_matches_aspect_ratio(tmp_path):
    client, folders = build_client(tmp_path)
    # Two frames: a wide one (16x8) and a tall one (8x16).
    template_id = "green_mixed"
    template_folder = folders["TEMPLATES_FOLDER"] / template_id
    template_folder.mkdir(parents=True)
    width, height = 40, 24
    background = Image.new("RGBA", (width, height), (200, 20, 20, 255))
    mask = Image.new("L", (width, height), 0)
    regions = [
        {"x": 2, "y": 4, "width": 16, "height": 8, "area": 128},
        {"x": 24, "y": 4, "width": 8, "height": 16, "area": 128},
    ]
    for region in regions:
        for y in range(region["y"], region["y"] + region["height"]):
            for x in range(region["x"], region["x"] + region["width"]):
                background.putpixel((x, y), (0, 255, 0, 255))
                mask.putpixel((x, y), 255)
    background.save(template_folder / "background.png")
    background.save(template_folder / "preview.png")
    mask.save(template_folder / "mask.png")
    manifest = {
        "template_id": template_id,
        "name": "Mixed orientation set",
        "canvas_width": width,
        "canvas_height": height,
        "artwork_area": {"x": 2, "y": 4, "width": 30, "height": 16},
        "fit_mode": "stretch",
        "background": "background.png",
        "mask": "mask.png",
        "preview": "preview.png",
        "supported_modes": ["simple"],
        "output_format": "png",
        "raw_artwork_area": {"mode": "green_frames_mockups", "regions": regions},
        "effects": {
            "green_frame_mockups": {
                "fit_mode": "stretch",
                "use_perspective": False,
                "feather_radius": 0,
                "edge_aa_radius": 0,
            }
        },
    }
    (template_folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    register_green_template(client, template_id, template_folder / "manifest.json")

    # Send the tall artwork FIRST: ratio matching must still route the wide
    # artwork to the wide frame and the tall artwork to the tall frame.
    response = post_batch(
        client,
        {
            "items": [
                {
                    "id": "ratios",
                    "artworks": ["tall", "wide"],
                    "template_id": template_id,
                    "fit_mode": "stretch",
                }
            ]
        },
        {
            "tall": (image_bytes((8, 16), (10, 10, 250, 255)), "tall.png"),
            "wide": (image_bytes((16, 8), (250, 240, 10, 255)), "wide.png"),
        },
    )

    assert response.status_code == 200
    item = response.get_json()["items"][0]
    assert item["success"] is True
    assert item["frame_assignment"] == ["wide", "tall"]
    generated = folders["OUTPUT_FOLDER"] / Path(item["output_url"]).name
    with Image.open(generated).convert("RGBA") as output:
        assert output.getpixel((10, 8))[:3] == (250, 240, 10)   # wide frame
        assert output.getpixel((28, 12))[:3] == (10, 10, 250)   # tall frame


def test_batch_rejects_frame_out_of_range_and_duplicates(tmp_path):
    client, folders = build_client(tmp_path)
    folder = write_green_set_template(folders["TEMPLATES_FOLDER"], regions=2)
    register_green_template(client, "green_set_002", folder / "manifest.json")

    out_of_range = post_batch(
        client,
        {
            "items": [
                {
                    "id": "bad-frame",
                    "artworks": [{"file": "a", "frame": 5}, "b"],
                    "template_id": "green_set_002",
                }
            ]
        },
        {
            "a": (image_bytes((8, 8), (10, 10, 250, 255)), "a.png"),
            "b": (image_bytes((8, 8), (250, 240, 10, 255)), "b.png"),
        },
    )
    duplicate = post_batch(
        client,
        {
            "items": [
                {
                    "id": "dup",
                    "artworks": [{"file": "a", "frame": 1}, {"file": "b", "frame": 1}],
                    "template_id": "green_set_002",
                }
            ]
        },
        {
            "a": (image_bytes((8, 8), (10, 10, 250, 255)), "a.png"),
            "b": (image_bytes((8, 8), (250, 240, 10, 255)), "b.png"),
        },
    )

    assert out_of_range.status_code == 207
    bad_item = out_of_range.get_json()["items"][0]
    assert bad_item["success"] is False
    assert "frame 5" in bad_item["error"]
    assert duplicate.status_code == 400  # malformed spec, rejected up front


def test_template_detail_exposes_numbered_frames(tmp_path):
    client, folders = build_client(tmp_path)
    write_template(folders["TEMPLATES_FOLDER"], "template_001")
    write_green_set_template(folders["TEMPLATES_FOLDER"], regions=3)

    single = client.get("/api/mockups/templates/template_001")
    multi = client.get("/api/mockups/templates/green_set_002")
    missing = client.get("/api/mockups/templates/nope")

    assert single.status_code == 200
    single_frames = single.get_json()["frames"]
    assert len(single_frames) == 1
    assert single_frames[0]["frame"] == 1

    assert multi.status_code == 200
    payload = multi.get_json()
    frames = payload["frames"]
    assert [frame["frame"] for frame in frames] == [1, 2, 3]
    # Canonical order is left-to-right for same-row frames.
    assert frames[0]["x"] < frames[1]["x"] < frames[2]["x"]
    assert all({"x", "y", "width", "height", "ratio", "orientation"} <= set(f) for f in frames)

    assert missing.status_code == 404


def test_batch_rejects_malformed_spec(tmp_path):
    client, folders = build_client(tmp_path)
    write_template(folders["TEMPLATES_FOLDER"])

    missing_spec = client.post(
        "/api/mockups/render/batch", data={}, content_type="multipart/form-data"
    )
    bad_json = client.post(
        "/api/mockups/render/batch",
        data={"spec": "{not json"},
        content_type="multipart/form-data",
    )
    no_items = post_batch(client, {"items": []}, {})
    missing_file = post_batch(
        client,
        {"items": [{"id": "x", "artworks": ["nope"], "template_id": "template_001"}]},
        {},
    )

    assert missing_spec.status_code == 400
    assert bad_json.status_code == 400
    assert no_items.status_code == 400
    assert missing_file.status_code == 400
