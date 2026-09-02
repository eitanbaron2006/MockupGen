"""The print side: its own page, its own catalog, its own API.

It lives in this application so it shares one login and one deployment, and
nothing else: the ratios and sets are in a database of their own, and the
screen is at a URL of its own rather than another panel in the studio. That is
what leaves it able to be split out later.

The management endpoints are for the administrator. The export endpoint is
open like the render API beside it, because the shop-side application calls it
the same way it calls for mockups.
"""
from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path
from uuid import uuid4

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
from PIL import UnidentifiedImageError

from routes.responses import json_error
from services.image_utils import ImageProcessingError, load_rgba
from services.print_catalog_service import PrintCatalogError, ratios_for
from services.print_export_service import (
    PrintExportError,
    available_qualities,
    print_file_name,
    printing_guide,
    render_print_file,
)

print_routes = Blueprint("print_routes", __name__)

# A print file is tens of megapixels; a request that asks for a dozen of them
# is asking for minutes of work, so the count is capped.
MAX_FILES_PER_EXPORT = 12


def print_catalog():
    return current_app.extensions["print_catalog"]


def _tools() -> dict[str, str]:
    """Where the optional upscalers live on this machine."""
    settings = print_catalog().get_settings()
    return {
        "realesrgan": settings.get("realesrgan_path", ""),
        "topaz": settings.get("topaz_path", ""),
    }


def _print_folder() -> Path:
    folder = Path(current_app.config["PRINT_OUTPUT_FOLDER"])
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _require_admin_json():
    from routes.admin_routes import is_admin

    return None if is_admin() else json_error("Authentication required", 401)


def _require_csrf():
    import hmac

    expected = session.get("csrf_token")
    submitted = request.headers.get("X-CSRF-Token") or request.form.get("_csrf")
    if not expected or not submitted or not hmac.compare_digest(expected, submitted):
        return json_error("Invalid CSRF token", 403)
    return None


# ------------------------------------------------------------------ the page


@print_routes.get("/print")
def print_page():
    from routes.admin_routes import is_admin

    if not is_admin():
        return redirect(url_for("admin_routes.admin_login_page"))
    return render_template("admin/print.html", csrf_token=session["csrf_token"])


@print_routes.get("/print-outputs/<path:name>")
def print_output(name: str):
    """One finished print file, by the name the export answered with."""
    if Path(name).name != name:
        return json_error("Invalid file name", 400)
    path = _print_folder() / name
    if not path.is_file():
        return json_error("File not found", 404)
    return send_file(path)


# ------------------------------------------------------------------ ratios


@print_routes.get("/api/print/ratios")
def get_print_ratios():
    return jsonify(
        {
            "ratios": print_catalog().list_ratios(),
            "qualities": available_qualities(_tools()),
        }
    )


@print_routes.post("/api/print/ratios")
def create_print_ratio():
    refusal = _require_admin_json() or _require_csrf()
    if refusal:
        return refusal
    try:
        ratio = print_catalog().create_ratio(request.get_json(silent=True) or {})
    except PrintCatalogError as error:
        return json_error(str(error), 400)
    return jsonify({"success": True, "ratio": ratio}), 201


@print_routes.patch("/api/print/ratios/<int:ratio_id>")
def update_print_ratio(ratio_id: int):
    refusal = _require_admin_json() or _require_csrf()
    if refusal:
        return refusal
    try:
        ratio = print_catalog().update_ratio(ratio_id, request.get_json(silent=True) or {})
    except PrintCatalogError as error:
        return json_error(str(error), 404 if "not found" in str(error).lower() else 400)
    return jsonify({"success": True, "ratio": ratio})


@print_routes.delete("/api/print/ratios/<int:ratio_id>")
def delete_print_ratio(ratio_id: int):
    refusal = _require_admin_json() or _require_csrf()
    if refusal:
        return refusal
    try:
        print_catalog().delete_ratio(ratio_id)
    except PrintCatalogError as error:
        return json_error(str(error), 404)
    return jsonify({"success": True, "ratio_id": ratio_id})


# -------------------------------------------------------------- print sets


@print_routes.get("/api/print/sets")
def get_print_sets():
    return jsonify({"sets": print_catalog().list_sets()})


@print_routes.post("/api/print/sets")
def create_print_set():
    refusal = _require_admin_json() or _require_csrf()
    if refusal:
        return refusal
    try:
        print_set = print_catalog().create_set(request.get_json(silent=True) or {})
    except PrintCatalogError as error:
        return json_error(str(error), 400)
    return jsonify({"success": True, "set": print_set}), 201


@print_routes.patch("/api/print/sets/<int:set_id>")
def update_print_set(set_id: int):
    refusal = _require_admin_json() or _require_csrf()
    if refusal:
        return refusal
    try:
        print_set = print_catalog().update_set(set_id, request.get_json(silent=True) or {})
    except PrintCatalogError as error:
        return json_error(str(error), 404 if "not found" in str(error).lower() else 400)
    return jsonify({"success": True, "set": print_set})


@print_routes.delete("/api/print/sets/<int:set_id>")
def delete_print_set(set_id: int):
    refusal = _require_admin_json() or _require_csrf()
    if refusal:
        return refusal
    try:
        print_catalog().delete_set(set_id)
    except PrintCatalogError as error:
        return json_error(str(error), 404)
    return jsonify({"success": True, "set_id": set_id})


