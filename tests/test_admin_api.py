import io
import json
import shutil
import sys
from pathlib import Path

from PIL import Image

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from app import create_app
from services.detection_service import DetectionProposal


def image_bytes(size: tuple[int, int] = (640, 800)) -> io.BytesIO:
    stream = io.BytesIO()
    Image.new("RGBA", size, (238, 229, 214, 255)).save(stream, format="PNG")
    stream.seek(0)
    return stream


def build_app(tmp_path: Path):
    return create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "ADMIN_PASSWORD": "admin-pass",
            "DATABASE_PATH": str(tmp_path / "data" / "catalog.sqlite3"),
            "DRAFT_TEMPLATES_FOLDER": str(tmp_path / "draft_templates"),
            "TEMPLATES_FOLDER": str(tmp_path / "templates"),
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "OUTPUT_FOLDER": str(tmp_path / "outputs"),
            "DETECTION_PROVIDER": "classic",
        }
    )


def login(client) -> str:
    response = client.post("/api/admin/login", json={"password": "admin-pass"})
    assert response.status_code == 200
    return response.get_json()["csrf_token"]


def test_admin_page_and_authenticated_category_crud(tmp_path: Path):
    client = build_app(tmp_path).test_client()

    login_page = client.get("/admin/login")
    assert login_page.status_code == 200
    assert b"Mockup Studio" in login_page.data
    assert b"Approve artwork areas with precision" in login_page.data
    assert client.get("/admin").status_code == 302
    assert client.get("/api/admin/categories").status_code == 401

    csrf = login(client)
    response = client.post(
        "/api/admin/categories",
        json={"name": "Wall Art"},
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 201
    assert response.get_json()["category"]["slug"] == "wall-art"
    admin_page = client.get("/admin")
    assert admin_page.status_code == 200
    assert b"Import queue" in admin_page.data
    assert b"Template details" in admin_page.data
    assert b"Detect frame" in admin_page.data
    assert b"Authentication" in admin_page.data
    assert b"Test connection" in admin_page.data
    assert b"Local model" in admin_page.data
    assert b"dashed" in admin_page.data
    assert b"Classic / No AI" in admin_page.data
    assert b"Green Frames" in admin_page.data
    assert b"Green edge cleanup expansion" in admin_page.data
    categories = client.get("/api/admin/categories").get_json()["categories"]
    assert categories[0]["name"] == "Wall Art"


def test_detection_settings_expose_relevant_vertex_models_and_classic_has_no_model(
    tmp_path: Path, monkeypatch
):
    client = build_app(tmp_path).test_client()
    login(client)
    monkeypatch.setattr(
        "routes.admin_routes.list_vertex_detection_models",
        lambda: [
            {"id": "gemini-3.5-flash", "label": "Gemini 3.5 Flash", "stage": "GA"},
            {"id": "gemini-3.1-pro-preview", "label": "Gemini 3.1 Pro Preview", "stage": "Preview"},
        ],
    )

    vertex = client.get("/api/admin/settings/detection/models?provider=vertex")
    classic = client.get("/api/admin/settings/detection/models?provider=classic")

    assert vertex.status_code == 200
    vertex_ids = {model["id"] for model in vertex.get_json()["models"]}
    assert "gemini-3.5-flash" in vertex_ids
    assert "gemini-3.1-pro-preview" in vertex_ids
    assert classic.get_json()["models"] == []


def test_detection_settings_save_classic_green_frames_mode(tmp_path: Path):
    client = build_app(tmp_path).test_client()
    csrf = login(client)

    response = client.put(
        "/api/admin/settings/detection",
        json={
            "DETECTION_PROVIDER": "classic",
            "CLASSIC_INTERNAL_MODE": "green_frames_mockups",
        },
        headers={"X-CSRF-Token": csrf},
    )

    assert response.status_code == 200
    assert response.get_json()["settings"]["CLASSIC_INTERNAL_MODE"] == "green_frames_mockups"
    settings = client.get("/api/admin/settings/detection").get_json()["settings"]
    assert settings["CLASSIC_INTERNAL_MODE"] == "green_frames_mockups"


def test_green_frame_detection_saves_mask_for_template_rendering(tmp_path: Path):
    app = build_app(tmp_path)
    client = app.test_client()
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}
    category = client.post(
        "/api/admin/categories", json={"name": "Posters"}, headers=headers
    ).get_json()["category"]
    stream = io.BytesIO()
    image = Image.new("RGB", (120, 100), (238, 229, 214))
    from PIL import ImageDraw
    draw = ImageDraw.Draw(image)
    draw.rectangle((35, 25, 85, 75), fill=(0, 255, 0))
    image.save(stream, format="PNG")
    stream.seek(0)
    template = client.post(
        "/api/admin/templates/import",
        data={"category_id": str(category["id"]), "mockups": [(stream, "green.png")]},
        headers=headers,
        content_type="multipart/form-data",
    ).get_json()["templates"][0]
    client.put(
        "/api/admin/settings/detection",
        json={
            "DETECTION_PROVIDER": "classic",
            "CLASSIC_INTERNAL_MODE": "green_frames_mockups",
            "CLASSIC_GREEN_EDGE_EXPAND": "2",
        },
        headers=headers,
    )

    response = client.post(
        f"/api/admin/templates/{template['template_id']}/detect",
        json={"mode": "green_frames_mockups"},
        headers=headers,
    )

    assert response.status_code == 200
    updated = response.get_json()["template"]
    assert updated["mask_name"] == "mask.png"
    mask_path = tmp_path / "draft_templates" / template["template_id"] / "mask.png"
    assert mask_path.is_file()
    with Image.open(mask_path) as mask:
      assert mask.mode == "L"
      assert mask.getpixel((33, 25)) == 255


