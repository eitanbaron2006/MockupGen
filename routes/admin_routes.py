import hmac
import io
import secrets
import sqlite3
import threading
import time
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from pathlib import Path
from typing import Any, Callable
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

from routes.responses import json_error
from services.catalog_service import CatalogError, CatalogService, orientation_for_size
from services.classic_detection_service import ClassicDetectionProvider
from services.detection_service import DetectionError, build_provider, validate_proposal
from services.green_frame_mockup_service import (
    GreenFrameSettings,
    detect_frames_by_color,
    detect_frames_from_points,
    green_detection_raw,
    green_mask_image,
)
from services.listing_bundle_service import (
    GUIDE_RATIOS,
    dual_unit_labels,
    guide_ratio_shape,
    orientation_for_guide_ratio,
    size_family_for_ratio,
)
from services.listing_set_service import PRODUCT_TYPES, ListingSetError, normalize_items
from services.local_detection_service import discover_local_models
from services.mockup_generation_service import (
    DEFAULT_MOCKUP_PROMPT,
    SCENE_PRESETS,
    MockupGenerationError,
    generate_mockup,
    inspect_green,
    mockup_prompt,
    scene_preset,
)
from services.size_guide_service import (
    DEFAULT_GUIDE_PROMPT,
    GUIDE_PROMPT_PRESETS,
    SizeGuideError,
    generate_size_guide,
    guide_path,
    preset_prompt,
    store_guide_image,
    store_guide_upload,
    store_style_example,
    style_example_path,
)
from services.template_import_service import (
    TemplateImportError,
    delete_template_assets,
    draft_asset_path,
    import_backgrounds,
    publish_template,
    store_background,
)
from services.vertex_model_service import (
    FALLBACK_VERTEX_DETECTION_MODELS,
    list_vertex_detection_models,
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
    "CLASSIC_BLUR_SIZE",
    "CLASSIC_SEARCH_RADIUS",
    "CLASSIC_INTERNAL_MODE",
    "CLASSIC_SUBMODE",
    "CLASSIC_GREEN_EDGE_EXPAND",
    "CLASSIC_GREEN_TOLERANCE",
    "CLASSIC_IMPORT_MODE",
}


def _run_mask_detection(
    background: Path, mode: str, payload: dict
) -> tuple:
    """Handle color_pick / frame_points detection modes.

    Returns (DetectionProposal, mask_Image) using the same raw_artwork_area
    format as green-frame detection so the existing rendering pipeline applies.
    """
    from PIL import Image as _PILImage

    img = _PILImage.open(background).convert("RGBA")
    w, h = img.size
    tolerance = max(5, min(220, int(payload.get("tolerance", 80))))
    settings = GreenFrameSettings(tolerance=tolerance, min_area=80)

    if mode == "color_pick":
        color_raw = payload.get("color") or [0, 255, 0]
        target = tuple(int(c) for c in list(color_raw)[:3])
        state = detect_frames_by_color(img, target, tolerance, settings)
        reason = "Color pick: frame regions detected from sampled color."
    else:
        points = payload.get("points") or []
        if not points:
            raise DetectionError("At least one seed point is required for Frame Points mode.")
        state = detect_frames_from_points(img, points, tolerance, settings)
        reason = f"Frame points: {len(state.regions)} region(s) flood-filled from {len(points)} seed point(s)."

    if not state.regions:
        raise DetectionError(
            "No regions found. Try adjusting the tolerance or selecting a different area."
        )

    raw = green_detection_raw(state, 0)
    mask = green_mask_image(state)

    regions = raw.get("regions", [])
    all_xs = [r["x"] for r in regions] + [r["x"] + r["width"] for r in regions]
    all_ys = [r["y"] for r in regions] + [r["y"] + r["height"] for r in regions]
    first_corners = regions[0].get("corners") if regions else []
    artwork_area: dict = {
        "x": min(all_xs),
        "y": min(all_ys),
        "width": max(all_xs) - min(all_xs),
        "height": max(all_ys) - min(all_ys),
    }
    if first_corners:
        artwork_area["corners"] = first_corners

    proposal = validate_proposal(
        {
            "artwork_area": artwork_area,
            "confidence": 0.85,
            "reason": reason,
            "raw_artwork_area": raw,
        },
        image_width=w,
        image_height=h,
        provider="classic",
    )
    return proposal, mask


def _centered_artwork_area(width: int, height: int) -> dict[str, int]:
    """The neutral artwork box a template falls back to when detection is cleared."""
    orientation = orientation_for_size(width, height)
    if orientation == "portrait":
        area_width, area_height = int(width * 0.56), int(height * 0.62)
    elif orientation == "landscape":
        area_width, area_height = int(width * 0.62), int(height * 0.56)
    else:
        area_width = area_height = int(min(width, height) * 0.58)
    return {
        "x": (width - area_width) // 2,
        "y": (height - area_height) // 2,
        "width": area_width,
        "height": area_height,
    }


# The studio's own submode names and the detector's mode names are not the same
# words, and only one screen knew the translation. Batch detection did not, so
# it silently ran the provider's default -- a different algorithm from the one
# the button beside it ran, on the same mockup.
_SUBMODE_TO_MODE = {
    "auto": "auto",
    "green_frames": "green_frames_mockups",
    "color_pick": "color_pick",
    "frame_points": "frame_points",
    "none": "auto",
}

# Two of them are conversations, not settings: they need a colour sampled or
# points clicked on that particular mockup, so there is nothing to run
# unattended across twenty of them.
INTERACTIVE_MODES = {"color_pick", "frame_points"}


def detection_mode_in_use(explicit: str | None = None) -> str:
    """The mode the admin is working in right now.

    An explicit mode from the request wins -- that is the single Detect button
    saying which flow it ran. Otherwise it is read from the live submode the
    admin has selected in the app, which is what they expect every detection to
    follow, and only then from the stored default.
    """
    if explicit:
        return _SUBMODE_TO_MODE.get(explicit, explicit)
    settings = catalog().get_settings()
    submode = str(settings.get("CLASSIC_SUBMODE") or "").strip()
    if submode:
        return _SUBMODE_TO_MODE.get(submode, submode)
    return str(settings.get("CLASSIC_INTERNAL_MODE") or "auto")


