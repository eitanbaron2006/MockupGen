import io
import json
import sys
from pathlib import Path

from PIL import Image


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))


def image_bytes(size: tuple[int, int], color: tuple[int, int, int, int]) -> io.BytesIO:
    stream = io.BytesIO()
    Image.new("RGBA", size, color).save(stream, format="PNG")
    stream.seek(0)
    return stream


def striped_image_bytes() -> io.BytesIO:
    image = Image.new("RGBA", (8, 4), (20, 220, 40, 255))
    for y in range(4):
        for x in range(2):
            image.putpixel((x, y), (250, 230, 10, 255))
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    stream.seek(0)
    return stream


def save_image(
    path: Path, size: tuple[int, int], color: tuple[int, int, int, int]
) -> None:
    Image.new("RGBA", size, color).save(path, format="PNG")


def write_template(
    templates_folder: Path,
    template_id: str = "template_001",
    *,
    fit_mode: str = "cover",
    mask: str | None = None,
) -> Path:
    template_folder = templates_folder / template_id
    template_folder.mkdir(parents=True)
    manifest = {
        "template_id": template_id,
        "name": "Minimal wall frame mockup",
        "canvas_width": 10,
        "canvas_height": 10,
        "artwork_area": {"x": 1, "y": 1, "width": 8, "height": 8},
        "fit_mode": fit_mode,
        "background": "background.png",
        "foreground": "foreground.png",
        "mask": mask,
        "supported_modes": ["simple"],
        "output_format": "png",
    }
    (template_folder / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    save_image(template_folder / "background.png", (10, 10), (200, 20, 20, 255))
    foreground = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    foreground.putpixel((0, 0), (20, 30, 240, 255))
    foreground.save(template_folder / "foreground.png")
    save_image(template_folder / "preview.png", (10, 10), (200, 20, 20, 255))
    return template_folder


def build_client(tmp_path: Path, **overrides):
    from app import create_app

    paths = {
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "OUTPUT_FOLDER": str(tmp_path / "outputs"),
        "TEMPLATES_FOLDER": str(tmp_path / "templates_data"),
    }
    settings = {
        "TESTING": True,
        "MAX_CONTENT_LENGTH": 1024 * 1024,
        "ENABLE_SIMPLE_MODE": True,
        "ENABLE_PSD_MODE": True,
        "ENABLE_AI_MODE": True,
        **paths,
        **overrides,
    }
    app = create_app(settings)
    return app.test_client(), {key: Path(value) for key, value in paths.items()}


def post_render(client, template_id: str = "template_001", **fields):
    data = {
        "mode": "simple",
        "template_id": template_id,
        "output_format": "png",
        "artwork": (image_bytes((4, 4), (20, 220, 40, 255)), "artwork.png"),
        **fields,
    }
    return client.post("/api/mockups/render", data=data, content_type="multipart/form-data")


def test_health_check_reports_service(tmp_path):
    client, _ = build_client(tmp_path)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "ok",
        "service": "mockup-render-server",
    }


def test_templates_list_returns_valid_manifests_only(tmp_path):
    client, folders = build_client(tmp_path)
    write_template(folders["TEMPLATES_FOLDER"])
    invalid_folder = folders["TEMPLATES_FOLDER"] / "broken"
    invalid_folder.mkdir()
    (invalid_folder / "manifest.json").write_text("{invalid-json", encoding="utf-8")

    response = client.get("/api/mockups/templates")

    assert response.status_code == 200
    assert response.get_json() == [
        {
            "template_id": "template_001",
            "name": "Minimal wall frame mockup",
            "preview_url": "/templates/template_001/preview.png",
            "supported_modes": ["simple"],
            "orientation": "square",
            "product_type": None,
        }
    ]


def test_template_static_files_cannot_escape_templates_folder(tmp_path):
    client, folders = build_client(tmp_path)
    write_template(folders["TEMPLATES_FOLDER"])
    (tmp_path / "private.txt").write_text("not public", encoding="utf-8")

    response = client.get("/templates/../private.txt")

    assert response.status_code == 404


def test_simple_render_composites_artwork_and_transparent_foreground(tmp_path):
    client, folders = build_client(tmp_path)
    write_template(folders["TEMPLATES_FOLDER"])

    response = post_render(client)

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["mode"] == "simple"
    assert payload["template_id"] == "template_001"
    assert payload["width"] == 10
    assert payload["height"] == 10
    generated_path = folders["OUTPUT_FOLDER"] / Path(payload["output_url"]).name
    with Image.open(generated_path).convert("RGBA") as output:
        assert output.getpixel((3, 3)) == (20, 220, 40, 255)
        assert output.getpixel((0, 0)) == (20, 30, 240, 255)
        assert output.getpixel((9, 9)) == (200, 20, 20, 255)


