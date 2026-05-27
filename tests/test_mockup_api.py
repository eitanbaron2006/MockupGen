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

    for mode in ("psd", "ai"):
        response = post_render(client, mode=mode)
        assert response.status_code == 501
        assert response.get_json()["success"] is False
        assert response.get_json()["error"].startswith(f"{mode.upper()} rendering")


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