def save_green_frame_mask_if_needed(
    provider: Any, background: Path, mode: str, proposal: Any = None
) -> str | None:
    if mode == "green_frames_mockups" and hasattr(provider, "build_green_frame_mask"):
        mask = provider.build_green_frame_mask(background)
        mask_path = background.parent / "mask.png"
        mask.save(mask_path)
        return "mask.png"
    raw = getattr(proposal, "raw_artwork_area", None)
    if isinstance(raw, dict) and raw.get("mode") == "geometry":
        # Geometric frames render straight onto their corners, so a mask would
        # only be a second copy of the geometry waiting to go stale.
        return None
    if proposal and proposal.raw_artwork_area and isinstance(proposal.raw_artwork_area.get("regions"), list):
        regions = proposal.raw_artwork_area["regions"]
        if len(regions) > 1:
            from PIL import Image, ImageDraw
            with Image.open(background) as img:
                w, h = img.size
            mask = Image.new("L", (w, h), 0)
            draw = ImageDraw.Draw(mask)
            for region in regions:
                corners = region.get("corners") or region.get("inner_corners")
                if corners and len(corners) >= 3:
                    pts = [(int(round(p["x"])), int(round(p["y"]))) for p in corners]
                    draw.polygon(pts, fill=255)
                else:
                    rx, ry = int(region["x"]), int(region["y"])
                    rw, rh = int(region["width"]), int(region["height"])
                    draw.rectangle([rx, ry, rx + rw, ry + rh], fill=255)
            mask_path = background.parent / "mask.png"
            mask.save(mask_path)
            return "mask.png"
    return None

def catalog() -> CatalogService:
    return current_app.extensions["catalog_service"]


def detection_pool() -> ThreadPoolExecutor:
    """The one pool detections run in, created with the app.

    An app built by something other than create_app gets one on first use
    rather than an error -- still one for the process, never one per request.
    """
    pool = current_app.extensions.get("detection_pool")
    if pool is None:
        pool = ThreadPoolExecutor(
            max_workers=max(1, int(current_app.config.get("DETECTION_MAX_WORKERS", 5) or 5)),
            thread_name_prefix="detect",
        )
        current_app.extensions["detection_pool"] = pool
    return pool


def _setting_int(settings, config, key: str, fallback: int) -> int:
    from services.detection_service import _setting_int as read_int

    return read_int(settings, config, key, fallback)





def is_admin() -> bool:
    return bool(session.get("admin_authenticated"))


def require_admin_json(handler: Callable):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        if not is_admin():
            return json_error("Admin authentication required", 401)
        return handler(*args, **kwargs)

    return wrapped


def allow_large_upload(handler: Callable):
    """Lift the request ceiling for one admin route, and no further.

    The application-wide limit protects the public render API, where a request
    is one artwork from anyone at all. It has no business standing between the
    administrator and their own mockups: importing twenty of them is ordinary
    work that runs to tens of megabytes. The allowance is raised per request,
    so nothing outside these routes is affected.
    """

    @wraps(handler)
    def wrapper(*args, **kwargs):
        limit = current_app.config.get("ADMIN_MAX_CONTENT_LENGTH")
        if limit:
            request.max_content_length = int(limit)
        return handler(*args, **kwargs)

    return wrapper


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


# A password login invites exactly one attack: guessing. Ten tries from an
# address in five minutes is far more than a person needs and far less than a
# script wants. The count is kept in this process only -- it is a speed bump on
# the door, not an audit trail -- and it is cleared the moment a login succeeds.
LOGIN_WINDOW_SECONDS = 300
LOGIN_MAX_ATTEMPTS = 10
_login_attempts: dict[str, list[float]] = {}
_login_attempts_lock = threading.Lock()


def _login_client() -> str:
    # Deliberately not X-Forwarded-For: a header the caller writes is a header
    # the caller can rotate, and the limit would count to ten forever.
    return request.remote_addr or "unknown"


def _login_attempt_allowed(client: str) -> bool:
    now = time.monotonic()
    with _login_attempts_lock:
        recent = [when for when in _login_attempts.get(client, []) if now - when < LOGIN_WINDOW_SECONDS]
        allowed = len(recent) < LOGIN_MAX_ATTEMPTS
        if allowed:
            recent.append(now)
        _login_attempts[client] = recent
        if len(_login_attempts) > 1024:
            for key, when in list(_login_attempts.items()):
                if not when or now - when[-1] >= LOGIN_WINDOW_SECONDS:
                    _login_attempts.pop(key, None)
    return allowed


def _login_succeeded(client: str) -> None:
    with _login_attempts_lock:
        _login_attempts.pop(client, None)


@admin_routes.post("/api/admin/login")
def admin_login():
    configured = str(current_app.config.get("ADMIN_PASSWORD", ""))
    if not configured:
        return json_error("ADMIN_PASSWORD is not configured in .env", 503)
    client = _login_client()
    if not _login_attempt_allowed(client):
        return json_error("Too many login attempts. Try again in a few minutes.", 429)
    supplied = str((request.get_json(silent=True) or {}).get("password", ""))
    if not hmac.compare_digest(configured, supplied):
        return json_error("Incorrect password", 401)
    _login_succeeded(client)
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


@admin_routes.patch("/api/admin/categories/<int:category_id>")
@require_admin_json
@require_csrf
def update_admin_category(category_id: int):
    try:
        category = catalog().update_category(
            category_id,
            str((request.get_json(silent=True) or {}).get("name", "")),
        )
    except CatalogError as error:
        status = 404 if str(error) == "Category not found" else 400
        return json_error(str(error), status)
    return jsonify({"success": True, "category": category})


@admin_routes.delete("/api/admin/categories/<int:category_id>")
@require_admin_json
@require_csrf
def delete_admin_category(category_id: int):
    try:
        catalog().delete_empty_category(category_id)
    except CatalogError as error:
        status = 404 if str(error) == "Category not found" else 400
        return json_error(str(error), status)
    return jsonify({"success": True, "category_id": category_id})


# ----------------------------------------------------------------------
# Mockups drawn to order. A mockup here is a room with a flat green rectangle
# in it, so a generated one needs nothing a model is bad at -- and everything
# it produces is measured by the studio's own detector before it is kept.
# ----------------------------------------------------------------------