def test_auto_detection_returns_all_frames_without_writing_a_mask(tmp_path: Path):
    app = build_app(tmp_path)
    client = app.test_client()
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}
    category = client.post(
        "/api/admin/categories", json={"name": "Gallery"}, headers=headers
    ).get_json()["category"]

    from PIL import ImageDraw

    image = Image.new("RGB", (900, 700), (232, 226, 216))
    draw = ImageDraw.Draw(image)
    boxes = [(80, 90, 320, 430), (350, 90, 590, 430), (620, 90, 860, 430)]
    for box in boxes:
        draw.rectangle(box, fill=(255, 255, 255), outline=(60, 48, 38), width=6)
        draw.rectangle(
            (box[0] + 10, box[1] + 10, box[2] - 10, box[3] - 10),
            outline=(150, 140, 130),
            width=2,
        )
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    stream.seek(0)
    template = client.post(
        "/api/admin/templates/import",
        data={"category_id": str(category["id"]), "mockups": [(stream, "wall.png")]},
        headers=headers,
        content_type="multipart/form-data",
    ).get_json()["templates"][0]

    response = client.post(
        f"/api/admin/templates/{template['template_id']}/detect",
        json={"mode": "geometry"},
        headers=headers,
    )

    assert response.status_code == 200
    payload = response.get_json()
    raw = payload["proposal"]["raw_artwork_area"]
    regions = raw["regions"]
    assert len(regions) == 3
    for region, (left, top, _right, _bottom) in zip(regions, boxes):
        assert abs(region["x"] - left) <= 16
        assert abs(region["y"] - top) <= 16

    # Geometric frames render straight onto their corners, so no mask is
    # written; one would only be a second copy of the geometry going stale.
    assert raw["mode"] == "geometry"
    assert payload["template"]["mask_name"] is None
    assert not (tmp_path / "draft_templates" / template["template_id"] / "mask.png").exists()


def test_detection_settings_can_test_provider_without_saving_proposal(
    tmp_path: Path, monkeypatch
):
    app = build_app(tmp_path)
    client = app.test_client()
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}
    category = client.post(
        "/api/admin/categories", json={"name": "Wall Art"}, headers=headers
    ).get_json()["category"]
    template = client.post(
        "/api/admin/templates/import",
        data={"category_id": str(category["id"]), "mockups": [(image_bytes(), "frame.png")]},
        headers=headers,
        content_type="multipart/form-data",
    ).get_json()["templates"][0]

    class ConnectionProvider:
        def detect(self, _background):
            return DetectionProposal(
                artwork_area={"x": 110, "y": 120, "width": 210, "height": 310},
                confidence=0.91,
                reason="live provider connected",
                provider="vertex+edges",
            )

    monkeypatch.setattr("routes.admin_routes.build_provider", lambda *_args: ConnectionProvider())

    response = client.post(
        "/api/admin/settings/detection/test",
        json={"template_id": template["template_id"]},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.get_json()["proposal"]["provider"] == "vertex+edges"


def test_batch_import_area_update_and_activation_publish_real_template(tmp_path: Path):
    app = build_app(tmp_path)
    client = app.test_client()
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}

    category = client.post(
        "/api/admin/categories",
        json={"name": "Wall Art"},
        headers=headers,
    ).get_json()["category"]
    imported = client.post(
        "/api/admin/templates/import",
        data={
            "category_id": str(category["id"]),
            "mockups": [
                (image_bytes(), "living-room.png"),
                (image_bytes((800, 640)), "desk-frame.png"),
            ],
        },
        headers=headers,
        content_type="multipart/form-data",
    )

    assert imported.status_code == 201
    templates = imported.get_json()["templates"]
    assert len(templates) == 2
    assert templates[0]["status"] == "draft"
    assert templates[0]["artwork_area"]
    assert templates[0]["detection_provider"] == "classic"
    assert templates[1]["artwork_area"]
    assert templates[1]["detection_provider"] == "classic"

    template_id = templates[0]["template_id"]
    update = client.patch(
        f"/api/admin/templates/{template_id}",
        json={
            "name": "Living Room Frame",
            "artwork_area": {"x": 170, "y": 150, "width": 290, "height": 470},
            "fit_mode": "cover",
        },
        headers=headers,
    )
    assert update.status_code == 200

    activated = client.post(
        f"/api/admin/templates/{template_id}/activate", headers=headers
    )
    assert activated.status_code == 200
    template_folder = Path(app.config["TEMPLATES_FOLDER"]) / template_id
    manifest = json.loads((template_folder / "manifest.json").read_text("utf-8"))
    assert manifest["foreground"] is None
    assert manifest["product_type"] == "wall-art"
    assert (template_folder / "background.png").exists()
    assert (template_folder / "preview.png").exists()

    public_templates = client.get(
        "/api/mockups/templates?product_type=wall-art"
    ).get_json()
    assert public_templates[0]["template_id"] == template_id