def test_simple_render_ignores_missing_optional_foreground_asset(tmp_path):
    client, folders = build_client(tmp_path)
    template_folder = write_template(folders["TEMPLATES_FOLDER"])
    (template_folder / "foreground.png").unlink()

    templates_response = client.get("/api/mockups/templates")
    response = post_render(client)

    assert templates_response.status_code == 200
    assert len(templates_response.get_json()) == 1
    assert response.status_code == 200
    generated_path = folders["OUTPUT_FOLDER"] / Path(
        response.get_json()["output_url"]
    ).name
    with Image.open(generated_path).convert("RGBA") as output:
        assert output.getpixel((0, 0)) == (200, 20, 20, 255)
        assert output.getpixel((3, 3)) == (20, 220, 40, 255)


def test_contain_fit_preserves_background_outside_scaled_artwork(tmp_path):
    client, folders = build_client(tmp_path)
    write_template(folders["TEMPLATES_FOLDER"], fit_mode="contain")
    artwork = (image_bytes((8, 4), (20, 220, 40, 255)), "wide-artwork.png")

    response = post_render(client, artwork=artwork)

    generated_path = folders["OUTPUT_FOLDER"] / Path(
        response.get_json()["output_url"]
    ).name
    with Image.open(generated_path).convert("RGBA") as output:
        assert output.getpixel((4, 2)) == (200, 20, 20, 255)
        assert output.getpixel((4, 4)) == (20, 220, 40, 255)


def test_stretch_fit_uses_entire_source_image_without_crop(tmp_path):
    client, folders = build_client(tmp_path)
    write_template(folders["TEMPLATES_FOLDER"], fit_mode="stretch")
    artwork = (striped_image_bytes(), "striped-artwork.png")

    response = post_render(client, artwork=artwork)

    generated_path = folders["OUTPUT_FOLDER"] / Path(
        response.get_json()["output_url"]
    ).name
    with Image.open(generated_path).convert("RGBA") as output:
        assert output.getpixel((1, 1)) == (250, 230, 10, 255)
        assert output.getpixel((8, 1)) == (20, 220, 40, 255)


def test_simple_render_applies_artwork_area_mask(tmp_path):
    client, folders = build_client(tmp_path)
    template_folder = write_template(folders["TEMPLATES_FOLDER"], mask="mask.png")
    mask = Image.new("L", (8, 8), 255)
    for y in range(8):
        for x in range(4):
            mask.putpixel((x, y), 0)
    mask.save(template_folder / "mask.png")

    response = post_render(client)

    generated_path = folders["OUTPUT_FOLDER"] / Path(
        response.get_json()["output_url"]
    ).name
    with Image.open(generated_path).convert("RGBA") as output:
        assert output.getpixel((2, 4)) == (200, 20, 20, 255)
        assert output.getpixel((7, 4)) == (20, 220, 40, 255)


def test_simple_render_uses_catalog_mask_override_for_detected_templates(tmp_path):
    from app import create_app

    templates_folder = tmp_path / "templates_data"
    template_folder = write_template(templates_folder)
    mask = Image.new("L", (8, 8), 255)
    for y in range(8):
        for x in range(4):
            mask.putpixel((x, y), 0)
    mask.save(template_folder / "mask.png")

    app = create_app(
        {
            "TESTING": True,
            "MAX_CONTENT_LENGTH": 1024 * 1024,
            "ENABLE_SIMPLE_MODE": True,
            "ENABLE_PSD_MODE": True,
            "ENABLE_AI_MODE": True,
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "OUTPUT_FOLDER": str(tmp_path / "outputs"),
            "TEMPLATES_FOLDER": str(templates_folder),
            "DATABASE_PATH": str(tmp_path / "data" / "catalog.sqlite3"),
            "DRAFT_TEMPLATES_FOLDER": str(tmp_path / "draft_templates"),
        }
    )
    app.extensions["catalog_service"].update_template(
        "template_001",
        {"mask_name": "mask.png"},
    )
    client = app.test_client()

    response = post_render(client)

    generated_path = tmp_path / "outputs" / Path(response.get_json()["output_url"]).name
    with Image.open(generated_path).convert("RGBA") as output:
        assert output.getpixel((2, 4)) == (200, 20, 20, 255)
        assert output.getpixel((7, 4)) == (20, 220, 40, 255)