MOCKUP_PROMPT_KEY = "MOCKUP_PROMPT"


def _mockup_prompt_template() -> str:
    stored = str(catalog().get_settings().get(MOCKUP_PROMPT_KEY, "") or "").strip()
    return stored or DEFAULT_MOCKUP_PROMPT


def _vertex_project() -> str:
    project_id = str(current_app.config.get("VERTEX_PROJECT_ID", "") or "").strip()
    if not project_id:
        project_id = str(catalog().get_settings().get("VERTEX_PROJECT_ID", "") or "").strip()
    return project_id


@admin_routes.get("/api/admin/mockups/scenes")
@require_admin_json
def get_admin_mockup_scenes():
    return jsonify(
        {
            "scenes": [dict(preset) for preset in SCENE_PRESETS],
            "prompt": _mockup_prompt_template(),
            "default_prompt": DEFAULT_MOCKUP_PROMPT,
            "enabled": bool(current_app.config.get("ENABLE_AI_MODE", False)) and bool(_vertex_project()),
        }
    )


@admin_routes.post("/api/admin/mockups/generate")
@require_admin_json
@require_csrf
@allow_large_upload
def generate_admin_mockup():
    """Draw a mockup, measure its green, and keep it only if it can be used.

    A generated image that the detector cannot read is not a mockup, so it is
    handed back with its numbers instead of being filed away as one -- unless
    the admin says to keep it anyway, which is their call to make.
    """
    if not current_app.config.get("ENABLE_AI_MODE", False):
        return json_error("AI generation is disabled on this server", 503)
    project_id = _vertex_project()
    if not project_id:
        return json_error("Vertex AI is not configured (no project id)", 400)

    payload = request.get_json(silent=True) or {}
    if request.form:
        payload = {**payload, **request.form.to_dict()}

    category_id = payload.get("category_id")
    try:
        category_id = int(category_id) if category_id not in (None, "") else None
    except (TypeError, ValueError):
        return json_error("category_id must be a number", 400)
    if category_id is None or not catalog().get_category(category_id):
        return json_error("A category is required for a generated mockup", 400)

    try:
        frames = max(1, min(6, int(payload.get("frames", 1) or 1)))
    except (TypeError, ValueError):
        return json_error("frames must be a number", 400)
    orientation = str(payload.get("orientation") or "portrait").strip().lower()
    if orientation not in {"portrait", "landscape", "square"}:
        return json_error("orientation must be portrait, landscape or square", 400)
    ratio = str(payload.get("ratio") or "2:3").strip()

    scene_key = str(payload.get("scene") or "").strip()
    scene = scene_preset(scene_key)
    if scene_key and not scene:
        return json_error(f"Unknown scene: {scene_key}", 404)
    template = str(payload.get("prompt") or "").strip()

    reference = None
    upload = request.files.get("reference")
    if upload is not None and upload.filename:
        data = upload.read()
        if len(data) > 8 * 1024 * 1024:
            return json_error("A reference image must be 8MB or smaller", 400)
        reference = (data, upload.mimetype or "image/png")

    prompt = mockup_prompt(
        scene=(scene or SCENE_PRESETS[0])["scene"],
        frames=frames,
        orientation=orientation,
        ratio=ratio,
        template=template or _mockup_prompt_template(),
    )
    try:
        image = generate_mockup(
            prompt=prompt,
            project_id=project_id,
            location=str(current_app.config.get("VERTEX_LOCATION", "global") or "global"),
            reference=reference,
        )
    except MockupGenerationError as error:
        return json_error(str(error), 400)
    except Exception as error:  # a model that will not answer is not a crash
        return json_error(str(error) or "The mockup could not be generated", 502)

    report = inspect_green(image, expected_frames=frames)
    # Wording the admin rewrote is kept; a scene they merely picked is not.
    if template and not scene_key and template != _mockup_prompt_template():
        catalog().set_settings({MOCKUP_PROMPT_KEY: template})

    keep = bool(payload.get("keep", True)) and (report["usable"] or bool(payload.get("force")))
    draft = None
    if keep:
        name = str(payload.get("name", "")).strip() or f"AI {(scene or SCENE_PRESETS[0])['name']}"
        draft = store_background(
            image,
            name=name,
            source_filename=f"generated_{uuid4().hex[:12]}.png",
            category_id=category_id,
            drafts_folder=Path(current_app.config["DRAFT_TEMPLATES_FOLDER"]),
            catalog=catalog(),
        )

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return jsonify(
        {
            "success": True,
            "kept": bool(draft),
            "template": draft,
            "report": report,
            "width": image.width,
            "height": image.height,
            # Shown to the admin whether it was kept or not: a mockup that
            # failed is worth looking at before deciding what to change.
            "image": "data:image/png;base64," + b64encode(buffer.getvalue()).decode("ascii"),
        }
    ), 201


# ----------------------------------------------------------------------
# Listing sets: which mockups a shop listing is built from, decided here
# instead of guessed at render time.
# ----------------------------------------------------------------------


def _main_category_ids() -> set[int]:
    return {
        int(category["id"])
        for category in catalog().list_categories()
        if str(category.get("name") or "").strip().lower().startswith("main")
    }


def _checked_items(raw: Any) -> list[dict]:
    """The set's contents, with the MAIN rule applied before anything is saved.

    A MAIN mockup is the image Etsy shows in search, so it belongs to the hero
    slot alone. Catching that here means a saved set is one that can run, and
    the rule cannot be worked around by editing a set through the API.
    """
    main_categories = _main_category_ids()

    def is_main_template(template_id: str) -> bool:
        record = catalog().get_template(template_id)
        if not record:
            raise ListingSetError(f"Unknown mockup: {template_id}")
        return str(record.get("category_name") or "").strip().lower().startswith("main")

    return normalize_items(
        raw,
        is_main_template=is_main_template,
        is_main_category=lambda category_id: int(category_id) in main_categories,
    )


@admin_routes.get("/api/admin/listing-sets")
@require_admin_json
def get_admin_listing_sets():
    return jsonify(
        {
            "sets": catalog().list_listing_sets(
                product_type=request.args.get("product_type") or None,
                orientation=request.args.get("orientation") or None,
            ),
            # What the listing is for, as the shop-side app names it -- not the
            # shelves the mockups are filed on.
            "product_types": list(PRODUCT_TYPES),
        }
    )