def test_import_rejects_mockup_filename_that_already_exists(tmp_path: Path):
    client = build_app(tmp_path).test_client()
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}
    category = client.post(
        "/api/admin/categories", json={"name": "Wall Art"}, headers=headers
    ).get_json()["category"]

    first = client.post(
        "/api/admin/templates/import",
        data={"category_id": str(category["id"]), "mockups": [(image_bytes(), "frame.png")]},
        headers=headers,
        content_type="multipart/form-data",
    )
    duplicate = client.post(
        "/api/admin/templates/import",
        data={"category_id": str(category["id"]), "mockups": [(image_bytes(), "frame.png")]},
        headers=headers,
        content_type="multipart/form-data",
    )

    templates = client.get("/api/admin/templates?product_type=wall-art").get_json()["templates"]
    assert first.status_code == 201
    assert duplicate.status_code == 400
    assert "frame.png" in duplicate.get_json()["error"]
    assert len(templates) == 1


def test_import_rejects_duplicate_batch_without_partial_creation(tmp_path: Path):
    client = build_app(tmp_path).test_client()
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}
    category = client.post(
        "/api/admin/categories", json={"name": "Wall Art"}, headers=headers
    ).get_json()["category"]
    client.post(
        "/api/admin/templates/import",
        data={"category_id": str(category["id"]), "mockups": [(image_bytes(), "frame.png")]},
        headers=headers,
        content_type="multipart/form-data",
    )

    duplicate_batch = client.post(
        "/api/admin/templates/import",
        data={
            "category_id": str(category["id"]),
            "mockups": [
                (image_bytes(), "new-frame.png"),
                (image_bytes(), "frame.png"),
            ],
        },
        headers=headers,
        content_type="multipart/form-data",
    )

    templates = client.get("/api/admin/templates?product_type=wall-art").get_json()["templates"]
    assert duplicate_batch.status_code == 400
    assert "frame.png" in duplicate_batch.get_json()["error"]
    assert [template["source_filename"] for template in templates] == ["frame.png"]


def test_delete_draft_template_removes_record_assets_and_allows_reimport(tmp_path: Path):
    app = build_app(tmp_path)
    client = app.test_client()
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}
    category = client.post(
        "/api/admin/categories", json={"name": "Wall Art"}, headers=headers
    ).get_json()["category"]
    template = client.post(
        "/api/admin/templates/import",
        data={"category_id": str(category["id"]), "mockups": [(image_bytes(), "frame.png")]},
        headers=headers,
        content_type="multipart/form-data",
    ).get_json()["templates"][0]
    draft_folder = Path(app.config["DRAFT_TEMPLATES_FOLDER"]) / template["template_id"]

    deleted = client.delete(
        f"/api/admin/templates/{template['template_id']}", headers=headers
    )
    templates = client.get("/api/admin/templates?product_type=wall-art").get_json()["templates"]
    reimported = client.post(
        "/api/admin/templates/import",
        data={"category_id": str(category["id"]), "mockups": [(image_bytes(), "frame.png")]},
        headers=headers,
        content_type="multipart/form-data",
    )

    assert deleted.status_code == 200
    assert templates == []
    assert not draft_folder.exists()
    assert reimported.status_code == 201


def test_delete_active_template_removes_public_template_assets(tmp_path: Path):
    app = build_app(tmp_path)
    client = app.test_client()
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}
    category = client.post(
        "/api/admin/categories", json={"name": "Wall Art"}, headers=headers
    ).get_json()["category"]
    template = client.post(
        "/api/admin/templates/import",
        data={"category_id": str(category["id"]), "mockups": [(image_bytes(), "frame.png")]},
        headers=headers,
        content_type="multipart/form-data",
    ).get_json()["templates"][0]
    template_id = template["template_id"]
    client.post(f"/api/admin/templates/{template_id}/activate", headers=headers)
    public_folder = Path(app.config["TEMPLATES_FOLDER"]) / template_id

    deleted = client.delete(f"/api/admin/templates/{template_id}", headers=headers)
    admin_templates = client.get("/api/admin/templates?product_type=wall-art").get_json()[
        "templates"
    ]
    public_templates = client.get("/api/mockups/templates?product_type=wall-art").get_json()

    assert deleted.status_code == 200
    assert admin_templates == []
    assert public_templates == []
    assert not public_folder.exists()


def test_rename_category_updates_name_and_slug(tmp_path: Path):
    client = build_app(tmp_path).test_client()
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}
    category = client.post(
        "/api/admin/categories", json={"name": "Wall Art"}, headers=headers
    ).get_json()["category"]

    response = client.patch(
        f"/api/admin/categories/{category['id']}",
        json={"name": "Fine Art Prints"},
        headers=headers,
    )
    categories = client.get("/api/admin/categories").get_json()["categories"]

    assert response.status_code == 200
    assert response.get_json()["category"]["name"] == "Fine Art Prints"
    assert response.get_json()["category"]["slug"] == "fine-art-prints"
    assert categories[0]["name"] == "Fine Art Prints"
    assert categories[0]["slug"] == "fine-art-prints"


def test_delete_empty_category_removes_it(tmp_path: Path):
    client = build_app(tmp_path).test_client()
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}
    category = client.post(
        "/api/admin/categories", json={"name": "Empty Category"}, headers=headers
    ).get_json()["category"]

    response = client.delete(f"/api/admin/categories/{category['id']}", headers=headers)
    categories = client.get("/api/admin/categories").get_json()["categories"]

    assert response.status_code == 200
    assert response.get_json()["category_id"] == category["id"]
    assert categories == []