@print_routes.get("/api/print/settings")
def get_print_settings():
    refusal = _require_admin_json()
    if refusal:
        return refusal
    return jsonify({"settings": print_catalog().get_settings()})


@print_routes.put("/api/print/settings")
def put_print_settings():
    refusal = _require_admin_json() or _require_csrf()
    if refusal:
        return refusal
    payload = request.get_json(silent=True) or {}
    allowed = {"realesrgan_path", "topaz_path"}
    print_catalog().set_settings({k: str(v) for k, v in payload.items() if k in allowed})
    return jsonify({"success": True, "settings": print_catalog().get_settings()})


# ------------------------------------------------------------------ export


@print_routes.post("/api/print/export")
def export_print_files():
    """One artwork in, the print files a listing ships out.

    multipart/form-data: an ``artwork`` file, and either ``set`` (a saved print
    set) or ``ratios`` (a comma-separated list) with an optional ``quality``.
    """
    upload = request.files.get("artwork")
    if upload is None or not upload.filename:
        return json_error("An artwork file is required", 400)

    catalog = print_catalog()
    spec_raw = request.form.get("spec", "").strip()
    spec: dict = {}
    if spec_raw:
        try:
            parsed = json.loads(spec_raw)
        except json.JSONDecodeError as error:
            return json_error(f"Invalid spec JSON: {error}", 400)
        if not isinstance(parsed, dict):
            return json_error("spec must be a JSON object", 400)
        spec = parsed
    spec = {**spec, **{key: value for key, value in request.form.items() if key != "spec"}}

    try:
        artwork = load_rgba(upload).convert("RGB")
    except (ImageProcessingError, UnidentifiedImageError, OSError) as error:
        return json_error(f"That file is not a readable image: {error}", 400)

    quality = str(spec.get("quality") or "").strip()
    include_guide = True
    requested = spec.get("set")
    if requested not in (None, ""):
        try:
            print_set = catalog.get_set(int(requested))
        except (TypeError, ValueError):
            return json_error("spec.set must be a print set id", 400)
        if not print_set:
            return json_error(f"Print set not found: {requested}", 404)
        ratios = ratios_for(catalog, print_set, artwork.width / artwork.height)
        quality = quality or print_set["quality"]
        include_guide = print_set["include_guide"]
    else:
        wanted = spec.get("ratios") or ""
        keys = [key.strip().lower() for key in (wanted.split(",") if isinstance(wanted, str) else wanted) if str(key).strip()]
        active = catalog.list_ratios(active_only=True)
        ratios = (
            [ratio for ratio in active if ratio["key"].lower() in keys]
            if keys
            else ratios_for(catalog, {"mode": "matching"}, artwork.width / artwork.height)
        )
    if not ratios:
        return json_error("No ratio matches this request", 400)
    if len(ratios) > MAX_FILES_PER_EXPORT:
        return json_error(f"At most {MAX_FILES_PER_EXPORT} files per export", 400)

    folder = _print_folder()
    batch = uuid4().hex[:12]
    files = []
    for ratio in ratios:
        try:
            rendered = render_print_file(artwork, ratio, quality=quality or "bicubic", tools=_tools())
        except PrintExportError as error:
            files.append({"ratio": ratio["key"], "success": False, "error": str(error)})
            continue
        name = f"{batch}_{print_file_name(ratio, artwork)}"
        rendered.save(folder / name, format="JPEG", quality=95, optimize=True, dpi=(300, 300))
        files.append(
            {
                "ratio": ratio["key"],
                "success": True,
                "file": name,
                "url": f"/print-outputs/{name}",
                "width": rendered.width,
                "height": rendered.height,
                "prints_at": ratio.get("sizes", ""),
            }
        )

    guide_name = None
    if include_guide and any(entry["success"] for entry in files):
        guide_name = f"{batch}_printing_guide.txt"
        (folder / guide_name).write_text(printing_guide(ratios), encoding="utf-8")

    made = [entry for entry in files if entry["success"]]
    return jsonify(
        {
            "success": len(made) == len(files),
            "artwork_ratio": round(artwork.width / artwork.height, 4),
            "quality": quality or "bicubic",
            "files": files,
            "guide": {"file": guide_name, "url": f"/print-outputs/{guide_name}"} if guide_name else None,
        }
    ), (200 if len(made) == len(files) else 207)


@print_routes.post("/api/print/archive")
def archive_print_files():
    """The finished files as one .zip, the way a buyer receives them."""
    payload = request.get_json(silent=True) or {}
    names = payload.get("files") or []
    if not isinstance(names, list) or not names:
        return json_error("files must be a non-empty list", 400)
    folder = _print_folder()
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for raw in names[:MAX_FILES_PER_EXPORT + 1]:
            # The export answers with both a name and a URL, so either is
            # accepted -- but only that one prefix. Anything else carrying a
            # path separator is refused outright rather than quietly trimmed
            # down to a name and then reported as missing.
            name = str(raw)
            if name.startswith("/print-outputs/"):
                name = name[len("/print-outputs/"):]
            if not name or "/" in name or "\\" in name or Path(name).name != name:
                return json_error(f"Invalid file name: {raw}", 400)
            path = folder / name
            if not path.is_file():
                return json_error(f"File not found: {name}", 404)
            # The buyer should not see the batch id the studio uses to keep
            # one export apart from another.
            archive.write(path, arcname=name.split("_", 1)[-1] if "_" in name else name)
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=str(payload.get("name") or "print-files.zip"),
    )