def test_simple_render_supports_catalog_draft_template_masks(tmp_path):
    from app import create_app

    draft_root = tmp_path / "draft_templates"
    draft_folder = draft_root / "draft_001"
    draft_folder.mkdir(parents=True)
    save_image(draft_folder / "background.png", (10, 10), (200, 20, 20, 255))
    save_image(draft_folder / "preview.png", (10, 10), (200, 20, 20, 255))
    mask = Image.new("L", (8, 8), 255)
    for y in range(8):
        for x in range(4):
            mask.putpixel((x, y), 0)
    mask.save(draft_folder / "mask.png")

    app = create_app(
        {
            "TESTING": True,
            "MAX_CONTENT_LENGTH": 1024 * 1024,
            "ENABLE_SIMPLE_MODE": True,
            "ENABLE_PSD_MODE": True,
            "ENABLE_AI_MODE": True,
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "OUTPUT_FOLDER": str(tmp_path / "outputs"),
            "TEMPLATES_FOLDER": str(tmp_path / "templates_data"),
            "DATABASE_PATH": str(tmp_path / "data" / "catalog.sqlite3"),
            "DRAFT_TEMPLATES_FOLDER": str(draft_root),
        }
    )
    catalog = app.extensions["catalog_service"]
    category = catalog.create_category("Drafts")
    catalog.create_template(
        {
            "template_id": "draft_001",
            "name": "Draft green frame",
            "category_id": category["id"],
            "status": "draft",
            "canvas_width": 10,
            "canvas_height": 10,
            "artwork_area": {"x": 1, "y": 1, "width": 8, "height": 8},
            "fit_mode": "cover",
            "orientation": "square",
            "background_name": "background.png",
            "preview_name": "preview.png",
            "mask_name": "mask.png",
        }
    )
    client = app.test_client()

    response = post_render(client, template_id="draft_001")

    assert response.status_code == 200
    generated_path = tmp_path / "outputs" / Path(response.get_json()["output_url"]).name
    with Image.open(generated_path).convert("RGBA") as output:
        assert output.getpixel((2, 4)) == (200, 20, 20, 255)
        assert output.getpixel((7, 4)) == (20, 220, 40, 255)


def test_green_frame_render_fills_all_detected_mask_regions_by_default(tmp_path):
    from app import create_app

    templates_folder = tmp_path / "templates_data"
    template_folder = write_template(templates_folder)
    background = Image.new("RGBA", (24, 12), (200, 20, 20, 255))
    for box in ((2, 2, 9, 9), (14, 2, 21, 9)):
        for y in range(box[1], box[3] + 1):
            for x in range(box[0], box[2] + 1):
                background.putpixel((x, y), (0, 255, 0, 255))
    background.save(template_folder / "background.png")
    background.save(template_folder / "preview.png")
    manifest_path = template_folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["canvas_width"] = 24
    manifest["canvas_height"] = 12
    manifest["artwork_area"] = {"x": 2, "y": 2, "width": 20, "height": 8}
    manifest["foreground"] = None
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    mask = Image.new("L", (24, 12), 0)
    for box in ((2, 2, 9, 9), (14, 2, 21, 9)):
        for y in range(box[1], box[3] + 1):
            for x in range(box[0], box[2] + 1):
                mask.putpixel((x, y), 255)
    mask.save(template_folder / "mask.png")

    app = create_app(
        {
            "TESTING": True,
            "MAX_CONTENT_LENGTH": 1024 * 1024,
            "ENABLE_SIMPLE_MODE": True,
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "OUTPUT_FOLDER": str(tmp_path / "outputs"),
            "TEMPLATES_FOLDER": str(templates_folder),
            "DATABASE_PATH": str(tmp_path / "data" / "catalog.sqlite3"),
            "DRAFT_TEMPLATES_FOLDER": str(tmp_path / "draft_templates"),
        }
    )
    app.extensions["catalog_service"].update_template(
        "template_001",
        {
            "artwork_area": {"x": 2, "y": 2, "width": 20, "height": 8},
            "mask_name": "mask.png",
            "raw_artwork_area": {
                "mode": "green_frames_mockups",
                "regions": [
                    {"x": 2, "y": 2, "width": 8, "height": 8, "area": 64},
                    {"x": 14, "y": 2, "width": 8, "height": 8, "area": 64},
                ],
            },
            "effects": {
                "green_frame_mockups": {
                    "fit_mode": "stretch",
                    "feather_radius": 0,
                    "edge_aa_radius": 0,
                    "enable_inner_shadow": False,
                }
            },
        },
    )
    client = app.test_client()

    response = post_render(
        client,
        artwork=(striped_image_bytes(), "striped.png"),
    )

    assert response.status_code == 200
    generated_path = tmp_path / "outputs" / Path(response.get_json()["output_url"]).name
    with Image.open(generated_path).convert("RGBA") as output:
        assert output.getpixel((2, 5)) == (250, 230, 10, 255)
        assert output.getpixel((14, 5)) == (250, 230, 10, 255)
        assert output.getpixel((11, 5)) == (200, 20, 20, 255)