def test_delete_category_with_templates_is_rejected(tmp_path: Path):
    client = build_app(tmp_path).test_client()
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}
    category = client.post(
        "/api/admin/categories", json={"name": "Wall Art"}, headers=headers
    ).get_json()["category"]
    client.post(
        "/api/admin/templates/import",
        data={"category_id": str(category["id"]), "mockups": [(image_bytes(), "frame.png")]},
        headers=headers,
        content_type="multipart/form-data",
    )

    response = client.delete(f"/api/admin/categories/{category['id']}", headers=headers)
    categories = client.get("/api/admin/categories").get_json()["categories"]

    assert response.status_code == 400
    assert response.get_json()["error"] == "Only empty categories can be deleted"
    assert categories[0]["name"] == "Wall Art"
    assert categories[0]["template_count"] == 1


def test_reactivating_existing_active_template_publishes_new_reviewed_area(tmp_path: Path):
    app = build_app(tmp_path)
    client = app.test_client()
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}
    category = client.post(
        "/api/admin/categories", json={"name": "Wall Art"}, headers=headers
    ).get_json()["category"]
    template = client.post(
        "/api/admin/templates/import",
        data={"category_id": str(category["id"]), "mockups": [(image_bytes(), "frame.png")]},
        headers=headers,
        content_type="multipart/form-data",
    ).get_json()["templates"][0]
    template_id = template["template_id"]
    first_area = {"x": 100, "y": 100, "width": 200, "height": 300}
    second_area = {"x": 120, "y": 130, "width": 230, "height": 330}
    client.patch(
        f"/api/admin/templates/{template_id}",
        json={"artwork_area": first_area},
        headers=headers,
    )
    client.post(f"/api/admin/templates/{template_id}/activate", headers=headers)
    shutil.rmtree(Path(app.config["DRAFT_TEMPLATES_FOLDER"]) / template_id)
    client.patch(
        f"/api/admin/templates/{template_id}",
        json={"artwork_area": second_area},
        headers=headers,
    )

    response = client.post(f"/api/admin/templates/{template_id}/activate", headers=headers)

    assert response.status_code == 200
    manifest_path = Path(app.config["TEMPLATES_FOLDER"]) / template_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    assert manifest["artwork_area"] == second_area


def test_ai_detection_is_a_preview_until_admin_saves_or_approves(
    tmp_path: Path, monkeypatch
):
    app = build_app(tmp_path)
    client = app.test_client()
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}
    category = client.post(
        "/api/admin/categories", json={"name": "Wall Art"}, headers=headers
    ).get_json()["category"]
    template_id = client.post(
        "/api/admin/templates/import",
        data={"category_id": str(category["id"]), "mockups": [(image_bytes(), "frame.png")]},
        headers=headers,
        content_type="multipart/form-data",
    ).get_json()["templates"][0]["template_id"]
    approved_area = {"x": 100, "y": 100, "width": 200, "height": 300}
    proposed_area = {"x": 120, "y": 130, "width": 230, "height": 330}
    client.patch(
        f"/api/admin/templates/{template_id}",
        json={"artwork_area": approved_area},
        headers=headers,
    )
    client.post(f"/api/admin/templates/{template_id}/activate", headers=headers)

    class ProposedProvider:
        def detect(self, _background):
            return DetectionProposal(
                artwork_area=proposed_area,
                confidence=0.93,
                reason="detected proposal",
                provider="vertex",
            )

    monkeypatch.setattr("routes.admin_routes.build_provider", lambda *_args: ProposedProvider())

    detection = client.post(f"/api/admin/templates/{template_id}/detect", headers=headers)
    stored = client.get("/api/admin/templates?product_type=wall-art").get_json()["templates"][0]

    assert detection.status_code == 200
    assert detection.get_json()["template"]["artwork_area"] == proposed_area
    assert stored["artwork_area"] == approved_area


def test_draft_ai_detection_is_saved_immediately(tmp_path: Path, monkeypatch):
    app = build_app(tmp_path)
    client = app.test_client()
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}
    category = client.post(
        "/api/admin/categories", json={"name": "Wall Art"}, headers=headers
    ).get_json()["category"]
    template_id = client.post(
        "/api/admin/templates/import",
        data={"category_id": str(category["id"]), "mockups": [(image_bytes(), "frame.png")]},
        headers=headers,
        content_type="multipart/form-data",
    ).get_json()["templates"][0]["template_id"]
    proposed_area = {"x": 120, "y": 130, "width": 230, "height": 330}

    class ProposedProvider:
        def detect(self, _background):
            return DetectionProposal(
                artwork_area=proposed_area,
                confidence=0.93,
                reason="detected proposal",
                provider="vertex",
            )

    monkeypatch.setattr("routes.admin_routes.build_provider", lambda *_args: ProposedProvider())

    detection = client.post(f"/api/admin/templates/{template_id}/detect", headers=headers)
    stored = client.get("/api/admin/templates?product_type=wall-art").get_json()["templates"][0]

    assert detection.status_code == 200
    assert detection.get_json()["template"]["artwork_area"] == proposed_area
    assert stored["artwork_area"] == proposed_area


