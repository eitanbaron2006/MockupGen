from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from services.ai_mockup_service import render_ai_mockup
from services.image_utils import ImageProcessingError, store_uploaded_artwork
from services.psd_mockup_service import render_psd_mockup
from services.simple_mockup_service import (
    InvalidTemplateError,
    RenderValidationError,
    TemplateNotFoundError,
    list_templates,
    load_manifest,
    render_simple_mockup,
    select_template_for_artwork,
)


mockup_routes = Blueprint("mockup_routes", __name__)


def error_response(message: str, status_code: int):
    return jsonify({"success": False, "error": message}), status_code


@mockup_routes.get("/api/health")
def health_check():
    return jsonify({"status": "ok", "service": "mockup-render-server"})


@mockup_routes.get("/api/mockups/templates")
def get_templates():
    templates = list_templates(Path(current_app.config["TEMPLATES_FOLDER"]))
    product_type = request.args.get("product_type", "").strip().lower()
    if product_type:
        filtered = []
        for template in templates:
            try:
                _, manifest = load_manifest(
                    Path(current_app.config["TEMPLATES_FOLDER"]), template["template_id"]
                )
            except (TemplateNotFoundError, InvalidTemplateError):
                continue
            if str(manifest.get("product_type", "")).lower() == product_type:
                filtered.append(template)
        templates = filtered
    return jsonify(templates)


@mockup_routes.get("/api/mockups/categories")
def get_categories():
    catalog = current_app.extensions.get("catalog_service")
    return jsonify(catalog.list_categories(active_only=True) if catalog else [])


@mockup_routes.post("/api/mockups/render")
def render_mockup():
    mode = request.form.get("mode", "simple").strip().lower()
    template_id = request.form.get("template_id", "").strip()
    product_type = request.form.get("product_type", "").strip()
    output_format = request.form.get("output_format", "png").strip().lower()
    fit_mode = request.form.get("fit_mode", "").strip().lower() or None
    artwork = request.files.get("artwork")

    if mode not in {"simple", "psd", "ai"}:
        return error_response("Unsupported rendering mode", 400)
    if not template_id and (mode != "simple" or not product_type):
        return error_response("Template ID or product type is required", 400)
    if artwork is None or not artwork.filename:
        return error_response("Artwork file is required", 400)

    enabled_flag = f"ENABLE_{mode.upper()}_MODE"
    if not current_app.config.get(enabled_flag, False):
        return error_response(f"{mode.upper()} rendering mode is disabled", 503)

    try:
        artwork_path = store_uploaded_artwork(
            artwork, Path(current_app.config["UPLOAD_FOLDER"])
        )
        if mode == "simple":
            if not template_id:
                template_id = select_template_for_artwork(
                    Path(current_app.config["TEMPLATES_FOLDER"]), product_type, artwork_path
                ) or ""
                if not template_id:
                    return error_response("No suitable template found for product type", 404)
            result = render_simple_mockup(
                template_id=template_id,
                artwork_path=artwork_path,
                output_format=output_format,
                templates_folder=Path(current_app.config["TEMPLATES_FOLDER"]),
                output_folder=Path(current_app.config["OUTPUT_FOLDER"]),
                fit_mode=fit_mode,
            )
            return jsonify(result.as_response())
        elif mode == "ai":
            project_id = current_app.config.get("VERTEX_PROJECT_ID", "").strip()
            if not project_id:
                catalog = current_app.extensions.get("catalog_service")
                if catalog:
                    project_id = catalog.get_settings().get("VERTEX_PROJECT_ID", "").strip()
            if not project_id:
                return error_response("Vertex Project ID is not configured. Please set it in .env or Settings.", 400)

            result = render_ai_mockup(
                template_id=template_id,
                artwork_path=artwork_path,
                templates_folder=Path(current_app.config["TEMPLATES_FOLDER"]),
                output_folder=Path(current_app.config["OUTPUT_FOLDER"]),
                project_id=project_id,
                location=current_app.config.get("VERTEX_LOCATION", "global"),
            )
            return jsonify(result.as_response())
        elif mode == "psd":
            render_psd_mockup(template_id=template_id, artwork_path=artwork_path)
    except TemplateNotFoundError:
        return error_response("Template not found", 404)
    except (ImageProcessingError, RenderValidationError) as error:
        return error_response(str(error), 400)
    except InvalidTemplateError as error:
        return error_response(str(error), 500)
    except NotImplementedError as error:
        return error_response(str(error), 501)

    return error_response("Rendering did not produce an output", 500)