def test_green_frame_stretch_scale_does_not_fill_mask_with_contain_background(tmp_path):
    from app import create_app

    templates_folder = tmp_path / "templates_data"
    template_folder = write_template(templates_folder)
    Image.new("RGBA", (12, 12), (200, 20, 20, 255)).save(template_folder / "background.png")
    Image.new("RGBA", (12, 12), (200, 20, 20, 255)).save(template_folder / "preview.png")
    manifest_path = template_folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["canvas_width"] = 12
    manifest["canvas_height"] = 12
    manifest["artwork_area"] = {"x": 2, "y": 2, "width": 8, "height": 8}
    manifest["foreground"] = None
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    mask = Image.new("L", (12, 12), 0)
    for y in range(2, 10):
        for x in range(2, 10):
            mask.putpixel((x, y), 255)
    mask.save(template_folder / "mask.png")

    app = create_app(
        {
            "TESTING": True,
            "MAX_CONTENT_LENGTH": 1024 * 1024,
            "ENABLE_SIMPLE_MODE": True,
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "OUTPUT_FOLDER": str(tmp_path / "outputs"),
            "TEMPLATES_FOLDER": str(templates_folder),
            "DATABASE_PATH": str(tmp_path / "data" / "catalog.sqlite3"),
            "DRAFT_TEMPLATES_FOLDER": str(tmp_path / "draft_templates"),
        }
    )
    app.extensions["catalog_service"].update_template(
        "template_001",
        {
            "artwork_area": {"x": 2, "y": 2, "width": 8, "height": 8},
            "mask_name": "mask.png",
            "raw_artwork_area": {
                "mode": "green_frames_mockups",
                "regions": [{"x": 2, "y": 2, "width": 8, "height": 8, "area": 64}],
            },
            "effects": {
                "green_frame_mockups": {
                    "fit_mode": "stretch",
                    "artwork_scale": 0.5,
                    "contain_bg_color": "#ffffff",
                    "enable_inner_shadow": False,
                }
            },
        },
    )
    client = app.test_client()

    response = post_render(
        client,
        artwork=(image_bytes((4, 4), (20, 40, 220, 255)), "blue.png"),
    )

    assert response.status_code == 200
    generated_path = tmp_path / "outputs" / Path(response.get_json()["output_url"]).name
    with Image.open(generated_path).convert("RGBA") as output:
        assert output.getpixel((3, 6)) == (200, 20, 20, 255)
        assert output.getpixel((6, 6)) == (20, 40, 220, 255)


def test_green_frame_contain_scale_can_fill_margin_with_contain_background(tmp_path):
    from app import create_app

    templates_folder = tmp_path / "templates_data"
    template_folder = write_template(templates_folder)
    Image.new("RGBA", (12, 12), (200, 20, 20, 255)).save(template_folder / "background.png")
    Image.new("RGBA", (12, 12), (200, 20, 20, 255)).save(template_folder / "preview.png")
    manifest_path = template_folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["canvas_width"] = 12
    manifest["canvas_height"] = 12
    manifest["artwork_area"] = {"x": 2, "y": 2, "width": 8, "height": 8}
    manifest["foreground"] = None
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    mask = Image.new("L", (12, 12), 0)
    for y in range(2, 10):
        for x in range(2, 10):
            mask.putpixel((x, y), 255)
    mask.save(template_folder / "mask.png")

    app = create_app(
        {
            "TESTING": True,
            "MAX_CONTENT_LENGTH": 1024 * 1024,
            "ENABLE_SIMPLE_MODE": True,
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "OUTPUT_FOLDER": str(tmp_path / "outputs"),
            "TEMPLATES_FOLDER": str(templates_folder),
            "DATABASE_PATH": str(tmp_path / "data" / "catalog.sqlite3"),
            "DRAFT_TEMPLATES_FOLDER": str(tmp_path / "draft_templates"),
        }
    )
    app.extensions["catalog_service"].update_template(
        "template_001",
        {
            "artwork_area": {"x": 2, "y": 2, "width": 8, "height": 8},
            "mask_name": "mask.png",
            "raw_artwork_area": {
                "mode": "green_frames_mockups",
                "regions": [{"x": 2, "y": 2, "width": 8, "height": 8, "area": 64}],
            },
            "effects": {
                "green_frame_mockups": {
                    "fit_mode": "contain",
                    "artwork_scale": 0.5,
                    "contain_bg_color": "#ffffff",
                    "enable_inner_shadow": False,
                }
            },
        },
    )
    client = app.test_client()

    response = post_render(
        client,
        artwork=(image_bytes((4, 4), (20, 40, 220, 255)), "blue.png"),
    )

    assert response.status_code == 200
    generated_path = tmp_path / "outputs" / Path(response.get_json()["output_url"]).name
    with Image.open(generated_path).convert("RGBA") as output:
        assert output.getpixel((3, 6)) == (255, 255, 255, 255)
        assert output.getpixel((6, 6)) == (20, 40, 220, 255)