def test_reset_admin_template_detection(tmp_path: Path):
    app = build_app(tmp_path)
    client = app.test_client()
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}
    category = client.post(
        "/api/admin/categories", json={"name": "Wall Art"}, headers=headers
    ).get_json()["category"]
    template_id = client.post(
        "/api/admin/templates/import",
        data={"category_id": str(category["id"]), "mockups": [(image_bytes(), "frame.png")]},
        headers=headers,
        content_type="multipart/form-data",
    ).get_json()["templates"][0]["template_id"]

    mask_file = tmp_path / "draft_templates" / template_id / "mask.png"
    mask_file.write_text("dummy mask", encoding="utf-8")
    assert mask_file.is_file()

    client.patch(
        f"/api/admin/templates/{template_id}",
        json={
            "raw_artwork_area": {"regions": [{"x": 10, "y": 10, "width": 50, "height": 50}]},
            "mask_name": "mask.png",
            "detection_provider": "vertex",
            "detection_confidence": 0.95,
        },
        headers=headers,
    )

    res = client.post(f"/api/admin/templates/{template_id}/reset-detection", headers=headers)
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    tpl = data["template"]
    # Reset clears detection outright; it must not leave a fresh one behind.
    assert tpl["raw_artwork_area"] is None
    assert tpl["mask_name"] is None
    assert tpl["detection_provider"] is None
    assert tpl["detection_confidence"] is None
    assert not mask_file.is_file()





def test_green_frames_is_its_own_classic_submode(tmp_path: Path):
    """Green frames and colour pick are separate choices.

    Colour pick samples whatever colour the admin clicks, which is the tool for
    frames that are not green; green frames runs the fixed chroma pass that the
    studio relied on before the submodes existed.
    """
    client = build_app(tmp_path).test_client()
    csrf = login(client)

    saved = client.put(
        "/api/admin/settings/detection",
        json={"DETECTION_PROVIDER": "classic", "CLASSIC_SUBMODE": "green_frames"},
        headers={"X-CSRF-Token": csrf},
    )

    assert saved.status_code == 200
    settings = client.get("/api/admin/settings/detection").get_json()["settings"]
    assert settings["CLASSIC_SUBMODE"] == "green_frames"

    page = client.get("/admin").data
    for submode in (b"auto", b"frame_points", b"green_frames", b"color_pick"):
        assert b'data-submode="' + submode + b'"' in page


def test_editing_a_published_template_leaves_its_manifest_alone(tmp_path: Path):
    """The catalog holds a template's live state; the manifest is its snapshot.

    Every edit used to rewrite manifest.json, which put a tracked file change in
    the working tree behind every rename, drag and slider in the admin. The
    manifest is written when a template is published and left alone after that,
    and every reader lays the catalog over it.
    """
    app = build_app(tmp_path)
    client = app.test_client()
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}

    category = client.post(
        "/api/admin/categories", json={"name": "Wall Art"}, headers=headers
    ).get_json()["category"]
    imported = client.post(
        "/api/admin/templates/import",
        data={"category_id": str(category["id"]), "mockups": [(image_bytes(), "living-room.png")]},
        headers=headers,
        content_type="multipart/form-data",
    )
    template_id = imported.get_json()["templates"][0]["template_id"]
    assert client.post(f"/api/admin/templates/{template_id}/activate", headers=headers).status_code == 200

    manifest_path = Path(app.config["TEMPLATES_FOLDER"]) / template_id / "manifest.json"
    published = manifest_path.read_bytes()
    published_name = json.loads(published.decode("utf-8"))["name"]

    renamed = client.patch(
        f"/api/admin/templates/{template_id}",
        json={
            "name": "V1-7",
            "artwork_area": {"x": 12, "y": 14, "width": 260, "height": 380},
            "fit_mode": "contain",
        },
        headers=headers,
    )
    assert renamed.status_code == 200

    # Nothing on disk moved...
    assert manifest_path.read_bytes() == published
    assert json.loads(manifest_path.read_text("utf-8"))["name"] == published_name

    # ...and every reader still reports the edit.
    listed = client.get("/api/mockups/templates").get_json()
    assert [entry["name"] for entry in listed if entry["template_id"] == template_id] == ["V1-7"]
    detail = client.get(f"/api/mockups/templates/{template_id}").get_json()
    assert detail["name"] == "V1-7"
    assert detail["fit_mode"] == "contain"
    assert detail["frames"][0]["width"] == 260


def test_published_asset_path_stays_inside_the_template_folder(tmp_path: Path):
    """The id and the asset name both arrive from the URL.

    Either one climbing out of the template's folder would serve any file the
    server can read -- .env with the secret key and the admin password among
    them -- so both are held to a single path segment and the resolved file has
    to sit inside the folder it was asked for.
    """
    from routes.admin_routes import published_asset_path

    templates = tmp_path / "templates"
    (templates / "template_ok").mkdir(parents=True)
    (templates / "template_ok" / "preview.png").write_bytes(b"not really a png")
    secret = tmp_path / ".env"
    secret.write_text("SECRET_KEY=hunter2", encoding="utf-8")

    served = published_asset_path(templates, "template_ok", "preview.png")
    assert served is not None and served.name == "preview.png"

    for asset_name in ("../.env", "..", "../../.env", "nested/preview.png", str(secret)):
        assert published_asset_path(templates, "template_ok", asset_name) is None, asset_name
    for template_id in ("..", "../..", "template_ok/.."):
        assert published_asset_path(templates, template_id, ".env") is None, template_id

    # A name that resolves inside the folder but is not there is not served.
    assert published_asset_path(templates, "template_ok", "background.png") is None


