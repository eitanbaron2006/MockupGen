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
import time
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
    stream_with_context,
    url_for,
)
from PIL import Image, UnidentifiedImageError

from routes.responses import json_error
from services.image_utils import ImageProcessingError, load_rgba
from services.print_catalog_service import (
    DEFAULT_OUTPUT_MODE,
    PrintCatalogError,
    checked_output_mode,
    ratios_for,
    set_for_artwork,
)
from services.print_export_service import (
    OUTPUT_MODES,
    PrintExportError,
    available_qualities,
    discover_tool,
    fit_file_to_limit,
    flatten_artwork,
    has_transparency,
    print_file_name,
    printing_guide,
    render_print_file,
    resolved_tools,
)
from services.print_package_service import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_FILES,
    PackingError,
    packing_report,
    plan_delivery,
)

print_routes = Blueprint("print_routes", __name__)

# A print file is tens of megapixels; a request that asks for a dozen of them
# is asking for minutes of work, so the count is capped.
MAX_FILES_PER_EXPORT = 12

# How long a finished export is kept before the sweep removes it, files and
# record together. Zero keeps everything, which is what a shop that archives
# its own deliveries elsewhere would want.
DEFAULT_RETENTION_DAYS = 30


def _retention_days() -> int:
    try:
        return max(0, int(print_catalog().get_settings().get("retention_days", DEFAULT_RETENTION_DAYS)))
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS


def sweep_expired_exports() -> dict[str, int]:
    """Delete exports past their keeping date, files and record together.

    This is why the record exists at all: a folder of anonymous files cannot be
    cleaned up safely, because nothing says which of them still matters.

    Files that no record claims are swept on the same clock. Anything written
    before the history existed, or left behind by a half-finished export, is
    unreachable by definition -- nothing can name it -- so once it is past the
    window it is only taking up room.
    """
    days = _retention_days()
    catalog = print_catalog()
    folder = _print_folder()
    removed_files = 0
    expired = catalog.exports_older_than(days)
    for export in expired:
        for name in catalog.forget_export(export["id"]):
            path = folder / name
            if path.is_file():
                path.unlink(missing_ok=True)
                removed_files += 1

    orphans = 0
    if days > 0:
        claimed = catalog.claimed_file_names()
        cutoff = time.time() - days * 86400
        for path in folder.iterdir():
            # A preview belongs to the file beside it and is swept with it.
            if PREVIEW_SUFFIX in path.name:
                stem = path.name.split(PREVIEW_SUFFIX)[0]
                if any(name.startswith(stem) for name in claimed):
                    continue
            if path.is_file() and path.name not in claimed and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                orphans += 1
    return {"exports": len(expired), "files": removed_files, "unclaimed_files": orphans}


def print_catalog():
    return current_app.extensions["print_catalog"]


def _tools() -> dict[str, str]:
    """Where the optional upscalers live on this machine.

    A setting that was never filled in does not mean "not installed": the
    usual install locations are checked too, so a machine that has
    Real-ESRGAN where the original resizer puts it needs no configuration.
    """
    return resolved_tools(print_catalog().get_settings())


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


# A print file is fifteen to twenty megabytes. Anything that wants to *look* at
# one -- a listing row, a gallery, a grid of six -- must not download it, so a
# small preview is made once and kept beside it.
PREVIEW_EDGE = 420
PREVIEW_SUFFIX = ".preview-"


@print_routes.get("/print-outputs/<path:name>")
def print_output(name: str):
    """One finished print file, or a small preview of it.

    ``?preview=1`` answers with a few tens of kilobytes instead of twenty
    megabytes. The preview is written next to the file the first time it is
    asked for, so a gallery of six costs one render each and nothing after.
    """
    if Path(name).name != name:
        return json_error("Invalid file name", 400)
    folder = _print_folder()
    path = folder / name
    if not path.is_file():
        return json_error("File not found", 404)

    asked = request.args.get("preview")
    if not asked:
        return send_file(path)

    if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        # A guide or an archive has nothing to show.
        return json_error("That file has no preview", 415)

    # A number asks for that edge: a grid wants a few tens of kilobytes and a
    # full-screen view wants something worth looking at, and neither wants the
    # twenty megabytes behind them. Each size is kept separately.
    try:
        edge = max(120, min(int(asked), 2400)) if asked not in {"1", "true", "yes"} else PREVIEW_EDGE
    except (TypeError, ValueError):
        edge = PREVIEW_EDGE

    preview = folder / f"{path.stem}.preview-{edge}.jpg"
    if not preview.is_file() or preview.stat().st_mtime < path.stat().st_mtime:
        try:
            with Image.open(path) as opened:
                opened.load()
                thumbnail = opened.convert("RGB")
            thumbnail.thumbnail((edge, edge), Image.LANCZOS)
            thumbnail.save(preview, format="JPEG", quality=82, optimize=True)
        except (OSError, ValueError) as error:
            return json_error(f"Could not make a preview: {error}", 500)
    return send_file(preview)