@admin_routes.post("/api/admin/listing-sets")
@require_admin_json
@require_csrf
def create_admin_listing_set():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name", "")).strip()
    if not name:
        return json_error("A set needs a name", 400)
    try:
        listing_set = catalog().create_listing_set(
            {
                "name": name,
                "product_type": payload.get("product_type") or None,
                "orientation": payload.get("orientation") or "any",
                "status": payload.get("status") or "active",
                "items": _checked_items(payload.get("items")),
            }
        )
    except ListingSetError as error:
        return json_error(str(error), 400)
    except CatalogError as error:
        return json_error(str(error), 400)
    except sqlite3.IntegrityError:
        return json_error("A set with that name already exists", 409)
    return jsonify({"success": True, "set": listing_set}), 201


@admin_routes.patch("/api/admin/listing-sets/<int:set_id>")
@require_admin_json
@require_csrf
def update_admin_listing_set(set_id: int):
    payload = request.get_json(silent=True) or {}
    changes: dict[str, Any] = {}
    for field in ("name", "product_type", "orientation", "status"):
        if field in payload:
            changes[field] = payload[field]
    try:
        if "items" in payload:
            changes["items"] = _checked_items(payload.get("items"))
        listing_set = catalog().update_listing_set(set_id, changes)
    except ListingSetError as error:
        return json_error(str(error), 400)
    except CatalogError as error:
        return json_error(str(error), 404 if "not found" in str(error).lower() else 400)
    except sqlite3.IntegrityError:
        return json_error("A set with that name already exists", 409)
    return jsonify({"success": True, "set": listing_set})


@admin_routes.delete("/api/admin/listing-sets/<int:set_id>")
@require_admin_json
@require_csrf
def delete_admin_listing_set(set_id: int):
    try:
        catalog().delete_listing_set(set_id)
    except CatalogError as error:
        return json_error(str(error), 404)
    return jsonify({"success": True, "set_id": set_id})


# ----------------------------------------------------------------------
# The size guide library: ready-made print-size charts, one per ratio.
# ----------------------------------------------------------------------


def _guides_folder() -> Path:
    return Path(current_app.config.get("SIZE_GUIDES_FOLDER", "data/size_guides"))


SIZE_GUIDE_PROMPT_KEY = "SIZE_GUIDE_PROMPT"


def _guide_prompt_template() -> str:
    """The wording the studio will send, which the admin may have rewritten."""
    stored = str(catalog().get_settings().get(SIZE_GUIDE_PROMPT_KEY, "") or "").strip()
    return stored or DEFAULT_GUIDE_PROMPT


@admin_routes.get("/api/admin/size-guides")
@require_admin_json
def get_admin_size_guides():
    return jsonify(
        {
            "guides": catalog().list_size_guides(ratio=request.args.get("ratio") or None),
            "ratios": list(GUIDE_RATIOS),
            "prompt": _guide_prompt_template(),
            "default_prompt": DEFAULT_GUIDE_PROMPT,
            # The styles a shop's chart is drawn in: offered as they are, and
            # any of them can be opened in the editor and rewritten.
            "presets": [
                {
                    **preset,
                    "example": style_example_path(_guides_folder(), preset["key"]) is not None,
                }
                for preset in GUIDE_PROMPT_PRESETS
            ],
        }
    )


def _checked_ratio() -> str:
    ratio = str(request.form.get("ratio") or (request.get_json(silent=True) or {}).get("ratio") or "").strip()
    if ratio not in GUIDE_RATIOS:
        raise SizeGuideError(f"ratio must be one of: {', '.join(GUIDE_RATIOS)}")
    return ratio


@admin_routes.post("/api/admin/size-guides")
@require_admin_json
@require_csrf
@allow_large_upload
def create_admin_size_guide():
    upload = request.files.get("guide")
    if upload is None or not upload.filename:
        return json_error("A guide image is required", 400)
    try:
        ratio = _checked_ratio()
        file_name, width, height = store_guide_upload(upload, _guides_folder())
    except SizeGuideError as error:
        return json_error(str(error), 400)
    guide = catalog().create_size_guide(
        {
            "name": str(request.form.get("name", "")).strip() or f"{ratio} chart",
            "ratio": ratio,
            # Read off the ratio rather than asked for twice: a 3:2 chart is a
            # landscape chart, and two fields could only ever disagree.
            "orientation": orientation_for_guide_ratio(ratio),
            "file_name": file_name,
            "source": "upload",
        }
    )
    return jsonify({"success": True, "guide": {**guide, "width": width, "height": height}}), 201