def test_template_asset_route_does_not_serve_files_outside_the_template(tmp_path: Path):
    client = build_app(tmp_path).test_client()
    login(client)

    templates = Path(client.application.config["TEMPLATES_FOLDER"])
    (templates / "template_ok").mkdir(parents=True, exist_ok=True)
    (templates / "template_ok" / "preview.png").write_bytes(b"not really a png")
    (templates.parent / "secret.txt").write_text("SECRET_KEY=hunter2", encoding="utf-8")

    assert client.get("/api/admin/templates/template_ok/asset/preview.png").status_code == 200
    for attempt in (
        "/api/admin/templates/template_ok/asset/..%2F..%2Fsecret.txt",
        "/api/admin/templates/template_ok/asset/..",
        "/api/admin/templates/..%2F..%2Fsecret.txt/asset/preview.png",
    ):
        response = client.get(attempt)
        assert response.status_code in (301, 308, 400, 404), attempt
        assert b"hunter2" not in response.data, attempt


def test_login_stops_answering_after_ten_wrong_passwords(tmp_path: Path):
    """Guessing is the only attack a password login invites.

    Ten tries from one address in five minutes is more than a person needs; the
    eleventh is turned away without the password being checked at all, and a
    login that succeeds clears the count behind it.
    """
    from routes import admin_routes

    admin_routes._login_attempts.clear()
    try:
        client = build_app(tmp_path).test_client()

        for attempt in range(admin_routes.LOGIN_MAX_ATTEMPTS):
            response = client.post("/api/admin/login", json={"password": "wrong"})
            assert response.status_code == 401, attempt

        blocked = client.post("/api/admin/login", json={"password": "wrong"})
        assert blocked.status_code == 429
        # The right password is not a way past the limit either.
        assert client.post("/api/admin/login", json={"password": "admin-pass"}).status_code == 429

        # A successful login wipes the slate, so a fumbled password earlier in
        # the day cannot lock the studio out later.
        admin_routes._login_attempts.clear()
        assert client.post("/api/admin/login", json={"password": "wrong"}).status_code == 401
        assert client.post("/api/admin/login", json={"password": "admin-pass"}).status_code == 200
        assert admin_routes._login_attempts == {}
    finally:
        admin_routes._login_attempts.clear()


def test_server_refuses_to_start_on_the_published_default_secret_key(tmp_path: Path):
    """The default key is printed in this repository.

    A server signing session cookies with it is a server whose admin session
    anyone can forge, so it does not start -- unless it is a local debug or test
    run, where the convenience costs nothing.
    """
    import pytest

    from config import Config

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app(
            {
                "SECRET_KEY": Config.DEFAULT_SECRET_KEY,
                "DATABASE_PATH": str(tmp_path / "data" / "catalog.sqlite3"),
                "DRAFT_TEMPLATES_FOLDER": str(tmp_path / "draft_templates"),
                "TEMPLATES_FOLDER": str(tmp_path / "templates"),
                "UPLOAD_FOLDER": str(tmp_path / "uploads"),
                "OUTPUT_FOLDER": str(tmp_path / "outputs"),
            }
        )

    # Testing runs are exempt, which is why every other test here still builds.
    app = build_app(tmp_path)
    assert app.config["SECRET_KEY"] == "test-secret"


def test_requests_have_a_size_ceiling_by_default(tmp_path: Path):
    """Unbounded uploads are read into memory whatever their size."""
    import config

    assert config.DEFAULT_MAX_CONTENT_LENGTH == 32 * 1024 * 1024
    assert build_app(tmp_path).config["MAX_CONTENT_LENGTH"] == config.DEFAULT_MAX_CONTENT_LENGTH


def test_missing_mask_file_is_drawn_from_the_template_own_frames(tmp_path: Path):
    """A template can name a mask.png that was never written beside it.

    The renderer already draws that mask from the frames the template carries;
    the editor asking the server for the file was the only thing left with
    nothing to show -- an unclipped overlay and a 404 in the console. The same
    drawing is served here, so both see the same opening.
    """
    from PIL import Image

    client = build_app(tmp_path).test_client()
    csrf = login(client)

    response = client.post(
        "/api/admin/categories", json={"name": "Wall Art"}, headers={"X-CSRF-Token": csrf}
    )
    category_id = response.get_json()["category"]["id"]
    upload = client.post(
        "/api/admin/templates/import",
        data={"category_id": str(category_id), "mockups": [(image_bytes((900, 900)), "green.png")]},
        content_type="multipart/form-data",
        headers={"X-CSRF-Token": csrf},
    )
    assert upload.status_code in (200, 201)
    template_id = upload.get_json()["templates"][0]["template_id"]
    published = client.post(
        f"/api/admin/templates/{template_id}/activate",
        json={},
        headers={"X-CSRF-Token": csrf},
    )
    assert published.status_code == 200

    # A published template that names a mask file which is not on disk.
    catalog = client.application.extensions["catalog_service"]
    catalog.update_template(
        template_id,
        {
            "mask_name": "mask.png",
            "raw_artwork_area": {
                "mode": "green_frames_mockups",
                "regions": [
                    {
                        "corners": [
                            {"x": 100, "y": 150},
                            {"x": 500, "y": 150},
                            {"x": 500, "y": 600},
                            {"x": 100, "y": 600},
                        ]
                    }
                ],
            },
        },
    )
    templates_folder = Path(client.application.config["TEMPLATES_FOLDER"])
    assert not (templates_folder / template_id / "mask.png").exists()

    served = client.get(f"/api/admin/templates/{template_id}/asset/mask.png")
    assert served.status_code == 200
    assert served.mimetype == "image/png"

    mask = Image.open(io.BytesIO(served.data)).convert("L")
    assert mask.size == (900, 900)
    # getbbox is exclusive on the right and bottom edge.
    box = mask.point(lambda value: 255 if value > 127 else 0).getbbox()
    assert box == (100, 150, 501, 601)

    # A template with no frames to draw from still says the asset is missing.
    catalog.update_template(template_id, {"raw_artwork_area": None})
    assert client.get(f"/api/admin/templates/{template_id}/asset/mask.png").status_code == 404
    assert client.get(f"/api/admin/templates/{template_id}/asset/nothing.png").status_code == 404


