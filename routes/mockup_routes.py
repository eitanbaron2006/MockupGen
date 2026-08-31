import json
import zipfile
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from flask import Blueprint, current_app, jsonify, request, send_file

from routes.responses import json_error
from services.ai_mockup_service import render_ai_mockup
from services.image_utils import ImageProcessingError, store_uploaded_artwork
from services.listing_bundle_service import (
    BundleValidationError,
    artwork_ratio,
    auto_jobs,
    build_listing_bundle,
    orientation_for_ratio,
    size_family_for_ratio,
)
from services.listing_set_service import ListingSetError, resolve_items, rotation_for
from services.mockup_request_service import RequestValidationError, execute_batch_render
from services.psd_mockup_service import render_psd_mockup
from services.simple_mockup_service import (
    InvalidTemplateError,
    RenderValidationError,
    TemplateNotFoundError,
    list_templates,
    load_manifest,
    merge_template_record,
    render_simple_mockup,
    select_template_for_artwork,
)
from services.size_guide_service import SizeGuideError, guide_path
from services.template_manifest import build_manifest, write_manifest

mockup_routes = Blueprint("mockup_routes", __name__)


# The blueprint's own name for it, kept so the routes below read as they did.
error_response = json_error


def prepare_draft_render_manifest(drafts_folder: Path, template: dict) -> Path | None:
    template_id = str(template.get("template_id", ""))
    if not template_id or Path(template_id).name != template_id:
        return None
    template_folder = drafts_folder / template_id
    if not template_folder.is_dir() or not template.get("artwork_area"):
        return None
    write_manifest(template_folder, build_manifest({**template, "template_id": template_id}))
    return drafts_folder



@mockup_routes.get("/api/health")
def health_check():
    return jsonify({"status": "ok", "service": "mockup-render-server"})


@mockup_routes.get("/api/mockups/templates")
def get_templates():
    # The catalog is the source of truth for everything the admin edits — name,
    # category, orientation, the detected frames. The manifest on disk is only
    # the snapshot written when the template was published.
    catalog = current_app.extensions.get("catalog_service")
    records_by_id = {
        record["template_id"]: record
        for record in (catalog.list_templates(status="active") if catalog else [])
    }
    templates = list_templates(Path(current_app.config["TEMPLATES_FOLDER"]), records_by_id)

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

    catalog = current_app.extensions.get("catalog_service")
    manifest = merge_template_record(
        manifest, catalog.get_template(template_id) if catalog else None
    )
    raw_artwork_area = manifest.get("raw_artwork_area")

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


@mockup_routes.post("/api/mockups/listing-bundle")
def render_listing_bundle():
    """Every image one shop listing needs, from a single artwork.

    multipart/form-data:
      - artwork: the artwork file (or another field named by spec.artwork)
      - spec: optional JSON -- the listing set to build, or selection hints for
        the automatic fallback (see docs/mockup_batch_api.md)
    """
    if not current_app.config.get("ENABLE_SIMPLE_MODE", False):
        return error_response("SIMPLE rendering mode is disabled", 503)

    spec_raw = request.form.get("spec", "").strip()
    spec: dict = {}
    if spec_raw:
        try:
            parsed = json.loads(spec_raw)
        except json.JSONDecodeError as error:
            return error_response(f"Invalid spec JSON: {error}", 400)
        if not isinstance(parsed, dict):
            return error_response("spec must be a JSON object", 400)
        spec = parsed

    artwork_field = str(spec.get("artwork") or "artwork")
    artwork = request.files.get(artwork_field)
    if artwork is None or not artwork.filename:
        return error_response(f"Artwork file '{artwork_field}' is required", 400)

    quality = spec.get("quality")
    try:
        quality = int(quality) if quality is not None else None
    except (TypeError, ValueError):
        return error_response("spec.quality must be a number", 400)

    catalog = current_app.extensions.get("catalog_service")
    records = (
        {record["template_id"]: record for record in catalog.list_templates()}
        if catalog
        else {}
    )

    def is_main_template(template_id: str) -> bool:
        record = records.get(template_id) or {}
        return str(record.get("category_name") or "").strip().lower().startswith("main")

    def category_templates(category_id: int) -> list[dict]:
        return [
            record for record in records.values()
            if record.get("category_id") == category_id
        ]

    try:
        artwork_path = store_uploaded_artwork(
            artwork, Path(current_app.config["UPLOAD_FOLDER"])
        )
        ratio = artwork_ratio(artwork_path)

        requested_set = spec.get("set")
        if requested_set not in (None, ""):
            if not catalog:
                return error_response("The catalog is unavailable", 503)
            try:
                listing_set = catalog.get_listing_set(int(requested_set))
            except (TypeError, ValueError):
                return error_response("spec.set must be a listing set id", 400)
            if not listing_set:
                return error_response(f"Listing set not found: {requested_set}", 404)
            jobs = resolve_items(
                listing_set["items"],
                category_templates=category_templates,
                rotation=rotation_for(artwork_path.name),
            )
        else:
            jobs = auto_jobs(
                ratio,
                templates_folder=Path(current_app.config["TEMPLATES_FOLDER"]),
                records=records or None,
                selection=spec.get("selection"),
                is_main_template=is_main_template,
                templates=spec.get("templates"),
            )

        guides_folder = Path(current_app.config.get("SIZE_GUIDES_FOLDER", "data/size_guides"))
        payload = build_listing_bundle(
            artwork_path,
            jobs=jobs,
            templates_folder=Path(current_app.config["TEMPLATES_FOLDER"]),
            output_folder=Path(current_app.config["OUTPUT_FOLDER"]),
            output_format=str(spec.get("format") or "png").lower(),
            quality=quality,
            realism=bool(spec.get("realism", not current_app.config.get("TESTING"))),
            records=records or None,
            size_guides=catalog.list_size_guides() if catalog else [],
            guide_asset_path=lambda guide: guide_path(guides_folder, guide.get("file_name", "")),
            generate_size_guide=_size_guide_generator(),
        )
    except ListingSetError as error:
        return error_response(str(error), 400)
    except BundleValidationError as error:
        return error_response(str(error), 400)
    except (ImageProcessingError, SizeGuideError) as error:
        return error_response(str(error), 400)
    status = 200 if payload["success"] else 207
    return jsonify(payload), status