@admin_routes.post("/api/admin/size-guides/generate")
@require_admin_json
@require_csrf
@allow_large_upload
def generate_admin_size_guide():
    """Draw a chart with Vertex and keep it, so it is drawn once and not per render."""
    if not current_app.config.get("ENABLE_AI_MODE", False):
        return json_error("AI generation is disabled on this server", 503)
    project_id = str(current_app.config.get("VERTEX_PROJECT_ID", "") or "").strip()
    if not project_id:
        project_id = str(catalog().get_settings().get("VERTEX_PROJECT_ID", "") or "").strip()
    if not project_id:
        return json_error("Vertex AI is not configured (no project id)", 400)
    payload = request.get_json(silent=True) or {}
    if request.form:
        payload = {**payload, **request.form.to_dict()}
    template = str(payload.get("prompt") or "").strip()
    preset = str(payload.get("preset") or "").strip()
    if not template and preset:
        template = preset_prompt(preset) or ""
        if not template:
            return json_error(f"Unknown size guide style: {preset}", 400)
    reference = None
    upload = request.files.get("reference")
    if upload is not None and upload.filename:
        data = upload.read()
        if len(data) > 8 * 1024 * 1024:
            return json_error("A reference image must be 8MB or smaller", 400)
        reference = (data, upload.mimetype or "image/png")
    elif preset:
        # A style carries the picture it was modelled on. Wording the admin
        # wrote gets no example attached to it: it is their design, not a copy
        # of one of ours.
        example = style_example_path(_guides_folder(), preset)
        if example is not None:
            mime = "image/png" if example.suffix == ".png" else "image/jpeg"
            reference = (example.read_bytes(), "image/webp" if example.suffix == ".webp" else mime)
    try:
        ratio = _checked_ratio()
        orientation = orientation_for_guide_ratio(ratio)
        # The sizes come from the family the ratio belongs to, in both units,
        # so the chart lists what the shop actually sells at that shape.
        _, unit, sizes = size_family_for_ratio(guide_ratio_shape(ratio))
        image = generate_size_guide(
            family=ratio,
            sizes=dual_unit_labels(sizes, unit),
            unit=unit,
            orientation=orientation,
            project_id=project_id,
            location=str(current_app.config.get("VERTEX_LOCATION", "global") or "global"),
            template=template or _guide_prompt_template(),
            reference=reference,
        )
        file_name, width, height = store_guide_image(image, _guides_folder())
    except SizeGuideError as error:
        return json_error(str(error), 400)
    except Exception as error:  # a model that will not answer is not a crash
        return json_error(str(error) or "The size guide could not be generated", 502)
    # Wording the admin edited is kept, so the next chart is drawn the same way.
    if template and not preset and template != _guide_prompt_template():
        catalog().set_settings({SIZE_GUIDE_PROMPT_KEY: template})
    guide = catalog().create_size_guide(
        {
            "name": str(payload.get("name", "")).strip()
            or f"{ratio} {preset or 'chart'} (AI)".replace("  ", " "),
            "ratio": ratio,
            "orientation": orientation,
            "file_name": file_name,
            "source": "ai",
        }
    )
    return jsonify({"success": True, "guide": {**guide, "width": width, "height": height}}), 201


@admin_routes.get("/api/admin/size-guides/styles/<style_key>/example")
@require_admin_json
def get_admin_style_example(style_key: str):
    example = style_example_path(_guides_folder(), style_key)
    if example is None:
        return json_error("No example for that style", 404)
    return send_file(example)


@admin_routes.post("/api/admin/size-guides/styles/<style_key>/example")
@require_admin_json
@require_csrf
@allow_large_upload
def set_admin_style_example(style_key: str):
    """The picture a style should be drawn in the manner of."""
    if not any(preset["key"] == style_key for preset in GUIDE_PROMPT_PRESETS):
        return json_error(f"Unknown size guide style: {style_key}", 404)
    upload = request.files.get("example")
    if upload is None or not upload.filename:
        return json_error("An example image is required", 400)
    try:
        store_style_example(upload, _guides_folder(), style_key)
    except SizeGuideError as error:
        return json_error(str(error), 400)
    return jsonify({"success": True, "style": style_key, "example": True}), 201


@admin_routes.delete("/api/admin/size-guides/styles/<style_key>/example")
@require_admin_json
@require_csrf
def delete_admin_style_example(style_key: str):
    example = style_example_path(_guides_folder(), style_key)
    if example is None:
        return json_error("No example for that style", 404)
    example.unlink(missing_ok=True)
    return jsonify({"success": True, "style": style_key, "example": False})


@admin_routes.get("/api/admin/size-guides/<int:guide_id>/asset")
@require_admin_json
def get_admin_size_guide_asset(guide_id: int):
    guide = catalog().get_size_guide(guide_id)
    if not guide:
        return json_error("Size guide not found", 404)
    try:
        path = guide_path(_guides_folder(), guide.get("file_name", ""))
    except SizeGuideError as error:
        return json_error(str(error), 400)
    if not path.is_file():
        return json_error("Size guide file is missing", 404)
    return send_file(path)


@admin_routes.delete("/api/admin/size-guides/<int:guide_id>")
@require_admin_json
@require_csrf
def delete_admin_size_guide(guide_id: int):
    try:
        guide = catalog().delete_size_guide(guide_id)
    except CatalogError as error:
        return json_error(str(error), 404)
    try:
        guide_path(_guides_folder(), guide.get("file_name", "")).unlink(missing_ok=True)
    except (SizeGuideError, OSError):
        pass  # The record is gone; a stray file is not worth failing over.
    return jsonify({"success": True, "guide_id": guide_id})


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
@allow_large_upload
def import_admin_templates():
    try:
        category_id = int(request.form.get("category_id", ""))
        templates = import_backgrounds(
            request.files.getlist("mockups"),
            category_id=category_id,
            drafts_folder=Path(current_app.config["DRAFT_TEMPLATES_FOLDER"]),
            catalog=catalog(),
        )
        # What runs on a newly added mockup is a setting of its own, separate
        # from the mode the studio is in: adding mockups and working on one are
        # different moments. "none" imports them and leaves the frames to you.
        engine = catalog().get_settings()
        import_mode = str(
            engine.get("CLASSIC_IMPORT_MODE")
            or current_app.config.get("CLASSIC_IMPORT_MODE", "auto")
        ).strip().lower() or "auto"
        if import_mode == "none":
            return jsonify({"success": True, "templates": templates}), 201
        detector = ClassicDetectionProvider(
            green_edge_expand=_setting_int(engine, current_app.config, "CLASSIC_GREEN_EDGE_EXPAND", 1),
            green_tolerance=_setting_int(engine, current_app.config, "CLASSIC_GREEN_TOLERANCE", 130),
            default_mode="green_frames_mockups" if import_mode == "green_frames" else "auto",
        )
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


def published_asset_path(templates_folder: Path, template_id: str, asset_name: str) -> Path | None:
    """One file from one published template's own folder, or nothing.

    The id and the name both arrive from the URL, so both are held to a single
    path segment and the resolved file is required to sit inside the template's
    own folder -- otherwise a name that climbs out of it ("..", a symlink, an
    absolute path on Windows) would serve any file the server can read, .env
    among them. The draft branch beside this one is already guarded this way.
    """
    def one_segment(value: str) -> bool:
        # Path("..").name is "", so the name check alone lets ".." through.
        return bool(value) and value not in (".", "..") and Path(value).name == value

    if not one_segment(template_id) or not one_segment(asset_name):
        return None
    root = templates_folder.resolve()
    template_folder = (root / template_id).resolve()
    asset_path = (template_folder / asset_name).resolve()
    if root not in template_folder.parents:
        return None
    if template_folder not in asset_path.parents or not asset_path.is_file():
        return None
    return asset_path