def test_batch_detect_runs_in_one_pool_for_the_whole_process(tmp_path: Path):
    """A pool per request has no ceiling worth the name.

    Two batches arriving together used to put ten threads and ten provider
    calls in flight where the limit was meant to be five; the AI provider's
    quota is counted per project, not per request. Every batch now queues
    against the pool the app was built with.
    """
    from concurrent.futures import ThreadPoolExecutor

    app = build_app(tmp_path)
    pool = app.extensions["detection_pool"]
    assert isinstance(pool, ThreadPoolExecutor)
    assert pool._max_workers == app.config["DETECTION_MAX_WORKERS"]

    calls: list[int] = []

    class RecordingPool:
        def map(self, function, items):
            items = list(items)
            calls.append(len(items))
            return [function(item) for item in items]

    app.extensions["detection_pool"] = RecordingPool()

    client = app.test_client()
    csrf = login(client)
    category = client.post(
        "/api/admin/categories", json={"name": "Wall Art"}, headers={"X-CSRF-Token": csrf}
    ).get_json()["category"]
    upload = client.post(
        "/api/admin/templates/import",
        data={"category_id": str(category["id"]), "mockups": [(image_bytes(), "wall.png")]},
        content_type="multipart/form-data",
        headers={"X-CSRF-Token": csrf},
    )
    template_id = upload.get_json()["templates"][0]["template_id"]

    response = client.post(
        "/api/admin/templates/batch-detect",
        json={"template_ids": [template_id, template_id]},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert len(response.get_json()["results"]) == 2
    # The route reached for the shared pool rather than building its own.
    assert calls == [2]

    # Source-level: nothing in the route constructs a pool of its own any more.
    route_source = Path("routes/admin_routes.py").read_text(encoding="utf-8")
    batch = route_source.split("def batch_detect_admin_templates():", 1)[1].split("@admin_routes", 1)[0]
    assert "ThreadPoolExecutor(" not in batch
    assert "detection_pool()" in batch


def test_green_detection_tolerance_is_a_setting_anyone_can_change(tmp_path: Path):
    """How strict DETECT FRAME is about green belongs to whoever runs the studio.

    It was a number in the source before, which meant a mockup whose screen
    photographed dull could only be fixed by editing frames by hand. It is now
    saved with the other engine settings, reaches the detector, and is refused
    outside the range the colour space allows.
    """
    import config
    from services.detection_service import build_provider

    assert config.Config.CLASSIC_GREEN_TOLERANCE == 130

    client = build_app(tmp_path).test_client()
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}

    saved = client.put(
        "/api/admin/settings/detection",
        json={"DETECTION_PROVIDER": "classic", "CLASSIC_GREEN_TOLERANCE": "160"},
        headers=headers,
    )
    assert saved.status_code == 200
    settings = client.get("/api/admin/settings/detection").get_json()["settings"]
    assert settings["CLASSIC_GREEN_TOLERANCE"] == "160"

    # It reaches the detector rather than sitting in the table.
    provider = build_provider(settings, client.application.config)
    assert provider.green_tolerance == 160
    assert provider._green_settings().tolerance == 160

    # ...and the default is what a provider gets with nothing saved.
    assert build_provider({}, client.application.config).green_tolerance == 130

    for bad in ("9", "500", "not a number"):
        rejected = client.put(
            "/api/admin/settings/detection",
            json={"DETECTION_PROVIDER": "classic", "CLASSIC_GREEN_TOLERANCE": bad},
            headers=headers,
        )
        assert rejected.status_code == 400, bad


def test_a_blank_control_cannot_sink_the_whole_settings_save(tmp_path: Path):
    """One control that could not show its value used to fail the entire panel.

    The classic-mode select had no option for the green-frames submode, so with
    that mode active it rendered blank and sent "" -- and an empty submode was
    rejected as an unsupported one, taking the tolerance and everything else on
    the panel down with it. A blank is now nothing to save rather than a bad
    value, and the option it was missing is there.
    """
    client = build_app(tmp_path).test_client()
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}

    client.put(
        "/api/admin/settings/detection",
        json={"DETECTION_PROVIDER": "classic", "CLASSIC_SUBMODE": "green_frames"},
        headers=headers,
    )

    response = client.put(
        "/api/admin/settings/detection",
        json={
            "DETECTION_PROVIDER": "classic",
            "CLASSIC_SUBMODE": "",
            "CLASSIC_INTERNAL_MODE": "",
            "CLASSIC_GREEN_TOLERANCE": "160",
        },
        headers=headers,
    )
    assert response.status_code == 200
    settings = client.get("/api/admin/settings/detection").get_json()["settings"]
    assert settings["CLASSIC_GREEN_TOLERANCE"] == "160"
    # The blank was not stored over the mode that was already there.
    assert settings["CLASSIC_SUBMODE"] == "green_frames"

    # A real value that is not a mode is still refused.
    assert client.put(
        "/api/admin/settings/detection",
        json={"DETECTION_PROVIDER": "classic", "CLASSIC_SUBMODE": "sideways"},
        headers=headers,
    ).status_code == 400

    # None is a choice that saves, not an empty value that is dropped.
    assert client.put(
        "/api/admin/settings/detection",
        json={"DETECTION_PROVIDER": "classic", "CLASSIC_SUBMODE": "none"},
        headers=headers,
    ).status_code == 200
    assert client.get("/api/admin/settings/detection").get_json()["settings"]["CLASSIC_SUBMODE"] == "none"


