import hmac
import secrets
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from flask import (
    Blueprint,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from services.catalog_service import CatalogError, CatalogService, orientation_for_size
from services.classic_detection_service import ClassicDetectionProvider
from services.detection_service import DetectionError, build_provider, validate_proposal
from services.local_detection_service import discover_local_models
from services.vertex_model_service import (
    FALLBACK_VERTEX_DETECTION_MODELS,
    list_vertex_detection_models,
)
from services.template_import_service import (
    TemplateImportError,
    draft_asset_path,
    import_backgrounds,
    publish_template,
)


admin_routes = Blueprint("admin_routes", __name__)
SETTINGS_KEYS = {
    "DETECTION_PROVIDER",
    "VERTEX_PROJECT_ID",
    "VERTEX_LOCATION",
    "VERTEX_MODEL",
    "VERTEX_MEDIA_RESOLUTION",
    "VERTEX_AUTH_MODE",
    "DETECTION_REFINEMENT",
    "LOCAL_DETECTION_URL",
    "LOCAL_DETECTION_MODEL",
}
def catalog() -> CatalogService:
    return current_app.extensions["catalog_service"]


def json_error(message: str, status: int):
    return jsonify({"success": False, "error": message}), status


def is_admin() -> bool:
    return bool(session.get("admin_authenticated"))


def require_admin_json(handler: Callable):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        if not is_admin():
            return json_error("Admin authentication required", 401)
        return handler(*args, **kwargs)

    return wrapped


def require_csrf(handler: Callable):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        expected = session.get("csrf_token")
        submitted = request.headers.get("X-CSRF-Token") or request.form.get("_csrf")
        if not expected or not submitted or not hmac.compare_digest(expected, submitted):
            return json_error("Invalid CSRF token", 403)
        return handler(*args, **kwargs)

    return wrapped


@admin_routes.get("/admin/login")
def admin_login_page():
    return render_template("admin/login.html")


@admin_routes.get("/admin")
def admin_page():
    if not is_admin():
        return redirect(url_for("admin_routes.admin_login_page"))
    return render_template("admin/index.html", csrf_token=session["csrf_token"])


@admin_routes.post("/api/admin/login")
def admin_login():
    configured = str(current_app.config.get("ADMIN_PASSWORD", ""))
    if not configured:
        return json_error("ADMIN_PASSWORD is not configured in .env", 503)
    supplied = str((request.get_json(silent=True) or {}).get("password", ""))
    if not hmac.compare_digest(configured, supplied):
        return json_error("Incorrect password", 401)
    session.clear()
    session["admin_authenticated"] = True
    session["csrf_token"] = secrets.token_urlsafe(32)
    return jsonify({"success": True, "csrf_token": session["csrf_token"]})


@admin_routes.post("/api/admin/logout")
@require_admin_json
@require_csrf
def admin_logout():
    session.clear()
    return jsonify({"success": True})


@admin_routes.get("/api/admin/categories")
@require_admin_json
def get_admin_categories():
    return jsonify({"categories": catalog().list_categories()})


@admin_routes.post("/api/admin/categories")
@require_admin_json
@require_csrf
def create_admin_category():
    try:
        category = catalog().create_category(
            str((request.get_json(silent=True) or {}).get("name", ""))
        )
    except CatalogError as error:
        return json_error(str(error), 400)
    return jsonify({"success": True, "category": category}), 201


@admin_routes.get("/api/admin/templates")
@require_admin_json
def get_admin_templates():
    templates = catalog().list_templates(
        category_slug=request.args.get("product_type") or None,
        status=request.args.get("status") or None,
    )
    return jsonify({"templates": templates})


@admin_routes.post("/api/admin/templates/import")
@require_admin_json
@require_csrf
def import_admin_templates():
    try:
        category_id = int(request.form.get("category_id", ""))
        templates = import_backgrounds(
            request.files.getlist("mockups"),
            category_id=category_id,
            drafts_folder=Path(current_app.config["DRAFT_TEMPLATES_FOLDER"]),
            catalog=catalog(),
        )
        detector = ClassicDetectionProvider()
        detected_templates = []
        for template in templates:
            if template["status"] == "active":
                detected_templates.append(template)
                continue
            proposal = detector.detect(
                draft_asset_path(
                    Path(current_app.config["DRAFT_TEMPLATES_FOLDER"]),
                    template["template_id"],
                    "background.png",
                )
            )
            detected_templates.append(
                catalog().update_template(
                    template["template_id"],
                    {
                        "artwork_area": proposal.artwork_area,
                        "orientation": orientation_for_size(
                            proposal.artwork_area["width"],
                            proposal.artwork_area["height"],
                        ),
                        "detection_provider": proposal.provider,
                        "detection_confidence": proposal.confidence,
                    },
                )
            )
        templates = detected_templates
    except (ValueError, TemplateImportError) as error:
        return json_error(str(error) or "Category is required", 400)
    except DetectionError as error:
        return json_error(str(error), 422)
    return jsonify({"success": True, "templates": templates}), 201


@admin_routes.get("/api/admin/templates/<template_id>/asset/<asset_name>")
@require_admin_json
def admin_template_asset(template_id: str, asset_name: str):
    try:
        asset_path = draft_asset_path(
            Path(current_app.config["DRAFT_TEMPLATES_FOLDER"]), template_id, asset_name
        )
    except TemplateImportError:
        active_asset = Path(current_app.config["TEMPLATES_FOLDER"]) / template_id / asset_name
        if not active_asset.is_file():
            return json_error("Asset not found", 404)
        asset_path = active_asset
    return send_file(asset_path)


@admin_routes.patch("/api/admin/templates/<template_id>")
@require_admin_json
@require_csrf
def update_admin_template(template_id: str):
    template = catalog().get_template(template_id)
    if not template:
        return json_error("Template not found", 404)
    payload = request.get_json(silent=True) or {}
    changes: dict[str, Any] = {}
    try:
        if "artwork_area" in payload:
            proposal = validate_proposal(
                {"artwork_area": payload["artwork_area"]},
                image_width=template["canvas_width"],
                image_height=template["canvas_height"],
                provider="manual",
            )
            changes["artwork_area"] = proposal.artwork_area
            changes["orientation"] = orientation_for_size(
                proposal.artwork_area["width"], proposal.artwork_area["height"]
            )
        if "name" in payload:
            changes["name"] = str(payload["name"]).strip() or template["name"]
        if payload.get("fit_mode") in {"cover", "contain", "stretch"}:
            changes["fit_mode"] = payload["fit_mode"]
        if "category_id" in payload:
            changes["category_id"] = int(payload["category_id"])
        updated = catalog().update_template(template_id, changes)
    except (ValueError, CatalogError, DetectionError) as error:
        return json_error(str(error), 400)
    return jsonify({"success": True, "template": updated})


@admin_routes.post("/api/admin/templates/<template_id>/detect")
@require_admin_json
@require_csrf
def detect_admin_template(template_id: str):
    template = catalog().get_template(template_id)
    if not template:
        return json_error("Template not found", 404)
    try:
        background = draft_asset_path(
            Path(current_app.config["DRAFT_TEMPLATES_FOLDER"]), template_id, "background.png"
        )
    except TemplateImportError:
        background = Path(current_app.config["TEMPLATES_FOLDER"]) / template_id / "background.png"
    try:
        provider = build_provider(catalog().get_settings(), current_app.config)
        proposal = provider.detect(background)
        preview = {
            **template,
            "artwork_area": proposal.artwork_area,
            "orientation": orientation_for_size(
                proposal.artwork_area["width"], proposal.artwork_area["height"]
            ),
            "detection_provider": proposal.provider,
            "detection_confidence": proposal.confidence,
        }
    except DetectionError as error:
        return json_error(str(error), 422)
    return jsonify(
        {
            "success": True,
            "template": preview,
            "proposal": {
                "artwork_area": proposal.artwork_area,
                "confidence": proposal.confidence,
                "reason": proposal.reason,
                "provider": proposal.provider,
            },
        }
    )


def background_for_template(template_id: str) -> Path:
    try:
        return draft_asset_path(
            Path(current_app.config["DRAFT_TEMPLATES_FOLDER"]), template_id, "background.png"
        )
    except TemplateImportError:
        return Path(current_app.config["TEMPLATES_FOLDER"]) / template_id / "background.png"


@admin_routes.post("/api/admin/templates/<template_id>/activate")
@require_admin_json
@require_csrf
def activate_admin_template(template_id: str):
    try:
        template = publish_template(
            template_id,
            catalog=catalog(),
            drafts_folder=Path(current_app.config["DRAFT_TEMPLATES_FOLDER"]),
            templates_folder=Path(current_app.config["TEMPLATES_FOLDER"]),
        )
    except TemplateImportError as error:
        return json_error(str(error), 400)
    return jsonify({"success": True, "template": template})


@admin_routes.get("/api/admin/settings/detection")
@require_admin_json
def get_detection_settings():
    stored = catalog().get_settings()
    settings = {
        key: stored.get(key, str(current_app.config.get(key, "")))
        for key in SETTINGS_KEYS
    }
    return jsonify({"settings": settings})


@admin_routes.get("/api/admin/settings/detection/models")
@require_admin_json
def get_detection_models():
    provider = (request.args.get("provider") or "classic").strip().lower()
    if provider == "vertex":
        try:
            models = list_vertex_detection_models()
            source = "vertex-model-garden"
        except Exception:
            models = FALLBACK_VERTEX_DETECTION_MODELS
            source = "fallback"
        return jsonify({"provider": provider, "models": models, "source": source})
    if provider == "local":
        endpoint = request.args.get("endpoint", "").strip() or catalog().get_settings().get(
            "LOCAL_DETECTION_URL", str(current_app.config.get("LOCAL_DETECTION_URL", ""))
        )
        models = discover_local_models(
            endpoint, api_key=str(current_app.config.get("LOCAL_DETECTION_API_KEY", ""))
        )
        return jsonify({"provider": provider, "models": models})
    if provider == "classic":
        return jsonify({"provider": provider, "models": []})
    return json_error("Unsupported detection provider", 400)


@admin_routes.put("/api/admin/settings/detection")
@require_admin_json
@require_csrf
def update_detection_settings():
    payload = request.get_json(silent=True) or {}
    settings = {key: str(payload[key]).strip() for key in SETTINGS_KEYS if key in payload}
    if settings.get("DETECTION_PROVIDER") not in {None, "classic", "vertex", "local"}:
        return json_error("Unsupported detection provider", 400)
    if settings.get("VERTEX_MODEL") == "gemini-3-flash-preview":
        settings["VERTEX_LOCATION"] = "global"
    if settings.get("VERTEX_AUTH_MODE") not in {None, "adc"}:
        return json_error("Only server-side Application Default Credentials are supported", 400)
    if settings.get("VERTEX_MEDIA_RESOLUTION") not in {None, "low", "medium", "high"}:
        return json_error("Unsupported media resolution", 400)
    if settings.get("DETECTION_REFINEMENT") not in {None, "hybrid", "ai_only"}:
        return json_error("Unsupported refinement mode", 400)
    catalog().set_settings(settings)
    return jsonify({"success": True, "settings": settings})


@admin_routes.post("/api/admin/settings/detection/test")
@require_admin_json
@require_csrf
def test_detection_settings():
    template_id = str((request.get_json(silent=True) or {}).get("template_id", "")).strip()
    template = catalog().get_template(template_id)
    if not template:
        return json_error("Select a template to test detection", 400)
    try:
        proposal = build_provider(catalog().get_settings(), current_app.config).detect(
            background_for_template(template_id)
        )
    except DetectionError as error:
        return json_error(str(error), 422)
    return jsonify(
        {
            "success": True,
            "proposal": {
                "artwork_area": proposal.artwork_area,
                "confidence": proposal.confidence,
                "reason": proposal.reason,
                "provider": proposal.provider,
            },
        }
    )