def test_green_frame_perspective_uses_wide_envelope_then_clips_to_mask(tmp_path):
    from app import create_app

    templates_folder = tmp_path / "templates_data"
    template_folder = write_template(templates_folder)
    Image.new("RGBA", (12, 12), (200, 20, 20, 255)).save(template_folder / "background.png")
    Image.new("RGBA", (12, 12), (200, 20, 20, 255)).save(template_folder / "preview.png")
    manifest_path = template_folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["canvas_width"] = 12
    manifest["canvas_height"] = 12
    manifest["artwork_area"] = {"x": 2, "y": 2, "width": 8, "height": 8}
    manifest["foreground"] = None
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    mask = Image.new("L", (12, 12), 0)
    for y in range(2, 10):
        for x in range(2, 10):
            mask.putpixel((x, y), 255)
    mask.save(template_folder / "mask.png")

    app = create_app(
        {
            "TESTING": True,
            "MAX_CONTENT_LENGTH": 1024 * 1024,
            "ENABLE_SIMPLE_MODE": True,
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "OUTPUT_FOLDER": str(tmp_path / "outputs"),
            "TEMPLATES_FOLDER": str(templates_folder),
            "DATABASE_PATH": str(tmp_path / "data" / "catalog.sqlite3"),
            "DRAFT_TEMPLATES_FOLDER": str(tmp_path / "draft_templates"),
        }
    )
    app.extensions["catalog_service"].update_template(
        "template_001",
        {
            "artwork_area": {"x": 2, "y": 2, "width": 8, "height": 8},
            "mask_name": "mask.png",
            "raw_artwork_area": {
                "mode": "green_frames_mockups",
                "regions": [
                    {
                        "x": 2,
                        "y": 2,
                        "width": 8,
                        "height": 8,
                        "area": 64,
                        "inner_corners": [
                            {"x": 4, "y": 4},
                            {"x": 8, "y": 4},
                            {"x": 8, "y": 8},
                            {"x": 4, "y": 8},
                        ],
                        "outer_corners": [
                            {"x": 2, "y": 2},
                            {"x": 9, "y": 2},
                            {"x": 9, "y": 9},
                            {"x": 2, "y": 9},
                        ],
                    }
                ],
            },
            "effects": {
                "green_frame_mockups": {
                    "use_perspective": True,
                    "use_vector_clip": True,
                    "fit_mode": "stretch",
                    "edge_expand": 0,
                    "feather_radius": 0,
                    "edge_aa_radius": 0,
                    "enable_inner_shadow": False,
                }
            },
        },
    )
    client = app.test_client()

    response = post_render(client, artwork=(striped_image_bytes(), "striped.png"))

    assert response.status_code == 200
    generated_path = tmp_path / "outputs" / Path(response.get_json()["output_url"]).name
    with Image.open(generated_path).convert("RGBA") as output:
        assert output.getpixel((2, 6)) != (200, 20, 20, 255)
        assert output.getpixel((1, 6)) == (200, 20, 20, 255)


def test_render_rejects_missing_template_and_invalid_file_type(tmp_path):
    client, folders = build_client(tmp_path)
    write_template(folders["TEMPLATES_FOLDER"])

    missing = post_render(client, template_id="not_here")
    invalid = post_render(
        client,
        artwork=(io.BytesIO(b"not-an-image"), "artwork.txt"),
    )

    assert missing.status_code == 404
    assert missing.get_json() == {"success": False, "error": "Template not found"}
    assert invalid.status_code == 400
    assert invalid.get_json() == {
        "success": False,
        "error": "Unsupported artwork file type",
    }


def test_placeholder_modes_return_not_implemented_json(tmp_path):
    client, folders = build_client(tmp_path)
    write_template(folders["TEMPLATES_FOLDER"])

    for mode in ("psd",):
        response = post_render(client, mode=mode)
        assert response.status_code == 501
        assert response.get_json()["success"] is False
        assert response.get_json()["error"].startswith(f"{mode.upper()} rendering")


def test_ai_render_mode_invokes_vertex_ai_generation(tmp_path: Path, monkeypatch):
    client, folders = build_client(
        tmp_path,
        VERTEX_PROJECT_ID="test-project",
        VERTEX_LOCATION="global",
    )
    write_template(folders["TEMPLATES_FOLDER"])

    class FakePart:
        def __init__(self, data, mime_type):
            class InlineData:
                def __init__(self, data, mime_type):
                    self.data = data
                    self.mime_type = mime_type
            self.inline_data = InlineData(data, mime_type)
            self.text = None

    class FakeContent:
        def __init__(self, data):
            self.parts = [FakePart(data, "image/png")]

    class FakeCandidate:
        def __init__(self, data):
            self.content = FakeContent(data)

    class FakeResponse:
        def __init__(self, data):
            self.candidates = [FakeCandidate(data)]

    dummy_img_stream = io.BytesIO()
    Image.new("RGBA", (10, 10), (255, 0, 0, 255)).save(dummy_img_stream, format="PNG")
    dummy_bytes = dummy_img_stream.getvalue()

    class FakeModels:
        def generate_content(self, **kwargs):
            return FakeResponse(dummy_bytes)

    class FakeClient:
        def __init__(self, **kwargs):
            self.models = FakeModels()

    import google.genai
    monkeypatch.setattr(google.genai, "Client", FakeClient)

    response = post_render(client, mode="ai")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["mode"] == "ai"
    assert payload["template_id"] == "template_001"
    assert payload["width"] == 10
    assert payload["height"] == 10
    assert payload["output_url"].startswith("/outputs/mockup_ai_")


