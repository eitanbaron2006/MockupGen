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
    assert b"Green frames mockups" in admin_page.data
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