def derived_mask_response(template_id: str, asset_name: str):
    """The opening a template's own frames describe, drawn on request.

    Fifteen published templates name a mask.png that was never written beside
    them. What they do carry is the frames themselves, and the renderer already
    falls back to drawing the mask from those (mask_from_regions), so the
    editor asking for the file was the only thing left with nothing to show --
    an unclipped overlay and a 404 in the console. Drawing the same mask here
    keeps what the editor shows and what the render produces the same thing.
    """
    if asset_name != "mask.png":
        return None
    template = catalog().get_template(template_id)
    if not template:
        return None
    raw = template.get("raw_artwork_area")
    regions = raw.get("regions") if isinstance(raw, dict) else None
    if not regions:
        return None
    canvas = (int(template["canvas_width"]), int(template["canvas_height"]))
    if canvas[0] < 1 or canvas[1] < 1:
        return None
    from services.simple_mockup_service import mask_from_regions

    mask = green_opening_mask(template_id, template, regions, canvas) or mask_from_regions(
        regions, canvas
    )
    buffer = io.BytesIO()
    mask.save(buffer, format="PNG")
    buffer.seek(0)
    return send_file(buffer, mimetype="image/png")


def green_opening_mask(template_id, template, regions, canvas):
    """The opening the render will cut, for a template detected from green.

    Drawing the saved frames would be an honest picture of the frames and a
    misleading picture of the render: the render cuts the green it finds in the
    mockup, held inside those frames and pushed out by whatever the per-side
    amounts say. Building it the same way here is what keeps the editor's
    overlay and the finished mockup showing the same opening.
    """
    import numpy as np
    from PIL import Image

    from services.green_frame_mockup_service import (
        detect_green_frames,
        parse_green_frame_settings,
        reshape_opening,
    )
    from services.simple_mockup_service import mask_from_regions

    mode = raw_detection_mode(template)
    if mode not in {"green_frames_mockups", "color_pick", "frame_points"}:
        return None
    background = published_asset_path(
        Path(current_app.config["TEMPLATES_FOLDER"]), template_id, "background.png"
    )
    if background is None:
        return None
    settings = parse_green_frame_settings(template.get("effects"), template.get("fit_mode"))
    try:
        with Image.open(background) as image:
            state = detect_green_frames(image.convert("RGBA"), settings)
    except (OSError, ValueError):
        return None
    if not state.regions:
        return None
    # Wide open, the green test counts the whole canvas and says nothing about
    # where the opening is; the render falls back to the frames there, so this
    # does too rather than showing an opening the render will not cut.
    if state.green_count >= 0.9 * canvas[0] * canvas[1]:
        return None
    bounds = np.asarray(mask_from_regions(regions, canvas)) > 127
    reshape_opening(state, settings, bounds)
    return Image.fromarray(np.where(state.clip_mask, 255, 0).astype(np.uint8), mode="L")


def raw_detection_mode(template) -> str:
    raw = template.get("raw_artwork_area")
    if not isinstance(raw, dict):
        return ""
    return str(raw.get("mode") or raw.get("provider") or "")


@admin_routes.get("/api/admin/templates/<template_id>/asset/<asset_name>")
@require_admin_json
def admin_template_asset(template_id: str, asset_name: str):
    try:
        asset_path = draft_asset_path(
            Path(current_app.config["DRAFT_TEMPLATES_FOLDER"]), template_id, asset_name
        )
    except TemplateImportError:
        asset_path = published_asset_path(
            Path(current_app.config["TEMPLATES_FOLDER"]), template_id, asset_name
        )
        if asset_path is None:
            derived = derived_mask_response(template_id, asset_name)
            if derived is not None:
                return derived
            return json_error("Asset not found", 404)
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
        if payload.get("fit_mode") in {"cover", "contain", "stretch", "auto"}:
            changes["fit_mode"] = payload["fit_mode"]
        if "category_id" in payload:
            changes["category_id"] = int(payload["category_id"])
        if "effects" in payload:
            effects = payload["effects"]
            if effects is None or isinstance(effects, dict):
                changes["effects"] = effects
            else:
                return json_error("Effects must be a dictionary or null", 400)
        if "raw_artwork_area" in payload:
            raw_artwork_area = payload["raw_artwork_area"]
            if raw_artwork_area is None or isinstance(raw_artwork_area, dict):
                changes["raw_artwork_area"] = raw_artwork_area
            else:
                return json_error("Raw artwork area must be a dictionary or null", 400)
        if "mask_name" in payload and payload.get("mask_name") in {"mask.png", None, ""}:
            changes["mask_name"] = payload.get("mask_name") or None
            if not changes["mask_name"]:
                for folder in (current_app.config["DRAFT_TEMPLATES_FOLDER"], current_app.config["TEMPLATES_FOLDER"]):
                    mp = Path(folder) / template_id / "mask.png"
                    if mp.is_file():
                        try:
                            mp.unlink()
                        except Exception:
                            pass
        # The catalog holds the template's live state; every reader lays it over
        # the manifest (merge_template_record). The manifest is the snapshot the
        # template was published with and is not rewritten on every edit -- it
        # used to be, which put a file change in the working tree behind every
        # slider drag in the admin.
        updated = catalog().update_template(template_id, changes)
    except (ValueError, CatalogError, DetectionError) as error:
        return json_error(str(error), 400)
    return jsonify({"success": True, "template": updated})


@admin_routes.delete("/api/admin/templates/<template_id>")
@require_admin_json
@require_csrf
def delete_admin_template(template_id: str):
    template = catalog().get_template(template_id)
    if not template:
        return json_error("Template not found", 404)
    try:
        delete_template_assets(
            template_id,
            drafts_folder=Path(current_app.config["DRAFT_TEMPLATES_FOLDER"]),
            templates_folder=Path(current_app.config["TEMPLATES_FOLDER"]),
        )
        catalog().delete_template(template_id)
    except (CatalogError, TemplateImportError) as error:
        return json_error(str(error), 400)
    return jsonify({"success": True, "template_id": template_id})