def test_ai_render_mode_passes_custom_model_parameter(tmp_path: Path, monkeypatch):
    client, folders = build_client(
        tmp_path,
        VERTEX_PROJECT_ID="test-project",
        VERTEX_LOCATION="global",
    )
    write_template(folders["TEMPLATES_FOLDER"])

    class FakePart:
        def __init__(self, data, mime_type):
            class InlineData:
                def __init__(self, data, mime_type):
                    self.data = data
                    self.mime_type = mime_type
            self.inline_data = InlineData(data, mime_type)
            self.text = None

    class FakeContent:
        def __init__(self, data):
            self.parts = [FakePart(data, "image/png")]

    class FakeCandidate:
        def __init__(self, data):
            self.content = FakeContent(data)

    class FakeResponse:
        def __init__(self, data):
            self.candidates = [FakeCandidate(data)]

    dummy_img_stream = io.BytesIO()
    Image.new("RGBA", (10, 10), (255, 0, 0, 255)).save(dummy_img_stream, format="PNG")
    dummy_bytes = dummy_img_stream.getvalue()

    captured_model = None

    class FakeModels:
        def generate_content(self, model, **kwargs):
            nonlocal captured_model
            captured_model = model
            return FakeResponse(dummy_bytes)

    class FakeClient:
        def __init__(self, **kwargs):
            self.models = FakeModels()

    import google.genai
    monkeypatch.setattr(google.genai, "Client", FakeClient)

    response = post_render(client, mode="ai", model="gemini-3.1-flash-image-preview")

    assert response.status_code == 200
    assert captured_model == "gemini-3.1-flash-image-preview"