# ------------------------------------------------------------------ ratios


@print_routes.get("/api/print/ratios")
def get_print_ratios():
    return jsonify(
        {
            "ratios": print_catalog().list_ratios(),
            "qualities": available_qualities(_tools()),
            "modes": list(OUTPUT_MODES),
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
        # A built-in ratio is refused, not missing.
        return json_error(str(error), 404 if "not found" in str(error).lower() else 400)
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
    # Discovery only, never the configured path: the screen offers this as
    # "found at ...", and echoing back a wrong setting under that wording would
    # be the opposite of helpful.
    return jsonify(
        {
            "settings": print_catalog().get_settings(),
            "found": {name: discover_tool(name) for name in ("realesrgan", "topaz")},
            "retention_days": _retention_days(),
        }
    )


@print_routes.put("/api/print/settings")
def put_print_settings():
    refusal = _require_admin_json() or _require_csrf()
    if refusal:
        return refusal
    payload = request.get_json(silent=True) or {}
    allowed = {"realesrgan_path", "topaz_path", "retention_days", "etsy_max_files", "etsy_max_bytes"}
    print_catalog().set_settings({k: str(v) for k, v in payload.items() if k in allowed})
    return jsonify({"success": True, "settings": print_catalog().get_settings()})


# ------------------------------------------------------------------ export


def _wants_stream() -> bool:
    """Whether this caller wants the files as they are made.

    Off unless asked for: the shop application has always received one JSON
    object and that must not change under it. The screen asks for the stream.
    """
    asked = str(request.args.get("stream") or request.form.get("stream") or "").lower()
    if asked in {"1", "true", "yes"}:
        return True
    return "application/x-ndjson" in (request.headers.get("Accept") or "")


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
        uploaded = load_rgba(upload)
        transparent = has_transparency(uploaded)
        artwork = flatten_artwork(uploaded)
    except (ImageProcessingError, UnidentifiedImageError, OSError) as error:
        return json_error(f"That file is not a readable image: {error}", 400)

    quality = str(spec.get("quality") or "").strip()
    asked_mode = str(spec.get("mode") or spec.get("output_mode") or "").strip()
    chosen_set = None
    include_guide = True
    output_mode = ""
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
        output_mode = print_set["output_mode"]
        include_guide = print_set["include_guide"]
        chosen_set = print_set
    else:
        wanted = spec.get("ratios") or ""
        keys = [key.strip().lower() for key in (wanted.split(",") if isinstance(wanted, str) else wanted) if str(key).strip()]
        active = catalog.list_ratios(active_only=True)
        if keys:
            ratios = [ratio for ratio in active if ratio["key"].lower() in keys]
        else:
            # No set named and no ratios listed: the shape of the artwork
            # decides. If its ratio has a package configured, that is what the
            # shop sells it as -- otherwise it is the one file its own shape
            # makes, which is what this used to do in every case.
            by_shape = set_for_artwork(catalog, artwork.width / artwork.height)
            if by_shape:
                print_set = by_shape
                chosen_set = by_shape
                ratios = ratios_for(catalog, by_shape, artwork.width / artwork.height)
                quality = quality or by_shape["quality"]
                output_mode = by_shape["output_mode"]
                include_guide = by_shape["include_guide"]
            else:
                ratios = ratios_for(catalog, {"mode": "matching"}, artwork.width / artwork.height)
    try:
        # An explicit mode wins over the set's, so one export can be tried
        # another way without editing the set.
        output_mode = checked_output_mode(asked_mode or output_mode or DEFAULT_OUTPUT_MODE)
    except PrintCatalogError as error:
        return json_error(str(error), 400)
    if not ratios:
        return json_error("No ratio matches this request", 400)
    if len(ratios) > MAX_FILES_PER_EXPORT:
        return json_error(f"At most {MAX_FILES_PER_EXPORT} files per export", 400)

    folder = _print_folder()
    batch = uuid4().hex[:12]
    tools = _tools()

    def one_file(ratio: dict) -> dict:
        """Render and save a single ratio, whatever the answer turns out to be.

        How long each one took travels with it: at full size the ratios differ
        by seconds, and which quality is worth its wait is the question the
        screen should be able to answer without a stopwatch.
        """
        began = time.perf_counter()
        try:
            rendered = render_print_file(
                artwork,
                ratio,
                quality=quality or "bicubic",
                mode=output_mode,
                tools=tools,
            )
        except PrintExportError as error:
            return {
                "ratio": ratio["key"],
                "success": False,
                "error": str(error),
                "ms": round((time.perf_counter() - began) * 1000),
            }
        name = f"{batch}_{print_file_name(ratio, artwork)}"
        produced = folder / name
        rendered.save(produced, format="JPEG", quality=95, optimize=True, dpi=(300, 300))
        return {
            "ratio": ratio["key"],
            "success": True,
            "file": name,
            "url": f"/print-outputs/{name}",
            "width": rendered.width,
            "height": rendered.height,
            "prints_at": ratio.get("sizes", ""),
            "bytes": produced.stat().st_size if produced.is_file() else 0,
            "ms": round((time.perf_counter() - began) * 1000),
        }

    def finish(files: list[dict]) -> dict:
        """The guide, the record and the sweep -- the same either way round."""
        guide_name = None
        if include_guide and any(entry["success"] for entry in files):
            guide_name = f"{batch}_printing_guide.txt"
            (folder / guide_name).write_text(printing_guide(ratios, output_mode), encoding="utf-8")

        made = [entry for entry in files if entry["success"]]
        # The record is what turns the folder into a history: which artwork,
        # under which set, in which mode -- and what may be swept away later.
        saved = None
        if made:
            saved = catalog.record_export(
                {
                    "batch": batch,
                    "artwork_name": upload.filename or "",
                    "artwork_width": artwork.width,
                    "artwork_height": artwork.height,
                    "set_id": chosen_set["id"] if chosen_set else None,
                    "set_name": chosen_set["name"] if chosen_set else "",
                    "output_mode": output_mode,
                    "quality": quality or "bicubic",
                    "guide_file": guide_name or "",
                    # Whatever the shop app calls this listing, so it can ask
                    # later what it already has for it.
                    "reference": str(spec.get("reference") or "").strip()[:200],
                },
                made,
            )
            sweep_expired_exports()

        return {
            "success": len(made) == len(files),
            "export_id": saved["id"] if saved else None,
            "artwork_ratio": round(artwork.width / artwork.height, 4),
            "artwork_was_transparent": transparent,
            "quality": quality or "bicubic",
            "mode": output_mode,
            "files": files,
            "guide": {"file": guide_name, "url": f"/print-outputs/{guide_name}"} if guide_name else None,
        }

    if _wants_stream():
        # Newline-delimited JSON: the screen shows a file the moment it exists
        # rather than holding a blank panel for the minutes a six-ratio export
        # takes. The plain answer stays the default, because the shop app asks
        # for one JSON object and that contract must not move under it.
        def lines():
            yield json.dumps(
                {
                    "event": "start",
                    "batch": batch,
                    "ratios": [ratio["key"] for ratio in ratios],
                    "quality": quality or "bicubic",
                    "mode": output_mode,
                }
            ) + "\n"
            produced = []
            for ratio in ratios:
                entry = one_file(ratio)
                produced.append(entry)
                yield json.dumps({"event": "file", **entry}) + "\n"
            yield json.dumps({"event": "done", **finish(produced)}) + "\n"

        return current_app.response_class(
            stream_with_context(lines()),
            mimetype="application/x-ndjson",
            # Nothing in between should hold the lines back and hand them over
            # as one block at the end.
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    files = [one_file(ratio) for ratio in ratios]
    summary = finish(files)
    return jsonify(summary), (200 if summary["success"] else 207)


def _etsy_limits() -> tuple[int, int]:
    """What the marketplace accepts. Settings, because it is not ours to fix."""
    stored = print_catalog().get_settings()
    try:
        max_files = max(1, int(stored.get("etsy_max_files", DEFAULT_MAX_FILES)))
    except (TypeError, ValueError):
        max_files = DEFAULT_MAX_FILES
    try:
        max_bytes = max(1, int(stored.get("etsy_max_bytes", DEFAULT_MAX_BYTES)))
    except (TypeError, ValueError):
        max_bytes = DEFAULT_MAX_BYTES
    return max_files, max_bytes


@print_routes.post("/api/print/deliverables")
def build_print_deliverables():
    """The print files, packed into what a shop is allowed to upload.

    Same input as the export -- an ``artwork`` file and a ``spec`` -- and the
    same files come out of it. What is different is the shape: a digital
    listing on Etsy takes five files of twenty megabytes, and a full pack is
    ten files of three to eight. The total was never the problem; the count is.

    The printing guide goes inside every archive rather than taking a slot of
    its own, so the buyer finds it whichever one they open first.
    """
    made = export_print_files()
    # An export that could not even start answers for itself.
    if isinstance(made, tuple):
        payload, status = made
        if status >= 400:
            return made
        summary = payload.get_json()
    elif getattr(made, "status_code", 200) >= 400:
        return made
    else:
        summary = made.get_json()

    produced = [entry for entry in summary.get("files", []) if entry.get("success")]
    if not produced:
        return json_error("Nothing was produced to package", 400)

    max_files, max_bytes = _etsy_limits()
    folder = _print_folder()
    guide = summary.get("guide") or {}
    guide_name = guide.get("file")

    # A detailed artwork at 7200x10800 is 34MB at quality 95 -- over the limit
    # on its own, before any packing. The dimensions and the 300 DPI are what
    # a print needs, so what gives is the compression, which is the one of the
    # three nobody can see change at arm's length.
    fitted = []
    for entry in produced:
        outcome = fit_file_to_limit(folder / entry["file"], max_bytes)
        entry["bytes"] = outcome["bytes"]
        if outcome.get("fitted"):
            entry["fitted_quality"] = outcome["quality"]
            fitted.append({
                "ratio": entry.get("ratio"),
                "was_bytes": outcome["was_bytes"],
                "bytes": outcome["bytes"],
                "quality": outcome["quality"],
            })

    def fit_all(budget: int) -> None:
        for entry in produced:
            outcome = fit_file_to_limit(folder / entry["file"], budget)
            entry["bytes"] = outcome["bytes"]
            if outcome.get("fitted"):
                entry["fitted_quality"] = outcome["quality"]
                fitted.append({
                    "ratio": entry.get("ratio"),
                    "was_bytes": outcome["was_bytes"],
                    "bytes": outcome["bytes"],
                    "quality": outcome["quality"],
                })

    fit_all(max_bytes)

    try:
        plan = plan_delivery(produced, max_files=max_files, max_bytes=max_bytes, has_guide=bool(guide_name))
    except PackingError as error:
        # The per-file limit is met and it still will not go. The total is not
        # the constraint either: six files of 15MB come to 93MB against a
        # 100MB ceiling, and still need six archives -- because two of them do
        # not fit in one 20MB archive together.
        #
        # What decides it is how many files have to share an archive. Six into
        # five means at least one pair, so every file has to be at most half an
        # archive; the margin is for the archive's own overhead.
        import math

        per_archive = math.ceil(len(produced) / max_files)
        share = int(max_bytes / per_archive * 0.94)
        if share < max_bytes and "archives" in str(error):
            fit_all(share)
            try:
                plan = plan_delivery(produced, max_files=max_files, max_bytes=max_bytes, has_guide=bool(guide_name))
            except PackingError as second:
                error = second
            else:
                return _deliverables_answer(plan, summary, produced, fitted, folder, guide, max_files, max_bytes)
        # Named rather than worked around: quietly delivering eight of eleven
        # ratios is the worst outcome available.
        return jsonify({
            "success": False,
            "error": str(error),
            "export_id": summary.get("export_id"),
            "files": summary.get("files"),
            "limits": {"max_files": max_files, "max_bytes": max_bytes},
        }), 409

    return _deliverables_answer(plan, summary, produced, fitted, folder, guide, max_files, max_bytes)


def _deliverables_answer(plan, summary, produced, fitted, folder, guide, max_files, max_bytes):
    """The finished answer, however many passes it took to get there."""
    guide_name = guide.get("file")
    if plan["mode"] == "files":
        # No archive at all. The files already fit the allowance, and a .zip
        # would only stand between the buyer and what they paid for.
        deliverables = [
            {
                "index": index,
                "kind": "print",
                "ratio": entry.get("ratio"),
                "name": entry["file"].split("_", 1)[-1],
                "file": entry["file"],
                "url": entry["url"],
                "bytes": entry.get("bytes", 0),
            }
            for index, entry in enumerate(plan["entries"], start=1)
        ]
        if plan["include_guide"] and guide_name:
            deliverables.append({
                "index": len(deliverables) + 1,
                "kind": "guide",
                "ratio": None,
                "name": guide_name.split("_", 1)[-1],
                "file": guide_name,
                "url": guide["url"],
                "bytes": (folder / guide_name).stat().st_size if (folder / guide_name).is_file() else 0,
            })
    else:
        report = packing_report(plan["parcels"], max_bytes)
        for parcel, described in zip(plan["parcels"], report):
            archive_path = folder / f"{summary.get('export_id') or 'pack'}_{described['name']}"
            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
                for entry in parcel.entries:
                    source = folder / entry["file"]
                    if source.is_file():
                        # The batch id is ours, not the buyer's.
                        archive.write(source, arcname=entry["file"].split("_", 1)[-1])
                if guide_name and (folder / guide_name).is_file():
                    archive.write(folder / guide_name, arcname=guide_name.split("_", 1)[-1])
            described["kind"] = "archive"
            described["file"] = archive_path.name
            described["url"] = f"/print-outputs/{archive_path.name}"
            described["bytes"] = archive_path.stat().st_size
            described["headroom_bytes"] = max_bytes - described["bytes"]
        deliverables = report

    return jsonify({
        "success": True,
        "export_id": summary.get("export_id"),
        "delivery": plan["mode"],
        "guide_dropped": plan["guide_dropped"],
        "mode": summary.get("mode"),
        "quality": summary.get("quality"),
        "limits": {"max_files": max_files, "max_bytes": max_bytes},
        "slots_used": len(deliverables),
        "deliverables": deliverables,
        "recompressed": fitted,
        "files": summary.get("files"),
        "guide": summary.get("guide"),
    })


@print_routes.get("/api/print/exports")
def get_print_exports():
    """What has been produced, newest first.

    Open like the export itself: the shop app asks what it already holds for a
    listing rather than rendering it a second time.
    """
    catalog = print_catalog()
    reference = str(request.args.get("reference") or "").strip()
    try:
        limit = int(request.args.get("limit", 50))
        offset = int(request.args.get("offset", 0))
    except (TypeError, ValueError):
        return json_error("limit and offset must be whole numbers", 400)
    return jsonify(
        {
            "exports": catalog.list_exports(limit=limit, offset=offset, reference=reference),
            "total": catalog.count_exports(reference=reference),
            "retention_days": _retention_days(),
        }
    )


@print_routes.get("/api/print/exports/<int:export_id>")
def get_print_export(export_id: int):
    export = print_catalog().get_export(export_id)
    if not export:
        return json_error("Export not found", 404)
    return jsonify({"export": export})


@print_routes.delete("/api/print/exports/<int:export_id>")
def delete_print_export(export_id: int):
    """Forget one export, and take its files with it."""
    refusal = _require_admin_json() or _require_csrf()
    if refusal:
        return refusal
    folder = _print_folder()
    try:
        names = print_catalog().forget_export(export_id)
    except PrintCatalogError as error:
        return json_error(str(error), 404)
    removed = 0
    for name in names:
        path = folder / name
        if path.is_file():
            path.unlink(missing_ok=True)
            removed += 1
    return jsonify({"success": True, "export_id": export_id, "files_removed": removed})


@print_routes.post("/api/print/exports/sweep")
def sweep_print_exports():
    """Run the retention sweep now rather than waiting for the next export."""
    refusal = _require_admin_json() or _require_csrf()
    if refusal:
        return refusal
    return jsonify({"success": True, **sweep_expired_exports(), "retention_days": _retention_days()})


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