@admin_routes.post("/api/admin/templates/<template_id>/reset-detection")
@require_admin_json
@require_csrf
def reset_admin_template_detection(template_id: str):
    template = catalog().get_template(template_id)
    if not template:
        return json_error("Template not found", 404)
    try:
        draft_mask = Path(current_app.config["DRAFT_TEMPLATES_FOLDER"]) / template_id / "mask.png"
        if draft_mask.is_file():
            draft_mask.unlink(missing_ok=True)
        pub_mask = Path(current_app.config["TEMPLATES_FOLDER"]) / template_id / "mask.png"
        if pub_mask.is_file():
            pub_mask.unlink(missing_ok=True)

        try:
            bg_path = draft_asset_path(Path(current_app.config["DRAFT_TEMPLATES_FOLDER"]), template_id, "background.png")
        except TemplateImportError:
            bg_path = Path(current_app.config["TEMPLATES_FOLDER"]) / template_id / "background.png"

        # Reset clears detection; it never runs a new one. Re-detecting here is
        # what made the button look like it did nothing -- the frames it wiped
        # reappeared immediately.
        from PIL import Image as _ResetImage

        with _ResetImage.open(bg_path) as background:
            canvas_width, canvas_height = background.size
        artwork_area = _centered_artwork_area(canvas_width, canvas_height)

        changes = {
            "artwork_area": artwork_area,
            "orientation": orientation_for_size(
                artwork_area["width"],
                artwork_area["height"],
            ),
            "raw_artwork_area": None,
            "mask_name": None,
            "detection_provider": None,
            "detection_confidence": None,
        }
        updated = catalog().update_template(template_id, changes)

        from services.simple_mockup_service import _GREEN_DETECTION_CACHE, _GREEN_DETECTION_LOCK
        with _GREEN_DETECTION_LOCK:
            _GREEN_DETECTION_CACHE.clear()
    except Exception as error:
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
        payload = request.get_json(silent=True) or {}
        mode = detection_mode_in_use(str(payload.get("mode") or "").strip() or None)
        point = payload.get("point")

        if mode in ("color_pick", "frame_points"):
            proposal, mask_img = _run_mask_detection(background, mode, payload)
            mask_path = background.parent / "mask.png"
            mask_img.save(str(mask_path))
            mask_name = "mask.png"
        else:
            provider = build_provider(catalog().get_settings(), current_app.config)
            if provider.__class__.__name__ == "ClassicDetectionProvider":
                proposal = provider.detect(background, mode=mode, point=point)
            else:
                proposal = provider.detect(background)
            mask_name = save_green_frame_mask_if_needed(provider, background, mode, proposal)

        if not mask_name:
            for folder in (current_app.config["DRAFT_TEMPLATES_FOLDER"], current_app.config["TEMPLATES_FOLDER"]):
                mp = Path(folder) / template_id / "mask.png"
                if mp.is_file():
                    try:
                        mp.unlink()
                    except Exception:
                        pass
            from services.simple_mockup_service import _GREEN_DETECTION_CACHE, _GREEN_DETECTION_LOCK
            with _GREEN_DETECTION_LOCK:
                _GREEN_DETECTION_CACHE.clear()

        if template.get("status") == "draft":
            changes = {
                "artwork_area": proposal.artwork_area,
                "orientation": orientation_for_size(
                    proposal.artwork_area["width"], proposal.artwork_area["height"]
                ),
                "detection_provider": proposal.provider,
                "detection_confidence": proposal.confidence,
                "raw_artwork_area": proposal.raw_artwork_area,
                "mask_name": mask_name,
            }
            preview = catalog().update_template(
                template_id,
                changes
            )
        else:
            preview = {
                **template,
                "artwork_area": proposal.artwork_area,
                "orientation": orientation_for_size(
                    proposal.artwork_area["width"], proposal.artwork_area["height"]
                ),
                "detection_provider": proposal.provider,
                "detection_confidence": proposal.confidence,
                "raw_artwork_area": proposal.raw_artwork_area,
                "mask_name": mask_name,
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
                "raw_artwork_area": proposal.raw_artwork_area,
            },
        }
    )