def _size_guide_generator():
    """Vertex draws a chart only where the library has none and AI is enabled.

    Image models are unreliable at rendering exact numbers, and a size chart is
    the one picture a buyer measures a wall against -- so this is the fallback
    behind the library, never the default, and the answer says which one the
    chart came from.
    """
    if not current_app.config.get("ENABLE_AI_MODE", False):
        return None
    if current_app.config.get("TESTING"):
        # A test run must not call a paid API over the network, and one that
        # did would pass or fail on someone else's quota.
        return None
    project_id = str(current_app.config.get("VERTEX_PROJECT_ID", "") or "").strip()
    if not project_id:
        catalog = current_app.extensions.get("catalog_service")
        if catalog:
            project_id = str(catalog.get_settings().get("VERTEX_PROJECT_ID", "") or "").strip()
    if not project_id:
        return None
    location = str(current_app.config.get("VERTEX_LOCATION", "global") or "global")

    def generate(ratio: float, family: str):
        from services.size_guide_service import generate_size_guide

        _, unit, sizes = size_family_for_ratio(ratio)
        return generate_size_guide(
            family=family,
            sizes=[size.label for size in sizes],
            unit=unit,
            orientation=orientation_for_ratio(ratio),
            project_id=project_id,
            location=location,
        )

    return generate


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
                # Picking by product type has to read the catalog: the category
                # is an admin-edited field, and the manifests only carry the
                # value each template was published with.
                picker_catalog = current_app.extensions.get("catalog_service")
                picker_records = {
                    record["template_id"]: record
                    for record in (picker_catalog.list_templates(status="active") if picker_catalog else [])
                }
                template_id = select_template_for_artwork(
                    Path(current_app.config["TEMPLATES_FOLDER"]), product_type, artwork_path,
                    picker_records,
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


def _rendered_output_path(outputs_folder: Path, reference: str) -> Path | None:
    """One rendered file, named the way a render result names it.

    The reference is whatever `/api/mockups/render...` handed back --
    "/outputs/mockup_x.png" or just the file name. Both the id and the name
    arrive from the caller, so the resolved file has to sit inside the outputs
    folder: a name that climbs out of it would archive anything the server can
    read.
    """
    name = str(reference or "").strip().rsplit("/", 1)[-1]
    if not name or name in (".", "..") or Path(name).name != name:
        return None
    root = outputs_folder.resolve()
    path = (root / name).resolve()
    if root not in path.parents or not path.is_file():
        return None
    return path


@mockup_routes.post("/api/mockups/outputs/archive")
def archive_rendered_outputs():
    """Several finished renders, as one .zip to save.

    Additive on purpose: the batch endpoint's JSON answer is unchanged, and a
    client that wants the images one by one keeps fetching them from /outputs
    exactly as before. This is for the moment someone wants the lot in a single
    file.

    JSON body:
      - outputs: the output_url values from a render response (or file names)
      - name: what to call the archive (optional)
    """
    payload = request.get_json(silent=True) or {}
    references = payload.get("outputs")
    if not isinstance(references, list) or not references:
        return error_response("Provide 'outputs': a list of rendered output URLs", 400)
    if len(references) > 200:
        return error_response("Too many outputs in one archive (200 at most)", 400)

    outputs_folder = Path(current_app.config["OUTPUT_FOLDER"])
    files: list[Path] = []
    for reference in references:
        path = _rendered_output_path(outputs_folder, reference)
        if path is None:
            # Half an archive is worse than none: the caller would not know
            # which mockup is missing from the folder they just downloaded.
            return error_response(f"No such rendered output: {reference}", 404)
        files.append(path)

    archive = BytesIO()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        used: set[str] = set()
        for path in files:
            name = path.name
            # Two renders of the same template land on the same name; keep both.
            if name in used:
                stem, suffix = path.stem, path.suffix
                index = 2
                while f"{stem}-{index}{suffix}" in used:
                    index += 1
                name = f"{stem}-{index}{suffix}"
            used.add(name)
            bundle.write(path, arcname=name)
    archive.seek(0)

    requested_name = str(payload.get("name") or "mockups.zip").strip()
    download_name = Path(requested_name).name or "mockups.zip"
    if not download_name.lower().endswith(".zip"):
        download_name += ".zip"
    return send_file(
        archive, mimetype="application/zip", as_attachment=True, download_name=download_name
    )