def test_render_can_select_closest_template_by_product_type_and_artwork_ratio(tmp_path):
    client, folders = build_client(tmp_path)
    portrait_folder = write_template(folders["TEMPLATES_FOLDER"], "portrait")
    landscape_folder = write_template(folders["TEMPLATES_FOLDER"], "landscape")
    for folder, area in (
        (portrait_folder, {"x": 2, "y": 1, "width": 4, "height": 8}),
        (landscape_folder, {"x": 1, "y": 2, "width": 8, "height": 4}),
    ):
        manifest_path = folder / "manifest.json"
        manifest = json.loads(manifest_path.read_text("utf-8"))
        manifest["product_type"] = "wall-art"
        manifest["artwork_area"] = area
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    response = client.post(
        "/api/mockups/render",
        data={
            "mode": "simple",
            "product_type": "wall-art",
            "output_format": "png",
            "artwork": (image_bytes((200, 400), (20, 220, 40, 255)), "portrait.png"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert response.get_json()["template_id"] == "portrait"


def test_perspective_render_warps_quadrilateral(tmp_path):
    client, folders = build_client(tmp_path)
    template_folder = folders["TEMPLATES_FOLDER"] / "perspective_001"
    template_folder.mkdir(parents=True)
    manifest = {
        "template_id": "perspective_001",
        "name": "Perspective mockup",
        "canvas_width": 10,
        "canvas_height": 10,
        "artwork_area": {
            "x": 2,
            "y": 2,
            "width": 6,
            "height": 6,
            "corners": [
                {"x": 2, "y": 2},
                {"x": 8, "y": 3},
                {"x": 7, "y": 8},
                {"x": 3, "y": 7}
            ]
        },
        "fit_mode": "stretch",
        "background": "background.png",
        "supported_modes": ["simple"],
        "output_format": "png",
    }
    (template_folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    save_image(template_folder / "background.png", (10, 10), (200, 20, 20, 255))
    save_image(template_folder / "preview.png", (10, 10), (200, 20, 20, 255))

    response = post_render(client, template_id="perspective_001")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    generated_path = folders["OUTPUT_FOLDER"] / Path(payload["output_url"]).name
    with Image.open(generated_path).convert("RGBA") as output:
        assert output.getpixel((2, 2)) == (20, 220, 40, 255)
        assert output.getpixel((0, 0)) == (200, 20, 20, 255)


def test_pillow_rendering_applies_realism_filters_and_feathering(tmp_path):
    # Tests that the realism filter (color mapping/grain) and border feathering are applied during PIL rendering
    client, folders = build_client(tmp_path)
    template_folder = folders["TEMPLATES_FOLDER"] / "realism_test"
    template_folder.mkdir(parents=True)
    manifest = {
        "template_id": "realism_test",
        "name": "Realism test mockup",
        "canvas_width": 10,
        "canvas_height": 10,
        "artwork_area": {
            "x": 2,
            "y": 2,
            "width": 6,
            "height": 6
        },
        "fit_mode": "stretch",
        "background": "background.png",
        "supported_modes": ["simple"],
        "output_format": "png",
    }
    (template_folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    # Clean solid blue background
    save_image(template_folder / "background.png", (10, 10), (0, 0, 255, 255))
    save_image(template_folder / "preview.png", (10, 10), (0, 0, 255, 255))

    # Render with pure solid white artwork (255, 255, 255, 255)
    response = client.post(
        "/api/mockups/render",
        data={
            "template_id": "realism_test",
            "mode": "simple",
            "output_format": "png",
            "artwork": (image_bytes((6, 6), (255, 255, 255, 255)), "artwork.png"),
            "realism": "true",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    
    generated_path = folders["OUTPUT_FOLDER"] / Path(payload["output_url"]).name
    with Image.open(generated_path).convert("RGBA") as output:
        # 1. Verify White Point Mapping & Paper Noise:
        # Pure white artwork (255, 255, 255) should be compressed to soft-print (max value around 246)
        # And introduce slight noise fluctuations.
        inner_pixel = output.getpixel((5, 5))
        assert inner_pixel[0] < 250  # Must be compressed below 250 (white point is ~246)
        assert inner_pixel[1] < 250
        assert inner_pixel[2] < 250
        
        # 2. Verify Edge Feathering (Soften border):
        # The boundary pixel at (2, 2) is a corner pixel and should be blended (alpha channel diluted)
        # It should contain a blend of blue background and soft white artwork
        border_pixel = output.getpixel((2, 2))
        # Blue channel should be partially visible (from the blurred background blend)
        assert border_pixel[2] > 0
        # Red/Green channels should be partially visible (from the feathered white artwork)
        assert border_pixel[0] > 0
        # The pixel is not fully opaque blue nor fully opaque white - it's a feathered blend!
        assert border_pixel != (0, 0, 255, 255)


def test_per_mockup_realism_effects(tmp_path):
    # Tests that custom inner shadow and glass reflection effects configured on a template are correctly applied
    client, folders = build_client(tmp_path)
    template_folder = folders["TEMPLATES_FOLDER"] / "effects_test"
    template_folder.mkdir(parents=True)
    manifest = {
        "template_id": "effects_test",
        "name": "Effects test mockup",
        "canvas_width": 10,
        "canvas_height": 10,
        "artwork_area": {
            "x": 2,
            "y": 2,
            "width": 6,
            "height": 6
        },
        "fit_mode": "stretch",
        "background": "background.png",
        "supported_modes": ["simple"],
        "output_format": "png",
        "effects": {
            "inner_shadow": {
                "enabled": True,
                "top": 2,
                "bottom": 2,
                "left": 2,
                "right": 2,
                "opacity": 1.0,
                "blur": 0
            },
            "glass_reflection": {
                "enabled": True,
                "type": "diagonal",
                "opacity": 1.0
            }
        }
    }
    (template_folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    save_image(template_folder / "background.png", (10, 10), (0, 0, 255, 255))
    save_image(template_folder / "preview.png", (10, 10), (0, 0, 255, 255))

    # Render with pure solid red artwork (255, 0, 0, 255)
    response = client.post(
        "/api/mockups/render",
        data={
            "template_id": "effects_test",
            "mode": "simple",
            "output_format": "png",
            "artwork": (image_bytes((6, 6), (255, 0, 0, 255)), "artwork.png"),
            "realism": "true",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True

    generated_path = folders["OUTPUT_FOLDER"] / Path(payload["output_url"]).name
    with Image.open(generated_path).convert("RGBA") as output:
        # Verify the pixel rendering works and generates a valid output image without PIL size/mismatch exceptions
        assert output.size == (10, 10)
        pixel_with_shadow = output.getpixel((3, 3))
        assert pixel_with_shadow[0] < 255


def test_global_realism_effects(tmp_path):
    # Tests that global Photoshop color filters, volumetric sun rays, window frame reflections, and global PNG overlays are correctly applied
    client, folders = build_client(tmp_path)
    template_folder = folders["TEMPLATES_FOLDER"] / "global_effects_test"
    template_folder.mkdir(parents=True)
    
    # 4x4 transparent green block base64 encoded overlay
    overlay_b64 = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAYAAACp8Z5+AAAABmJLR0QA/wD/AP+gvaeTAAAACXBIWXMAAAsTAAALEwEAmpwYAAAAB3RJTUUH6AYbERIZM8O26gAAADFJREFUCNdj/M/A8J+BEYgZGBgY/zMw/Gf8z8AIEwRxmBgYGBhRJM/A8B8mCJJn+P8fADuWDAZt9u/kAAAAAElFTkSuQmCC"
    
    manifest = {
        "template_id": "global_effects_test",
        "name": "Global Effects Test Mockup",
        "canvas_width": 10,
        "canvas_height": 10,
        "artwork_area": {
            "x": 2,
            "y": 2,
            "width": 6,
            "height": 6
        },
        "fit_mode": "stretch",
        "background": "background.png",
        "supported_modes": ["simple"],
        "output_format": "png",
        "effects": {
            "photoshop_adjustments": {
                "enabled": True,
                "brightness": 0.2,
                "contrast": -0.1,
                "saturation": 0.1,
                "color_filter": "vintage"
            },
            "global_reflections": {
                "enabled": True,
                "window_type": "foliage",
                "window_opacity": 0.4,
                "window_blur": 5,
                "rays_type": "warm_sunlight",
                "rays_opacity": 0.3,
                "rays_angle": 15
            },
            "global_png_overlay": {
                "enabled": True,
                "image": overlay_b64,
                "opacity": 0.8
            }
        }
    }
    (template_folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    save_image(template_folder / "background.png", (10, 10), (0, 0, 255, 255))
    save_image(template_folder / "preview.png", (10, 10), (0, 0, 255, 255))

    # Render with pure solid red artwork (255, 0, 0, 255)
    response = client.post(
        "/api/mockups/render",
        data={
            "template_id": "global_effects_test",
            "mode": "simple",
            "output_format": "png",
            "artwork": (image_bytes((6, 6), (255, 0, 0, 255)), "artwork.png"),
            "realism": "true",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True

    generated_path = folders["OUTPUT_FOLDER"] / Path(payload["output_url"]).name
    with Image.open(generated_path).convert("RGBA") as output:
        assert output.size == (10, 10)
        # Check that global PNG overlay, vintage tone, and foliage shadow composite properly without throwing PIL errors
        top_left_pixel = output.getpixel((0, 0))
        assert top_left_pixel != (0, 0, 255, 255)  # The background blue must be shifted by the global environmental effects!


def test_targeted_realism_effects(tmp_path):
    # Tests that target routing (artwork vs mockup vs all) strictly targets the correct layers
    client, folders = build_client(tmp_path)
    template_folder = folders["TEMPLATES_FOLDER"] / "target_effects_test"
    template_folder.mkdir(parents=True)
    
    # We will test using photoshop_adjustments since it is highly visible
    manifest = {
        "template_id": "target_effects_test",
        "name": "Target Effects Test Mockup",
        "canvas_width": 10,
        "canvas_height": 10,
        "artwork_area": {
            "x": 2,
            "y": 2,
            "width": 6,
            "height": 6
        },
        "fit_mode": "stretch",
        "background": "background.png",
        "supported_modes": ["simple"],
        "output_format": "png",
        "effects": {
            "photoshop_adjustments": {
                "enabled": True,
                "brightness": 0.5,
                "contrast": 0.5,
                "saturation": 0.5,
                "color_filter": "vintage",
                "target": "mockup" # Target mockup background only!
            }
        }
    }
    (template_folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    save_image(template_folder / "background.png", (10, 10), (0, 0, 255, 255)) # Pure Blue background
    save_image(template_folder / "preview.png", (10, 10), (0, 0, 255, 255))

    # Render with pure solid red artwork (255, 0, 0, 255)
    # Note: disabled the default glass cover sheen by enabling a custom glass reflection with 0 opacity, targeting artwork
    manifest["effects"]["glass_reflection"] = {
        "enabled": True,
        "type": "diagonal",
        "opacity": 0.0,
        "target": "artwork"
    }
    (template_folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    response = client.post(
        "/api/mockups/render",
        data={
            "template_id": "target_effects_test",
            "mode": "simple",
            "output_format": "png",
            "artwork": (image_bytes((6, 6), (255, 0, 0, 255)), "artwork.png"),
            "realism": "true",
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True

    generated_path = folders["OUTPUT_FOLDER"] / Path(payload["output_url"]).name
    with Image.open(generated_path).convert("RGBA") as output:
        # Check background pixel at (0, 0)
        bg_pixel = output.getpixel((0, 0))
        # Since photoshop adjustments targeted 'mockup', the blue background must be altered!
        assert bg_pixel != (0, 0, 255, 255)

        # Check artwork pixel at (5, 5) (well inside the 6x6 artwork area)
        art_pixel = output.getpixel((5, 5))
        # Since photoshop adjustments targeted 'mockup', the red artwork must NOT be affected by the vintage LUT curves!
        # (It only undergoes default B&W point print compression: 255 -> 246, i.e. 246, 8, 8, 255)
        assert art_pixel[0] == 246
        assert art_pixel[1] == 8
        assert art_pixel[2] == 8