@admin_routes.post("/api/admin/templates/batch-detect")
@require_admin_json
@require_csrf
def batch_detect_admin_templates():
    data = request.json or {}
    template_ids = data.get("template_ids", [])
    if not isinstance(template_ids, list) or not template_ids:
        return json_error("Invalid or empty template_ids list", 400)

    mode = detection_mode_in_use(str(data.get("mode") or "").strip() or None)
    if mode in INTERACTIVE_MODES:
        return json_error(
            f"{mode.replace('_', ' ').title()} needs a colour or points picked on each mockup, "
            "so it cannot run over a batch. Switch to Auto detect or Green frames first.",
            400,
        )

    try:
        provider = build_provider(catalog().get_settings(), current_app.config)
    except Exception as e:
        return json_error(f"Failed to initialize detection provider: {e}", 500)

    draft_folder = Path(current_app.config["DRAFT_TEMPLATES_FOLDER"])
    templates_folder = Path(current_app.config["TEMPLATES_FOLDER"])
    cat = catalog()

    def process_template(template_id: str):
        template = cat.get_template(template_id)
        if not template:
            return {"template_id": template_id, "success": False, "error": "Template not found"}

        try:
            background = draft_asset_path(draft_folder, template_id, "background.png")
        except TemplateImportError:
            background = templates_folder / template_id / "background.png"

        try:
            # The same call the single Detect button makes, with the same mode.
            if provider.__class__.__name__ == "ClassicDetectionProvider":
                proposal = provider.detect(background, mode=mode)
            else:
                proposal = provider.detect(background)
            mask_name = save_green_frame_mask_if_needed(provider, background, mode, proposal)
            changes = {
                "artwork_area": proposal.artwork_area,
                "orientation": orientation_for_size(
                    proposal.artwork_area["width"], proposal.artwork_area["height"]
                ),
                "detection_provider": proposal.provider,
                "detection_confidence": proposal.confidence,
                "raw_artwork_area": proposal.raw_artwork_area,
            }
            if mask_name:
                changes["mask_name"] = mask_name
            preview = cat.update_template(template_id, changes)
            return {
                "template_id": template_id,
                "success": True,
                "template": preview,
                "proposal": {
                    "artwork_area": proposal.artwork_area,
                    "confidence": proposal.confidence,
                    "reason": proposal.reason,
                    "provider": proposal.provider,
                    "raw_artwork_area": proposal.raw_artwork_area,
                },
            }
        except DetectionError as error:
            return {"template_id": template_id, "success": False, "error": str(error)}
        except Exception as e:
            return {"template_id": template_id, "success": False, "error": str(e)}

    # The process-wide pool, so a second batch queues behind the first instead
    # of doubling the threads and the provider calls in flight.
    results = list(detection_pool().map(process_template, template_ids))

    return jsonify({"success": True, "results": results})



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
    # A blank field is nothing to save, not a value to reject: one control the
    # panel could not fill used to sink every other setting sent with it.
    settings = {
        key: str(payload[key]).strip()
        for key in SETTINGS_KEYS
        if key in payload and str(payload[key]).strip() != ""
    }
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
    if settings.get("CLASSIC_INTERNAL_MODE") not in {None, "auto", "green_frames_mockups"}:
        return json_error("Unsupported classic internal mode", 400)
    # "none" is a choice, not a missing value: it leaves no mode preselected,
    # and Detect frame runs the ordinary classic path.
    if settings.get("CLASSIC_SUBMODE") not in {None, "none", "auto", "frame_points", "green_frames", "color_pick"}:
        return json_error("Unsupported classic submode", 400)
    # Only what can run unattended belongs here: frame points and colour pick
    # need someone to point at something.
    if settings.get("CLASSIC_IMPORT_MODE") not in {None, "none", "auto", "green_frames"}:
        return json_error("Unsupported detection mode for new mockups", 400)
    if "CLASSIC_GREEN_EDGE_EXPAND" in settings:
        try:
            edge_expand = int(settings["CLASSIC_GREEN_EDGE_EXPAND"])
        except ValueError:
            return json_error("Green frame edge expansion must be a number", 400)
        if edge_expand < 0 or edge_expand > 255:
            return json_error("Green frame edge expansion must be between 0 and 255", 400)
    if "CLASSIC_GREEN_TOLERANCE" in settings:
        try:
            tolerance = int(settings["CLASSIC_GREEN_TOLERANCE"])
        except ValueError:
            return json_error("Green detection tolerance must be a number", 400)
        if tolerance < 10 or tolerance > 442:
            return json_error("Green detection tolerance must be between 10 and 442", 400)
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
                "raw_artwork_area": proposal.raw_artwork_area,
            },
        }
    )


@admin_routes.get("/api/admin/providers/status")
@require_admin_json
def get_providers_status():
    settings = catalog().get_settings()
    config = current_app.config

    classic_status = {
        "available": True,
        "provider": "classic",
        "message": "Classic Edge Detection is always available locally",
    }

    from services.vertex_detection_service import check_vertex_health

    vertex_project = settings.get("VERTEX_PROJECT_ID") or config.get("VERTEX_PROJECT_ID")
    vertex_location = settings.get("VERTEX_LOCATION") or config.get("VERTEX_LOCATION", "global")
    vertex_model = settings.get("VERTEX_MODEL") or config.get("VERTEX_MODEL", "gemini-2.5-flash")

    vertex_status = check_vertex_health(
        project_id=vertex_project,
        location=vertex_location,
        model=vertex_model,
        timeout_seconds=6.0,
    )

    local_url = settings.get("LOCAL_DETECTION_URL") or config.get("LOCAL_DETECTION_URL")
    local_available = bool(local_url and local_url.strip())
    local_status = {
        "available": local_available,
        "provider": "local",
        "error": None if local_available else "Local Detection URL is not configured",
    }

    return jsonify(
        {
            "success": True,
            "active_provider": settings.get("DETECTION_PROVIDER")
            or config.get("DETECTION_PROVIDER", "classic"),
            "providers": {
                "classic": classic_status,
                "vertex": vertex_status,
                "local": local_status,
            },
        }
    )


@admin_routes.get("/server-pulse")
def server_pulse_page():
    if not is_admin():
        return redirect(url_for("admin_routes.admin_login_page"))
    return render_template("admin/server_pulse.html", csrf_token=session["csrf_token"])


@admin_routes.get("/api/telemetry/summary")
@require_admin_json
def get_telemetry_summary():
    svc = current_app.extensions.get("telemetry_service")
    if not svc:
        return jsonify({"success": False, "error": "Telemetry service not available"}), 503
    return jsonify({"success": True, "data": svc.get_summary()})


@admin_routes.get("/api/telemetry/requests")
@require_admin_json
def get_telemetry_requests():
    svc = current_app.extensions.get("telemetry_service")
    if not svc:
        return jsonify({"success": False, "error": "Telemetry service not available"}), 503
    limit = min(200, max(1, int(request.args.get("limit", 60))))
    status_filter = request.args.get("filter")
    return jsonify({"success": True, "requests": svc.get_recent_requests(limit=limit, status_filter=status_filter)})


@admin_routes.get("/api/telemetry/errors")
@require_admin_json
def get_telemetry_errors():
    svc = current_app.extensions.get("telemetry_service")
    if not svc:
        return jsonify({"success": False, "error": "Telemetry service not available"}), 503
    limit = min(100, max(1, int(request.args.get("limit", 40))))
    return jsonify({"success": True, "errors": svc.get_recent_errors(limit=limit)})


@admin_routes.post("/api/telemetry/purge-temp")
@require_admin_json
@require_csrf
def purge_telemetry_temp():
    svc = current_app.extensions.get("telemetry_service")
    if not svc:
        return jsonify({"success": False, "error": "Telemetry service not available"}), 503
    max_age_hours = float(request.args.get("max_age_hours", 24.0))
    result = svc.purge_temp_files(max_age_hours=max_age_hours)
    return jsonify({"success": True, **result})


@admin_routes.post("/api/telemetry/clear-logs")
@require_admin_json
@require_csrf
def clear_telemetry_logs():
    svc = current_app.extensions.get("telemetry_service")
    if not svc:
        return jsonify({"success": False, "error": "Telemetry service not available"}), 503
    svc.clear_logs()
    return jsonify({"success": True, "message": "Telemetry logs cleared"})
