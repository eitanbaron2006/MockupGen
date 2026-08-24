import json
from pathlib import Path
from tempfile import TemporaryDirectory

from flask import Blueprint, current_app, jsonify, request

from services.ai_mockup_service import render_ai_mockup
from services.image_utils import ImageProcessingError, store_uploaded_artwork
from services.mockup_request_service import RequestValidationError, execute_batch_render
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


def prepare_draft_render_manifest(drafts_folder: Path, template: dict) -> Path | None:
    template_id = str(template.get("template_id", ""))
    if not template_id or Path(template_id).name != template_id:
        return None
    template_folder = drafts_folder / template_id
    if not template_folder.is_dir() or not template.get("artwork_area"):
        return None
    manifest = {
        "template_id": template_id,
        "name": template.get("name") or template_id,
        "product_type": template.get("product_type"),
        "canvas_width": template["canvas_width"],
        "canvas_height": template["canvas_height"],
        "artwork_area": template["artwork_area"],
        "fit_mode": template.get("fit_mode") or "cover",
        "orientation": template.get("orientation"),
        "background": template.get("background_name") or "background.png",
        "foreground": template.get("foreground_name"),
        "mask": template.get("mask_name"),
        "preview": template.get("preview_name") or "preview.png",
        "supported_modes": ["simple"],
        "output_format": "png",
        "effects": template.get("effects"),
        "raw_artwork_area": template.get("raw_artwork_area"),
        "detection_provider": template.get("detection_provider"),
        "detection_confidence": template.get("detection_confidence"),
    }
    (template_folder / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return drafts_folder



@mockup_routes.get("/api/health")
def health_check():
    return jsonify({"status": "ok", "service": "mockup-render-server"})


@mockup_routes.get("/api/mockups/templates")
def get_templates():
    templates = list_templates(Path(current_app.config["TEMPLATES_FOLDER"]))

    # The catalog DB is the source of truth for admin-managed fields
    # (category/product_type, orientation, detected frame regions) — the
    # on-disk manifests can lag behind admin edits.
    catalog = current_app.extensions.get("catalog_service")
    if catalog:
        records_by_id = {
            record["template_id"]: record
            for record in catalog.list_templates(status="active")
        }
        for template in templates:
            record = records_by_id.get(template["template_id"])
            if not record:
                continue
            if record.get("product_type"):
                template["product_type"] = record["product_type"]
            if record.get("orientation"):
                template["orientation"] = record["orientation"]
            raw_area = record.get("raw_artwork_area")
            regions = raw_area.get("regions") if isinstance(raw_area, dict) else None
            if isinstance(regions, list) and regions:
                template["frame_count"] = len(regions)

    product_type = request.args.get("product_type", "").strip().lower()
    if product_type:
        templates = [
            template for template in templates
            if str(template.get("product_type", "")).lower() == product_type
        ]
    return jsonify(templates)


@mockup_routes.get("/api/mockups/templates/<template_id>")
def get_template_detail(template_id: str):
    """Template details including numbered artwork frames, so clients can let
    users assign each image of a set to a specific frame."""
    from services.template_selection_service import template_frames

    try:
        _, manifest = load_manifest(
            Path(current_app.config["TEMPLATES_FOLDER"]), template_id
        )
    except TemplateNotFoundError:
        return error_response("Template not found", 404)
    except InvalidTemplateError as error:
        return error_response(str(error), 500)

    raw_artwork_area = manifest.get("raw_artwork_area")
    catalog = current_app.extensions.get("catalog_service")
    if catalog:
        record = catalog.get_template(template_id)
        if record and record.get("raw_artwork_area"):
            raw_artwork_area = record["raw_artwork_area"]

    preview_name = manifest.get("preview", "preview.png")
    return jsonify(
        {
            "template_id": manifest["template_id"],
            "name": manifest.get("name"),
            "product_type": manifest.get("product_type"),
            "orientation": manifest.get("orientation"),
            "canvas_width": manifest["canvas_width"],
            "canvas_height": manifest["canvas_height"],
            "fit_mode": manifest.get("fit_mode"),
            "preview_url": f"/templates/{template_id}/{preview_name}",
            "frames": template_frames({**manifest, "raw_artwork_area": raw_artwork_area}),
        }
    )


@mockup_routes.get("/api/mockups/categories")
def get_categories():
    catalog = current_app.extensions.get("catalog_service")
    return jsonify(catalog.list_categories(active_only=True) if catalog else [])


@mockup_routes.post("/api/mockups/render/batch")
def render_mockup_batch():
    """Flexible multi-item rendering: singles, separate images and wall/device
    sets in one request, with manual or automatic template selection.

    multipart/form-data:
      - spec: JSON request specification (see docs/mockup_batch_api.md)
      - any number of file fields, referenced by name from spec items
    """
    if not current_app.config.get("ENABLE_SIMPLE_MODE", False):
        return error_response("SIMPLE rendering mode is disabled", 503)

    spec_raw = request.form.get("spec", "").strip()
    if not spec_raw:
        return error_response("Missing 'spec' JSON form field", 400)
    try:
        spec = json.loads(spec_raw)
    except json.JSONDecodeError as error:
        return error_response(f"Invalid spec JSON: {error}", 400)

    catalog = current_app.extensions.get("catalog_service")

    def template_record_lookup(template_id: str) -> dict | None:
        return catalog.get_template(template_id) if catalog else None

    default_realism = not current_app.config.get("TESTING")
    try:
        payload = execute_batch_render(
            spec,
            request.files,
            templates_folder=Path(current_app.config["TEMPLATES_FOLDER"]),
            output_folder=Path(current_app.config["OUTPUT_FOLDER"]),
            upload_folder=Path(current_app.config["UPLOAD_FOLDER"]),
            template_record_lookup=template_record_lookup,
            default_realism=default_realism,
        )
    except RequestValidationError as error:
        return error_response(str(error), 400)
    except ImageProcessingError as error:
        return error_response(str(error), 400)
    status = 200 if payload["success"] else 207
    return jsonify(payload), status


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

    # Admin canvas previews are throwaway on both ends: the render comes back
    # inline, and the artwork they upload is discarded instead of piling up in
    # the uploads folder on every redraw.
    preview = request.form.get("preview", "").strip().lower() == "true"
    scratch_dir = TemporaryDirectory(prefix="mockup-preview-") if preview else None

    try:
        artwork_path = store_uploaded_artwork(
            artwork,
            Path(scratch_dir.name) if scratch_dir else Path(current_app.config["UPLOAD_FOLDER"]),
        )
        if mode == "simple":
            if not template_id:
                template_id = select_template_for_artwork(
                    Path(current_app.config["TEMPLATES_FOLDER"]), product_type, artwork_path
                ) or ""
                if not template_id:
                    return error_response("No suitable template found for product type", 404)
            # Default to false in testing mode to preserve exact pixel matching assertions, otherwise true
            default_realism = "false" if current_app.config.get("TESTING") else "true"
            realism_val = request.form.get("realism", default_realism).strip().lower()
            realism = realism_val != "false"
            
            catalog = current_app.extensions.get("catalog_service")
            db_effects = None
            db_artwork_area = None
            db_raw_artwork_area = None
            db_mask_name = None
            db_fit_mode = fit_mode
            render_templates_folder = Path(current_app.config["TEMPLATES_FOLDER"])
            if catalog and template_id:
                db_template = catalog.get_template(template_id)
                if db_template:
                    db_effects = db_template.get("effects")
                    db_artwork_area = db_template.get("artwork_area")
                    db_raw_artwork_area = db_template.get("raw_artwork_area")
                    db_mask_name = db_template.get("mask_name")
                    if not db_fit_mode:
                        db_fit_mode = db_template.get("fit_mode")
                    if db_template.get("status") == "draft":
                        draft_folder = prepare_draft_render_manifest(
                            Path(current_app.config["DRAFT_TEMPLATES_FOLDER"]),
                            db_template,
                        )
                        if draft_folder:
                            render_templates_folder = draft_folder

            quality_raw = request.form.get("quality", "").strip()
            try:
                quality = int(quality_raw) if quality_raw else None
            except ValueError:
                return error_response("quality must be an integer between 1 and 100", 400)

            output_folder = Path(current_app.config["OUTPUT_FOLDER"])

            result = render_simple_mockup(
                template_id=template_id,
                artwork_path=artwork_path,
                output_format=output_format,
                quality=quality,
                templates_folder=render_templates_folder,
                output_folder=output_folder,
                fit_mode=db_fit_mode,
                realism=realism,
                effects=db_effects,
                artwork_area=db_artwork_area,
                raw_artwork_area=db_raw_artwork_area,
                mask_name=db_mask_name,
                preview=preview,
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

            model = request.form.get("model", "gemini-3.1-flash-image").strip()
            result = render_ai_mockup(
                template_id=template_id,
                artwork_path=artwork_path,
                templates_folder=Path(current_app.config["TEMPLATES_FOLDER"]),
                output_folder=Path(current_app.config["OUTPUT_FOLDER"]),
                project_id=project_id,
                location=current_app.config.get("VERTEX_LOCATION", "global"),
                model=model,
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
    except Exception as error:
        return error_response(str(error), 500)
    finally:
        if scratch_dir is not None:
            scratch_dir.cleanup()

    return error_response("Rendering did not produce an output", 500)