def test_detection_on_new_mockups_is_its_own_setting(tmp_path: Path):
    """Adding mockups and working on one are different moments.

    The panel used to set the mode the studio was working in, which meant
    choosing a default for imports changed what the top bar was doing right
    then. It now says only what runs by itself when a mockup is added -- and
    "none" means the mockup comes in and the frames are left to you, which is
    the whole point of having the choice.
    """
    import config

    assert config.Config.CLASSIC_IMPORT_MODE == "auto"

    app = build_app(tmp_path)
    client = app.test_client()
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}
    category = client.post(
        "/api/admin/categories", json={"name": "Wall Art"}, headers=headers
    ).get_json()["category"]

    def upload(name: str):
        return client.post(
            "/api/admin/templates/import",
            data={"category_id": str(category["id"]), "mockups": [(image_bytes(), name)]},
            content_type="multipart/form-data",
            headers=headers,
        ).get_json()["templates"][0]

    # By default a new mockup is detected on the way in.
    detected = upload("detected.png")
    assert detected["artwork_area"] is not None
    assert detected["detection_provider"]

    # Told not to, the import stops at importing.
    assert client.put(
        "/api/admin/settings/detection",
        json={"DETECTION_PROVIDER": "classic", "CLASSIC_IMPORT_MODE": "none"},
        headers=headers,
    ).status_code == 200
    untouched = upload("untouched.png")
    assert untouched["artwork_area"] is None
    assert untouched["status"] == "draft"

    # Choosing it leaves the mode the studio is working in exactly where it was.
    client.put(
        "/api/admin/settings/detection",
        json={"DETECTION_PROVIDER": "classic", "CLASSIC_SUBMODE": "green_frames"},
        headers=headers,
    )
    client.put(
        "/api/admin/settings/detection",
        json={"DETECTION_PROVIDER": "classic", "CLASSIC_IMPORT_MODE": "auto"},
        headers=headers,
    )
    settings = client.get("/api/admin/settings/detection").get_json()["settings"]
    assert settings["CLASSIC_SUBMODE"] == "green_frames"
    assert settings["CLASSIC_IMPORT_MODE"] == "auto"

    # Only what can run unattended is offered.
    assert client.put(
        "/api/admin/settings/detection",
        json={"DETECTION_PROVIDER": "classic", "CLASSIC_IMPORT_MODE": "color_pick"},
        headers=headers,
    ).status_code == 400

    html = (SERVER_ROOT / "templates" / "admin" / "index.html").read_text(encoding="utf-8")
    select = html.split('<select id="classicImportMode"', 1)[1].split("</select>", 1)[0]
    for mode in ("none", "auto", "green_frames"):
        assert f'value="{mode}"' in select, mode
    assert 'value="color_pick"' not in select


def test_the_requirements_list_what_the_app_imports():
    """A missing dependency is a container that builds and then falls over.

    The list had drifted: numpy and OpenCV are imported at the top of the
    detection services and neither was in it, so a clean install ran until the
    first detection and stopped. This walks the imports and asks for each one.
    """
    import ast

    distribution_for = {
        "flask": "Flask",
        "flask_cors": "Flask-Cors",
        "PIL": "Pillow",
        "dotenv": "python-dotenv",
        "numpy": "numpy",
        "cv2": "opencv-python-headless",
        "scipy": "scipy",
        "psutil": "psutil",
        "httpx": "httpx",
        "requests": "requests",
        "waitress": "waitress",
        "google": "google-genai",
    }
    optional = {"ultralytics", "torch"}   # requirements-ml.txt

    def packages(name: str) -> str:
        # The comments in these files talk about packages they deliberately do
        # not install, so only the requirement lines count.
        lines = (SERVER_ROOT / name).read_text(encoding="utf-8").splitlines()
        return chr(10).join(
            line for line in lines if line.strip() and not line.startswith("#")
        ).lower()

    requirements = packages("requirements.txt")
    imported: set[str] = set()
    for folder in ("services", "routes"):
        for path in (SERVER_ROOT / folder).glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imported.add(node.module.split(".")[0])

    missing = []
    for name in sorted(imported & set(distribution_for)):
        if distribution_for[name].lower() not in requirements:
            missing.append(f"{name} (install {distribution_for[name]})")
    assert not missing, f"imported but not in requirements.txt: {missing}"

    # The heavy optional stack stays out of the runtime list.
    for name in optional:
        assert name not in requirements
        assert name in packages("requirements-ml.txt")


def test_the_container_and_ci_describe_a_server_that_boots():
    """The deployment files are checked in, and say what they should."""
    dockerfile = (SERVER_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert 'CMD ["python", "run_server.py"]' in dockerfile
    assert "HEALTHCHECK" in dockerfile and "/api/health" in dockerfile
    assert "USER studio" in dockerfile           # not root
    assert "opencv" not in dockerfile            # dependencies come from the list

    ignored = (SERVER_ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    for folder in ("data", "templates_data", "draft_templates", "outputs", "models"):
        assert folder in ignored, folder        # volumes, not image content

    workflow = (SERVER_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "ruff check ." in workflow
    assert "pytest tests/ -q" in workflow
    assert "docker build" in workflow and "/api/health" in workflow
