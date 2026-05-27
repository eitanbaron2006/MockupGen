# Admin Template Detection And Vertex Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a real Flask admin application for batch mockup-template import, reviewable artwork-area detection through Vertex AI or configured alternatives, and category/orientation-based public template selection.

**Architecture:** Keep the existing Pillow `simple` renderer as the final render engine. Add a small SQLite catalog that controls draft and active templates, a Flask-served admin UI, and provider-based artwork-area detection used only during template import or re-detection. Vertex uses Google's official `google-genai` SDK with structured JSON output and defaults to the documented `gemini-2.5-flash` model; public renders never invoke AI after activation.

**Tech Stack:** Python 3, Flask, SQLite (`sqlite3`), Pillow, vanilla HTML/CSS/JavaScript, `google-genai` for Vertex AI, `requests` for an optional OpenAI-compatible local vision endpoint, pytest.

---

## Constraints Before Execution

- The project is not currently a Git repository. All commit steps below are conditional: run them only if a repository has been initialized before that task; otherwise keep a concise changed-files record.
- Do not edit, stop, start, inspect internally, or migrate any Supabase/Docker resources. This implementation uses only a local SQLite file inside this project.
- Do not expose Vertex credentials or local provider keys through the browser. The UI stores non-secret provider settings only.
- Do not route artwork-area detection through the existing unimplemented `mode=ai` final-render extension point; they are separate features.
- Use `gemini-2.5-flash` as the initial Vertex model default. Google's current structured-output documentation explicitly demonstrates that model with the `google-genai` SDK and `response_schema`.

## File Map

### Existing Files To Modify

| File | Responsibility After Change |
| --- | --- |
| `requirements.txt` | Add `google-genai` and `requests`. |
| `.env.example` | Document admin, SQLite, Vertex, and local detector configuration. |
| `config.py` | Parse database, admin, draft-folder, and detection-provider settings. |
| `app.py` | Initialize folders/catalog and register admin routes. |
| `routes/mockup_routes.py` | Add categories/filtering and automatic active-template selection. |
| `services/simple_mockup_service.py` | Reuse manifest rendering and expose filtered public manifest summaries where needed. |
| `README.md` | Document admin startup, Vertex credentials, provider behavior, and API changes. |
| `tests/test_mockup_api.py` | Preserve renderer coverage and adapt helpers for initialized catalog where public behavior changes. |

### Files To Create

| File | Responsibility |
| --- | --- |
| `routes/admin_routes.py` | Admin HTML pages, session/CSRF protection, and `/api/admin/*` endpoints. |
| `services/catalog_service.py` | SQLite schema, category/template/settings CRUD, active-template selection, existing-template seed. |
| `services/template_import_service.py` | Draft asset creation, preview generation, rectangle validation, manifest publication. |
| `services/detection_service.py` | Provider protocol, proposal model, validation, provider factory/errors. |
| `services/vertex_detection_service.py` | Real Vertex multimodal structured-output detector. |
| `services/local_detection_service.py` | Configured OpenAI-compatible local detector. |
| `services/classic_detection_service.py` | Cheap local contour-based baseline detector. |
| `templates/admin/login.html` | Admin login UI. |
| `templates/admin/index.html` | Full internal template-management UI. |
| `static/admin/admin.css` | EtsyAutoLister-aligned UI design tokens and single-viewport layout. |
| `static/admin/admin.js` | Admin fetch/state, batch import, queue, rectangle editing, provider settings. |
| `tests/helpers.py` | Image, manifest, and authenticated-admin test helpers. |
| `tests/test_catalog_service.py` | SQLite schema/seed/query/selection tests. |
| `tests/test_admin_api.py` | Login, CSRF, category, import, activation, settings endpoint tests. |
| `tests/test_detection_services.py` | Detection proposal validation, classic, Vertex parsing/failure, local adapter tests. |
| `tests/test_public_selection.py` | Public category/template filtering and automatic render-selection tests. |

### Runtime Folders To Create

| Folder | Responsibility |
| --- | --- |
| `data/` | Holds `mockup_catalog.sqlite3`; keep a `.gitkeep`, ignore generated DB. |
| `template_drafts/` | Holds unapproved imported assets; not publicly served. |

## Task 1: Configuration, Dependencies, And Application Bootstrap

**Files:**
- Modify: `requirements.txt`
- Modify: `.env.example`
- Modify: `config.py`
- Modify: `app.py`
- Create: `tests/helpers.py`
- Create: `tests/test_app_configuration.py`
- Create: `data/.gitkeep`
- Create: `template_drafts/.gitkeep`

- [ ] **Step 1: Extract reusable test helpers and write failing configuration tests**

Create `tests/helpers.py` with the existing image/template helper functions and an app builder that includes the new database and draft locations:

```python
import io
import json
from pathlib import Path

from PIL import Image


def image_bytes(size=(10, 10), color=(20, 220, 40, 255)) -> io.BytesIO:
    stream = io.BytesIO()
    Image.new("RGBA", size, color).save(stream, format="PNG")
    stream.seek(0)
    return stream


def save_image(path: Path, size: tuple[int, int], color: tuple[int, int, int, int]) -> None:
    Image.new("RGBA", size, color).save(path, format="PNG")


def striped_image_bytes() -> io.BytesIO:
    image = Image.new("RGBA", (8, 4), (20, 220, 40, 255))
    for y in range(4):
        for x in range(2):
            image.putpixel((x, y), (250, 230, 10, 255))
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    stream.seek(0)
    return stream


def write_template(
    templates_folder: Path,
    template_id: str = "template_001",
    *,
    fit_mode: str = "cover",
    mask: str | None = None,
) -> Path:
    folder = templates_folder / template_id
    folder.mkdir(parents=True, exist_ok=True)
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
        "preview": "preview.png",
        "supported_modes": ["simple"],
        "output_format": "png",
    }
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    save_image(folder / "background.png", (10, 10), (200, 20, 20, 255))
    foreground = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    foreground.putpixel((0, 0), (20, 30, 240, 255))
    foreground.save(folder / "foreground.png")
    save_image(folder / "preview.png", (10, 10), (200, 20, 20, 255))
    return folder


def build_app(tmp_path: Path, **overrides):
    from app import create_app

    settings = {
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "ADMIN_PASSWORD": "test-password",
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "OUTPUT_FOLDER": str(tmp_path / "outputs"),
        "TEMPLATES_FOLDER": str(tmp_path / "templates_data"),
        "DRAFT_TEMPLATES_FOLDER": str(tmp_path / "template_drafts"),
        "DATABASE_PATH": str(tmp_path / "data" / "catalog.sqlite3"),
        "ENABLE_SIMPLE_MODE": True,
        "DETECTION_PROVIDER": "classic",
        **overrides,
    }
    return create_app(settings)
```

Create `tests/test_app_configuration.py`:

```python
from pathlib import Path

from tests.helpers import build_app


def test_create_app_initializes_data_and_draft_locations(tmp_path):
    app = build_app(tmp_path)

    assert Path(app.config["DATABASE_PATH"]).parent.is_dir()
    assert Path(app.config["DRAFT_TEMPLATES_FOLDER"]).is_dir()
    assert app.config["VERTEX_MODEL"] == "gemini-2.5-flash"
```

- [ ] **Step 2: Run the new test and confirm it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_app_configuration.py -q
```

Expected: failure because `DRAFT_TEMPLATES_FOLDER`, `DATABASE_PATH`, or `VERTEX_MODEL` is not yet configured/created.

- [ ] **Step 3: Add dependencies and environment configuration**

Append dependencies to `requirements.txt`:

```text
google-genai>=1.0,<2.0
requests>=2.32,<3.0
```

Add to `.env.example`:

```dotenv
ADMIN_PASSWORD=change-this-admin-password
SECRET_KEY=change-this-session-secret
DATABASE_PATH=data/mockup_catalog.sqlite3
DRAFT_TEMPLATES_FOLDER=template_drafts
DETECTION_PROVIDER=classic
VERTEX_PROJECT_ID=
VERTEX_LOCATION=global
VERTEX_MODEL=gemini-2.5-flash
GOOGLE_APPLICATION_CREDENTIALS=
LOCAL_VISION_BASE_URL=
LOCAL_VISION_MODEL=
LOCAL_VISION_API_KEY=
```

Extend `config.py` with:

```python
DATABASE_PATH = _folder("DATABASE_PATH", "data/mockup_catalog.sqlite3")
DRAFT_TEMPLATES_FOLDER = _folder("DRAFT_TEMPLATES_FOLDER", "template_drafts")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
SECRET_KEY = os.getenv("SECRET_KEY", "")
DETECTION_PROVIDER = os.getenv("DETECTION_PROVIDER", "classic").strip().lower()
VERTEX_PROJECT_ID = os.getenv("VERTEX_PROJECT_ID", "").strip()
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "global").strip()
VERTEX_MODEL = os.getenv("VERTEX_MODEL", "gemini-2.5-flash").strip()
LOCAL_VISION_BASE_URL = os.getenv("LOCAL_VISION_BASE_URL", "").strip()
LOCAL_VISION_MODEL = os.getenv("LOCAL_VISION_MODEL", "").strip()
LOCAL_VISION_API_KEY = os.getenv("LOCAL_VISION_API_KEY", "").strip()
```

Expose every value on `Config`, and set Flask session configuration:

```python
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
```

Modify `app.py` initialization:

```python
for key in ("UPLOAD_FOLDER", "OUTPUT_FOLDER", "TEMPLATES_FOLDER", "DRAFT_TEMPLATES_FOLDER"):
    Path(app.config[key]).mkdir(parents=True, exist_ok=True)
Path(app.config["DATABASE_PATH"]).parent.mkdir(parents=True, exist_ok=True)
app.secret_key = app.config["SECRET_KEY"]
```

- [ ] **Step 4: Add runtime data folders and run configuration tests**

Create `data/.gitkeep` and `template_drafts/.gitkeep`; do not create a database file manually.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_app_configuration.py tests\test_mockup_api.py -q
```

Expected: new configuration test passes and existing renderer tests remain green after helper imports are updated.

- [ ] **Step 5: Commit if Git exists**

```powershell
git add requirements.txt .env.example config.py app.py data/.gitkeep template_drafts/.gitkeep tests/helpers.py tests/test_app_configuration.py tests/test_mockup_api.py
git commit -m "feat: configure admin detection runtime"
```

Skip this step if `git rev-parse --is-inside-work-tree` fails.

## Task 2: SQLite Catalog And Existing Template Seed

**Files:**
- Create: `services/catalog_service.py`
- Create: `tests/test_catalog_service.py`
- Modify: `app.py`

- [ ] **Step 1: Write failing catalog tests**

Create `tests/test_catalog_service.py`:

```python
from services.catalog_service import CatalogService
from tests.helpers import write_template


def test_initialize_schema_seeds_existing_manifest_as_active_template(tmp_path):
    templates = tmp_path / "templates_data"
    write_template(templates)
    catalog = CatalogService(tmp_path / "data" / "catalog.sqlite3")

    catalog.initialize(templates)

    categories = catalog.list_categories(active_only=True)
    templates_list = catalog.list_templates(status="active")
    assert categories[0]["slug"] == "uncategorized"
    assert templates_list[0]["template_id"] == "template_001"
    assert templates_list[0]["status"] == "active"


def test_select_template_matches_category_orientation_and_ratio(tmp_path):
    catalog = CatalogService(tmp_path / "catalog.sqlite3")
    catalog.initialize(tmp_path / "templates_data")
    category = catalog.create_category("Wall Art", "wall-art")
    catalog.create_template(category["id"], "portrait-close", "Portrait", status="active",
                            orientation="portrait", artwork_width=800, artwork_height=1200)
    catalog.create_template(category["id"], "portrait-wide", "Portrait Wide", status="active",
                            orientation="portrait", artwork_width=900, artwork_height=1000)

    match = catalog.select_active_template("wall-art", "portrait", artwork_ratio=0.67)

    assert match["template_id"] == "portrait-close"
```

- [ ] **Step 2: Run catalog tests to confirm failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_catalog_service.py -q
```

Expected: import failure for missing `services.catalog_service`.

- [ ] **Step 3: Implement focused SQLite catalog service**

Create `services/catalog_service.py` with a `sqlite3` connection helper, row mapping, explicit transactions, and schema:

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  archived INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS templates (
  id TEXT PRIMARY KEY,
  template_id TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  category_id INTEGER NOT NULL REFERENCES categories(id),
  status TEXT NOT NULL CHECK (status IN ('draft', 'review', 'active', 'archived', 'failed')),
  orientation TEXT CHECK (orientation IN ('square', 'portrait', 'landscape')),
  fit_mode TEXT NOT NULL DEFAULT 'cover',
  background_filename TEXT NOT NULL,
  foreground_filename TEXT,
  mask_filename TEXT,
  preview_filename TEXT,
  canvas_width INTEGER,
  canvas_height INTEGER,
  artwork_x INTEGER,
  artwork_y INTEGER,
  artwork_width INTEGER,
  artwork_height INTEGER,
  detection_provider TEXT,
  detection_model TEXT,
  detection_confidence REAL,
  detection_message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS detection_settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  provider TEXT NOT NULL,
  vertex_project_id TEXT NOT NULL DEFAULT '',
  vertex_location TEXT NOT NULL DEFAULT 'global',
  vertex_model TEXT NOT NULL DEFAULT 'gemini-2.5-flash',
  local_base_url TEXT NOT NULL DEFAULT '',
  local_model TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);
"""
```

Implement public methods named `initialize`, `create_category`,
`update_category`, `list_categories`, `create_template`, `update_template`,
`get_template`, `list_templates`, `get_detection_settings`,
`save_detection_settings`, and `select_active_template`. Every SQL query uses
bound `?` parameters for values; every mutating method opens a transaction and
returns the inserted or updated row as a dictionary.

`select_active_template()` must order by:

```sql
ABS((CAST(artwork_width AS REAL) / artwork_height) - ?) ASC, template_id ASC
```

When seeding existing manifests, create an `uncategorized` category and insert
only valid manifests that are absent from the catalog.

- [ ] **Step 4: Initialize the catalog from the application factory**

In `app.py`, after directories exist:

```python
from services.catalog_service import CatalogService

catalog = CatalogService(Path(app.config["DATABASE_PATH"]))
catalog.initialize(Path(app.config["TEMPLATES_FOLDER"]))
app.extensions["catalog"] = catalog
```

- [ ] **Step 5: Run tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_catalog_service.py tests\test_mockup_api.py -q
```

Expected: PASS, including repeat initialization without duplicate seeded records.

- [ ] **Step 6: Commit if Git exists**

```powershell
git add services/catalog_service.py app.py tests/test_catalog_service.py
git commit -m "feat: add sqlite mockup catalog"
```

## Task 3: Admin Authentication, CSRF, And Category API

**Files:**
- Create: `routes/admin_routes.py`
- Create: `templates/admin/login.html`
- Create: `templates/admin/index.html`
- Create: `static/admin/admin.css`
- Create: `static/admin/admin.js`
- Create: `tests/test_admin_api.py`
- Modify: `app.py`

- [ ] **Step 1: Write failing authentication and category API tests**

Create `tests/test_admin_api.py`:

```python
from tests.helpers import build_app


def login(client):
    response = client.post("/api/admin/login", json={"password": "test-password"})
    assert response.status_code == 200
    return response.get_json()["csrf_token"]


def test_admin_requires_login_and_csrf_for_mutations(tmp_path):
    client = build_app(tmp_path).test_client()
    assert client.get("/api/admin/categories").status_code == 401
    csrf = login(client)
    assert client.post("/api/admin/categories", json={"name": "Wall Art"}).status_code == 403
    response = client.post(
        "/api/admin/categories",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Wall Art"},
    )
    assert response.status_code == 201
    assert response.get_json()["category"]["slug"] == "wall-art"


def test_admin_ui_routes_render_after_authentication(tmp_path):
    client = build_app(tmp_path).test_client()
    assert client.get("/admin").status_code == 302
    login(client)
    page = client.get("/admin")
    assert page.status_code == 200
    assert b"Mockup Studio" in page.data
```

- [ ] **Step 2: Run the tests to confirm missing routes fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_admin_api.py -q
```

Expected: `404` failures for `/api/admin/login` and `/admin`.

- [ ] **Step 3: Implement protected admin routes**

Create `routes/admin_routes.py` with:

```python
import hmac
import secrets
from functools import wraps

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, session, url_for

admin_routes = Blueprint("admin_routes", __name__)


def _catalog():
    return current_app.extensions["catalog"]


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            return jsonify({"success": False, "error": "Authentication required"}), 401
        return view(*args, **kwargs)
    return wrapped


def csrf_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        supplied = request.headers.get("X-CSRF-Token", "")
        expected = session.get("csrf_token", "")
        if not supplied or not expected or not hmac.compare_digest(supplied, expected):
            return jsonify({"success": False, "error": "CSRF validation failed"}), 403
        return view(*args, **kwargs)
    return wrapped
```

Add login/logout, admin HTML routes, and category endpoints. Login uses
`hmac.compare_digest()` against `current_app.config["ADMIN_PASSWORD"]`;
successful login stores `is_admin=True` and `csrf_token=secrets.token_urlsafe(32)`.

- [ ] **Step 4: Create the production admin shell**

Create `templates/admin/login.html` with a password form posting through
JavaScript to `/api/admin/login`, and `templates/admin/index.html` containing:

```html
<div class="studio-shell">
  <aside class="sidebar">
    <div class="brand">Mockup <em>Studio</em><strong>.</strong></div>
    <section id="categories"></section>
  </aside>
  <main class="workspace">
    <header class="toolbar">
      <button id="open-engine">AI engine</button>
      <button id="import-mockups">Import mockups</button>
    </header>
    <section class="columns">
      <aside id="import-queue"></aside>
      <section id="review-canvas"></section>
      <aside id="template-inspector"></aside>
    </section>
  </main>
</div>
```

Port the approved palette/layout from the prototype into
`static/admin/admin.css`, using `height: 100dvh; overflow: hidden` and a
scrollable `#import-queue`. Do not include any prototype banner or fake
detection action.

Create `static/admin/admin.js` with session-aware fetch:

```javascript
let csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (!["GET", "HEAD"].includes((options.method || "GET").toUpperCase())) {
    headers["X-CSRF-Token"] = csrfToken;
  }
  const response = await fetch(path, { ...options, headers });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "Request failed");
  return payload;
}
```

- [ ] **Step 5: Register blueprint and run tests**

In `app.py`:

```python
from routes.admin_routes import admin_routes
app.register_blueprint(admin_routes)
```

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_admin_api.py tests\test_mockup_api.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit if Git exists**

```powershell
git add routes/admin_routes.py templates/admin static/admin app.py tests/test_admin_api.py
git commit -m "feat: add protected admin workspace"
```

## Task 4: Batch Import, Draft Assets, Review Edits, And Activation

**Files:**
- Create: `services/template_import_service.py`
- Modify: `routes/admin_routes.py`
- Modify: `static/admin/admin.js`
- Modify: `templates/admin/index.html`
- Modify: `tests/test_admin_api.py`

- [ ] **Step 1: Write failing draft import and activation tests**

Add to `tests/test_admin_api.py`:

```python
from pathlib import Path

from tests.helpers import image_bytes


def test_batch_import_creates_drafts_and_previews_without_public_manifest(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    csrf = login(client)
    category = client.post(
        "/api/admin/categories",
        headers={"X-CSRF-Token": csrf},
        json={"name": "Wall Art"},
    ).get_json()["category"]

    response = client.post(
        "/api/admin/templates/import",
        headers={"X-CSRF-Token": csrf},
        data={
            "category_id": str(category["id"]),
            "mockups": [
                (image_bytes((40, 40)), "one.png"),
                (image_bytes((30, 50)), "two.png"),
            ],
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    templates = response.get_json()["templates"]
    assert len(templates) == 2
    draft_folder = Path(app.config["DRAFT_TEMPLATES_FOLDER"]) / templates[0]["template_id"]
    assert (draft_folder / "background.png").exists()
    assert (draft_folder / "preview.png").exists()
    assert not (draft_folder / "manifest.json").exists()


def test_activation_writes_manifest_and_allows_missing_foreground(tmp_path):
    app = build_app(tmp_path)
    client = app.test_client()
    csrf = login(client)
    category_id = client.post(
        "/api/admin/categories", headers={"X-CSRF-Token": csrf}, json={"name": "Wall Art"}
    ).get_json()["category"]["id"]
    imported = client.post(
        "/api/admin/templates/import",
        headers={"X-CSRF-Token": csrf},
        data={"category_id": str(category_id), "mockups": [(image_bytes((40, 40)), "one.png")]},
        content_type="multipart/form-data",
    ).get_json()["templates"][0]
    template_id = imported["template_id"]
    client.patch(
        f"/api/admin/templates/{template_id}",
        headers={"X-CSRF-Token": csrf},
        json={"artwork_area": {"x": 4, "y": 4, "width": 32, "height": 32}, "orientation": "square"},
    )

    response = client.post(f"/api/admin/templates/{template_id}/activate", headers={"X-CSRF-Token": csrf})

    assert response.status_code == 200
    manifest = Path(app.config["TEMPLATES_FOLDER"]) / template_id / "manifest.json"
    assert manifest.exists()
    assert '"foreground": null' in manifest.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run the tests and confirm endpoint failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_admin_api.py -q
```

Expected: failing `404` for template import/update/activate endpoints.

- [ ] **Step 3: Implement import and publication service**

Create `services/template_import_service.py` with an `ArtworkArea` dataclass
containing integer `x`, `y`, `width`, and `height`, and a
`TemplateImportService` exposing `import_background`, `update_review`,
`attach_optional_asset`, and `activate`.

Implementation requirements:

- Store drafts only under `DRAFT_TEMPLATES_FOLDER/<template_id>/`.
- Convert background uploads to RGBA PNG as `background.png`.
- Create a bounded `preview.png` thumbnail with `ImageOps.contain()`.
- Validate rectangle bounds against stored canvas dimensions.
- Store updates in SQLite through `CatalogService`.
- On activation, create a new target folder under `TEMPLATES_FOLDER`; refuse
  to overwrite any existing folder.
- Copy background, preview, optional foreground, and optional mask into the
  published folder, then write this manifest:

```python
manifest = {
    "template_id": template["template_id"],
    "name": template["name"],
    "canvas_width": template["canvas_width"],
    "canvas_height": template["canvas_height"],
    "artwork_area": {
        "x": template["artwork_x"],
        "y": template["artwork_y"],
        "width": template["artwork_width"],
        "height": template["artwork_height"],
    },
    "fit_mode": template["fit_mode"],
    "background": "background.png",
    "foreground": template["foreground_filename"],
    "mask": template["mask_filename"],
    "preview": "preview.png",
    "supported_modes": ["simple"],
    "output_format": "png",
}
```

- [ ] **Step 4: Add admin template endpoints**

In `routes/admin_routes.py`, add authenticated, CSRF-protected endpoints for
`POST /api/admin/templates/import`, `PATCH /api/admin/templates/<template_id>`,
`POST /api/admin/templates/<template_id>/assets/<asset_kind>`, and
`POST /api/admin/templates/<template_id>/activate`. Each endpoint calls the
corresponding `TemplateImportService` method and returns the resulting
template dictionary.

Return clear JSON validation failures; allow `asset_kind` only in
`{"foreground", "mask"}`.

- [ ] **Step 5: Wire the real queue and canvas interactions in the admin JavaScript**

Implement:

```javascript
async function importMockups(files, categoryId) {
  const data = new FormData();
  data.append("category_id", categoryId);
  [...files].forEach((file) => data.append("mockups", file));
  const payload = await api("/api/admin/templates/import", { method: "POST", body: data });
  state.templates.unshift(...payload.templates);
  renderQueue();
}

async function saveRectangle(templateId, rectangle, orientation) {
  return api(`/api/admin/templates/${templateId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ artwork_area: rectangle, orientation }),
  });
}

async function activateTemplate(templateId) {
  return api(`/api/admin/templates/${templateId}/activate`, { method: "POST" });
}
```

The canvas selection box must send exact pixel coordinates, not demo
percentages; convert DOM scale back to native image pixels.

- [ ] **Step 6: Run tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_admin_api.py tests\test_mockup_api.py -q
```

Expected: PASS for imports, optional foreground behavior, activation, and all
existing render tests.

- [ ] **Step 7: Commit if Git exists**

```powershell
git add services/template_import_service.py routes/admin_routes.py templates/admin/index.html static/admin/admin.js tests/test_admin_api.py
git commit -m "feat: add admin template import and activation"
```

## Task 5: Detection Contract And Classic Baseline

**Files:**
- Create: `services/detection_service.py`
- Create: `services/classic_detection_service.py`
- Create: `tests/test_detection_services.py`
- Modify: `routes/admin_routes.py`

- [ ] **Step 1: Write failing detector contract and classic tests**

Create `tests/test_detection_services.py`:

```python
from pathlib import Path

from PIL import Image, ImageDraw
import pytest

from services.detection_service import DetectionProposal, InvalidDetectionProposal, validate_proposal
from services.classic_detection_service import ClassicArtworkAreaDetector


def test_validate_proposal_rejects_out_of_bounds_rectangle():
    proposal = DetectionProposal(95, 10, 20, 50, "portrait", 0.8, "fake", "fake")
    with pytest.raises(InvalidDetectionProposal):
        validate_proposal(proposal, canvas_width=100, canvas_height=100)


def test_classic_detector_finds_high_contrast_rectangular_artwork_area(tmp_path):
    path = tmp_path / "framed.png"
    image = Image.new("RGB", (200, 200), "white")
    ImageDraw.Draw(image).rectangle((45, 35, 155, 165), outline="black", width=4)
    image.save(path)

    proposal = ClassicArtworkAreaDetector().detect(path)

    assert abs(proposal.x - 45) <= 6
    assert abs(proposal.y - 35) <= 6
    assert proposal.orientation == "portrait"
```

- [ ] **Step 2: Run tests to confirm missing service failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_detection_services.py -q
```

Expected: module import failure.

- [ ] **Step 3: Define the detector contract and validation**

Create `services/detection_service.py`:

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class DetectionProposal:
    x: int
    y: int
    width: int
    height: int
    orientation: str
    confidence: float
    provider: str
    model: str
    message: str | None = None


class ArtworkAreaDetector(Protocol):
    def detect(self, image_path: Path) -> DetectionProposal:
        raise NotImplementedError


class DetectionError(RuntimeError):
    pass


class DetectionConfigurationError(DetectionError):
    pass


class InvalidDetectionProposal(DetectionError):
    pass


def orientation_for_area(width: int, height: int, tolerance: float = 0.08) -> str:
    ratio = width / height
    if abs(ratio - 1.0) <= tolerance:
        return "square"
    return "landscape" if ratio > 1 else "portrait"


def validate_proposal(proposal: DetectionProposal, *, canvas_width: int, canvas_height: int) -> DetectionProposal:
    if proposal.width <= 0 or proposal.height <= 0:
        raise InvalidDetectionProposal("Detected artwork area dimensions must be positive")
    if proposal.x < 0 or proposal.y < 0 or proposal.x + proposal.width > canvas_width or proposal.y + proposal.height > canvas_height:
        raise InvalidDetectionProposal("Detected artwork area is outside the source image")
    if proposal.width * proposal.height < (canvas_width * canvas_height) * 0.01:
        raise InvalidDetectionProposal("Detected artwork area is implausibly small")
    if proposal.orientation not in {"square", "portrait", "landscape"}:
        raise InvalidDetectionProposal("Detector returned an unsupported orientation")
    return proposal
```

- [ ] **Step 4: Implement the classic detector**

Create `services/classic_detection_service.py` as a conservative free
baseline:

```python
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

from services.detection_service import (
    DetectionError,
    DetectionProposal,
    orientation_for_area,
    validate_proposal,
)


class ClassicArtworkAreaDetector:
    def detect(self, image_path: Path) -> DetectionProposal:
        with Image.open(image_path) as source:
            width, height = source.size
            edge_map = ImageOps.grayscale(source).filter(ImageFilter.FIND_EDGES)
            threshold = edge_map.point(lambda value: 255 if value >= 96 else 0)
            bounds = threshold.getbbox()
        if bounds is None:
            raise DetectionError("Classic detector could not identify a reliable rectangular artwork area")
        left, top, right, bottom = bounds
        area_width = right - left
        area_height = bottom - top
        proposal = DetectionProposal(
            x=left,
            y=top,
            width=area_width,
            height=area_height,
            orientation=orientation_for_area(area_width, area_height),
            confidence=0.55,
            provider="classic",
            model="edge-threshold-v1",
            message="Classic edge-based proposal requires review.",
        )
        return validate_proposal(proposal, canvas_width=width, canvas_height=height)
```

This baseline remains low-confidence and review-only; it is a free fallback
and benchmark comparison, not an auto-activation engine.

- [ ] **Step 5: Add `/detect` route using an injectable provider factory**

In `services/detection_service.py`, add:

```python
def build_detector(settings: dict, config: dict) -> ArtworkAreaDetector:
    override = config.get("DETECTOR_FACTORY")
    if override:
        return override(settings, config)
    if settings["provider"] == "classic":
        from services.classic_detection_service import ClassicArtworkAreaDetector
        return ClassicArtworkAreaDetector()
    if settings["provider"] == "vertex":
        from services.vertex_detection_service import VertexArtworkAreaDetector
        return VertexArtworkAreaDetector(
            settings["vertex_project_id"],
            settings["vertex_location"],
            settings["vertex_model"],
        )
    if settings["provider"] == "local":
        from services.local_detection_service import LocalArtworkAreaDetector
        return LocalArtworkAreaDetector(
            settings["local_base_url"],
            settings["local_model"],
            config.get("LOCAL_VISION_API_KEY", ""),
        )
    raise DetectionConfigurationError("Unsupported detection provider")
```

In `routes/admin_routes.py`, add an authenticated, CSRF-protected
`POST /api/admin/templates/<template_id>/detect` route.

The route loads the draft background, invokes the configured provider,
validates the proposal, stores it in SQLite with `status="review"`, and
returns it. On detection failure it stores `detection_message` and returns a
visible JSON error.

- [ ] **Step 6: Test endpoint behavior with a fake injected provider**

Add a test that supplies:

```python
def fake_factory(_settings, _config):
    class FakeDetector:
        def detect(self, _path):
            return DetectionProposal(4, 4, 32, 32, "square", 0.98, "fake", "fixture")
    return FakeDetector()
```

Configure `DETECTOR_FACTORY=fake_factory`, import one draft, call
`/detect`, and assert the returned/stored rectangle.

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_detection_services.py tests\test_admin_api.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit if Git exists**

```powershell
git add services/detection_service.py services/classic_detection_service.py routes/admin_routes.py tests/test_detection_services.py tests/test_admin_api.py
git commit -m "feat: add artwork detection contract and baseline"
```

## Task 6: Vertex AI Detector And Provider Settings UI

**Files:**
- Create: `services/vertex_detection_service.py`
- Modify: `services/detection_service.py`
- Modify: `routes/admin_routes.py`
- Modify: `templates/admin/index.html`
- Modify: `static/admin/admin.js`
- Modify: `tests/test_detection_services.py`
- Modify: `tests/test_admin_api.py`

- [ ] **Step 1: Write failing Vertex parsing and connection error tests**

Add to `tests/test_detection_services.py`:

```python
import json

from services.vertex_detection_service import VertexArtworkAreaDetector


class FakeResponse:
    text = json.dumps({
        "x": 12, "y": 15, "width": 70, "height": 90,
        "orientation": "portrait", "confidence": 0.93,
        "message": "Front-facing insert area"
    })


class FakeModels:
    def generate_content(self, **_kwargs):
        return FakeResponse()


class FakeClient:
    models = FakeModels()


def test_vertex_detector_parses_structured_proposal(tmp_path):
    path = tmp_path / "mockup.png"
    Image.new("RGB", (100, 120), "white").save(path)
    detector = VertexArtworkAreaDetector("project", "global", "gemini-2.5-flash", client=FakeClient())

    proposal = detector.detect(path)

    assert proposal.provider == "vertex"
    assert proposal.model == "gemini-2.5-flash"
    assert proposal.width == 70
```

Add API tests asserting `/api/admin/detection/test` returns `503` when Vertex
project configuration is absent and maps provider failures to visible JSON.

- [ ] **Step 2: Run tests to confirm Vertex service is missing**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_detection_services.py tests\test_admin_api.py -q
```

Expected: import or endpoint failure.

- [ ] **Step 3: Implement real Vertex provider with official SDK shape**

Create `services/vertex_detection_service.py`:

```python
import json
from pathlib import Path

from google import genai
from google.genai import types
from PIL import Image

from services.detection_service import DetectionConfigurationError, DetectionError, DetectionProposal, validate_proposal

DETECTION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "x": {"type": "INTEGER"},
        "y": {"type": "INTEGER"},
        "width": {"type": "INTEGER"},
        "height": {"type": "INTEGER"},
        "orientation": {"type": "STRING", "enum": ["square", "portrait", "landscape"]},
        "confidence": {"type": "NUMBER", "minimum": 0, "maximum": 1},
        "message": {"type": "STRING", "nullable": True},
    },
    "required": ["x", "y", "width", "height", "orientation", "confidence"],
}

PROMPT = """Identify the single rectangular area where user artwork should be inserted in this product mockup.
Return pixel coordinates relative to the provided image. Choose only a front-facing axis-aligned rectangular
area suitable for simple PNG compositing. If the scene requires perspective placement, lower confidence and
explain it in message."""


class VertexArtworkAreaDetector:
    def __init__(self, project_id: str, location: str, model: str, client=None):
        if not project_id:
            raise DetectionConfigurationError("Vertex project ID is required")
        self.model = model or "gemini-2.5-flash"
        self.client = client or genai.Client(
            vertexai=True,
            project=project_id,
            location=location or "global",
            http_options=types.HttpOptions(api_version="v1"),
        )

    def detect(self, image_path: Path) -> DetectionProposal:
        with Image.open(image_path) as image:
            mime_type = Image.MIME.get(image.format, "image/png")
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=[
                    PROMPT,
                    types.Part.from_bytes(data=image_path.read_bytes(), mime_type=mime_type),
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=DETECTION_SCHEMA,
                    temperature=0,
                ),
            )
            payload = json.loads(response.text)
        except Exception as error:
            raise DetectionError(f"Vertex detection failed: {error}") from error
        proposal = DetectionProposal(
            x=int(payload["x"]), y=int(payload["y"]),
            width=int(payload["width"]), height=int(payload["height"]),
            orientation=payload["orientation"], confidence=float(payload["confidence"]),
            provider="vertex", model=self.model, message=payload.get("message"),
        )
        with Image.open(image_path) as image:
            return validate_proposal(proposal, canvas_width=image.width, canvas_height=image.height)
```

During implementation, use narrow exception mapping for Google API
authentication/unavailable/permission errors so the JSON admin message remains
useful without dumping credentials.

- [ ] **Step 4: Implement detection settings and real connection test endpoints**

Add authenticated `GET /api/admin/detection/settings`, and authenticated,
CSRF-protected `PUT /api/admin/detection/settings` and
`POST /api/admin/detection/test` routes.

For a test request:

- Save no credentials from the browser.
- Construct the detector from submitted non-secret settings plus server
  credentials.
- Generate a temporary high-contrast rectangular PNG under a temporary
  directory.
- Invoke `detect()` so the test covers multimodal access and structured JSON,
  not merely client construction.
- Return provider/model/proposal on success or clear error JSON on failure.

- [ ] **Step 5: Build working provider drawer in the admin UI**

Replace prototype controls with real fetch actions:

```javascript
async function loadDetectionSettings() {
  const payload = await api("/api/admin/detection/settings");
  state.detection = payload.settings;
  renderDetectionSettings();
}

async function saveDetectionSettings(settings) {
  const payload = await api("/api/admin/detection/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  state.detection = payload.settings;
  showStatus("Detection settings saved.", "success");
}

async function testDetectionConnection(settings) {
  showStatus("Testing provider connection...", "loading");
  try {
    const payload = await api("/api/admin/detection/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    });
    showStatus(`Connected: ${payload.provider} / ${payload.model}`, "success");
  } catch (error) {
    showStatus(error.message, "error");
  }
}
```

The UI explicitly states that credentials are server-side and instructs the
administrator to configure ADC or `GOOGLE_APPLICATION_CREDENTIALS` in `.env`.

- [ ] **Step 6: Run tests and manually test failure state**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_detection_services.py tests\test_admin_api.py -q
```

Expected: PASS.

Manual local failure check before credentials are configured:

```powershell
python app.py
```

Log into `/admin`, choose Vertex with a missing/invalid project, press `Test
connection`, and verify an error is displayed in the UI rather than silent
behavior.

- [ ] **Step 7: Commit if Git exists**

```powershell
git add services/vertex_detection_service.py services/detection_service.py routes/admin_routes.py templates/admin/index.html static/admin/admin.js tests/test_detection_services.py tests/test_admin_api.py
git commit -m "feat: connect template detection to vertex ai"
```

## Task 7: Configured Local Vision Provider

**Files:**
- Create: `services/local_detection_service.py`
- Modify: `services/detection_service.py`
- Modify: `tests/test_detection_services.py`

- [ ] **Step 1: Write failing local provider tests**

Add:

```python
from services.local_detection_service import LocalArtworkAreaDetector


class FakeHTTPResponse:
    def raise_for_status(self): pass
    def json(self):
        return {"choices": [{"message": {"content": '{"x": 5, "y": 6, "width": 50, "height": 60, "orientation": "portrait", "confidence": 0.8}'}}]}


def test_local_detector_posts_image_and_parses_response(tmp_path, monkeypatch):
    path = tmp_path / "image.png"
    Image.new("RGB", (100, 100), "white").save(path)
    monkeypatch.setattr("services.local_detection_service.requests.post", lambda *args, **kwargs: FakeHTTPResponse())
    detector = LocalArtworkAreaDetector("http://localhost:11434/v1", "qwen-vl", "")

    proposal = detector.detect(path)

    assert proposal.provider == "local"
    assert proposal.width == 50
```

- [ ] **Step 2: Implement OpenAI-compatible local adapter**

Create `services/local_detection_service.py`. Validate that base URL and model
are non-empty, base64-encode the image, and POST:

```python
payload = {
    "model": self.model,
    "messages": [{
        "role": "user",
        "content": [
            {"type": "text", "text": PROMPT + " Return JSON only."},
            {"type": "image_url", "image_url": {"url": data_url}},
        ],
    }],
    "temperature": 0,
}
```

Use a 60-second timeout, optional bearer token only when configured, parse the
JSON content into `DetectionProposal`, and reuse `validate_proposal()`.

- [ ] **Step 3: Add the provider factory case and run tests**

In `build_detector()`:

```python
if settings["provider"] == "local":
    return LocalArtworkAreaDetector(
        settings["local_base_url"],
        settings["local_model"],
        config.get("LOCAL_VISION_API_KEY", ""),
    )
```

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_detection_services.py -q
```

Expected: PASS.

- [ ] **Step 4: Commit if Git exists**

```powershell
git add services/local_detection_service.py services/detection_service.py tests/test_detection_services.py
git commit -m "feat: add local vision detector adapter"
```

## Task 8: Public Categories, Template Filtering, And Automatic Render Selection

**Files:**
- Modify: `routes/mockup_routes.py`
- Modify: `services/catalog_service.py`
- Modify: `tests/test_public_selection.py`
- Modify: `tests/test_mockup_api.py`

- [ ] **Step 1: Write failing public selection tests**

Create `tests/test_public_selection.py`:

```python
from tests.helpers import build_app, image_bytes, write_template


def test_public_categories_and_template_filters_return_active_entries_only(tmp_path):
    app = build_app(tmp_path)
    catalog = app.extensions["catalog"]
    category = catalog.create_category("Wall Art", "wall-art")
    catalog.create_template(category["id"], "portrait-active", "Portrait", status="active", orientation="portrait",
                            artwork_width=8, artwork_height=10, background_filename="background.png")
    catalog.create_template(category["id"], "portrait-draft", "Draft", status="draft", orientation="portrait",
                            artwork_width=8, artwork_height=10, background_filename="background.png")
    client = app.test_client()

    categories = client.get("/api/mockups/categories")
    templates = client.get("/api/mockups/templates?product_type=wall-art&orientation=portrait")

    assert categories.get_json()[0]["slug"] == "wall-art"
    assert [item["template_id"] for item in templates.get_json()] == ["portrait-active"]


def test_render_without_template_selects_closest_active_template(tmp_path):
    app = build_app(tmp_path)
    templates = app.config["TEMPLATES_FOLDER"]
    write_template(Path(templates), "portrait_active")
    catalog = app.extensions["catalog"]
    category = catalog.create_category("Wall Art", "wall-art")
    catalog.create_template(category["id"], "portrait_active", "Portrait", status="active",
                            orientation="portrait", artwork_width=8, artwork_height=8,
                            background_filename="background.png", preview_filename="preview.png")
    response = app.test_client().post(
        "/api/mockups/render",
        data={"mode": "simple", "product_type": "wall-art",
              "artwork": (image_bytes((10, 14)), "portrait.png"), "output_format": "png"},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert response.get_json()["template_id"] == "portrait_active"
```

- [ ] **Step 2: Run tests and confirm endpoints/selection fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_public_selection.py -q
```

Expected: missing categories endpoint and render requiring `template_id`.

- [ ] **Step 3: Add category and filtered templates routes**

In `routes/mockup_routes.py`:

```python
@mockup_routes.get("/api/mockups/categories")
def get_categories():
    return jsonify(current_app.extensions["catalog"].list_categories(active_only=True))
```

Update `GET /api/mockups/templates` to use active SQLite rows when filters are
provided and to return:

```python
{
    "template_id": row["template_id"],
    "name": row["name"],
    "preview_url": f"/templates/{row['template_id']}/{row['preview_filename']}",
    "supported_modes": ["simple"],
    "product_type": row["category_slug"],
    "orientation": row["orientation"],
}
```

Retain the legacy filesystem list response when no catalog rows exist, so the
existing MVP is not broken during first initialization.

For unfiltered `GET /api/mockups/templates`, preserve the original four
response keys (`template_id`, `name`, `preview_url`, `supported_modes`) so
existing clients and tests remain compatible. Add `product_type` and
`orientation` in filtered discovery responses. `GET /api/mockups/categories`
returns only non-archived categories that contain at least one active
template.

- [ ] **Step 4: Implement automatic selection in render route**

When `template_id` is blank:

```python
product_type = request.form.get("product_type", "").strip()
if not product_type:
    return error_response("Template ID or product type is required", 400)
artwork_path = store_uploaded_artwork(artwork, Path(current_app.config["UPLOAD_FOLDER"]))
with Image.open(artwork_path) as source:
    orientation = orientation_for_area(source.width, source.height)
    artwork_ratio = source.width / source.height
selected = current_app.extensions["catalog"].select_active_template(product_type, orientation, artwork_ratio)
if selected is None:
    return error_response("No matching template found", 404)
template_id = selected["template_id"]
```

Avoid storing the upload twice by moving artwork storage before selection and
passing the same `artwork_path` to `render_simple_mockup()`.

- [ ] **Step 5: Run public and regression tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_public_selection.py tests\test_mockup_api.py -q
```

Expected: PASS; explicit `template_id` rendering remains supported.

- [ ] **Step 6: Commit if Git exists**

```powershell
git add routes/mockup_routes.py services/catalog_service.py tests/test_public_selection.py tests/test_mockup_api.py
git commit -m "feat: select public mockups by category and orientation"
```

## Task 9: Documentation, Full Verification, And Real Vertex Smoke Test

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Test: all tests

- [ ] **Step 1: Update README with real admin and Vertex behavior**

Document:

- Installation with the new dependencies.
- The separation between admin detection and public rendering.
- Creating `.env` with `ADMIN_PASSWORD`, `SECRET_KEY`,
  `DATABASE_PATH=data/mockup_catalog.sqlite3`, and Vertex fields.
- ADC authentication, for example:

```powershell
gcloud auth application-default login
```

- Starting the real Flask service:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python app.py
```

- Opening `http://localhost:5000/admin`.
- Importing multiple mockups, testing Vertex connection, correcting a
  proposal, and activating templates.
- Public render call with automatic selection:

```bash
curl.exe -X POST http://localhost:5000/api/mockups/render \
  -F "mode=simple" \
  -F "product_type=wall-art" \
  -F "artwork=@my-artwork.png" \
  -F "output_format=png"
```

- That `localhost:51217` was only an earlier visual design companion and is
  not the production admin app.

- [ ] **Step 2: Run the complete automated test suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 3: Perform browser verification on the real `/admin` interface**

Start Flask and verify in the browser:

1. Login fails visibly for a wrong password and succeeds for the configured password.
2. Category creation updates the sidebar.
3. Multiple image import updates the queue with real thumbnails.
4. `Test connection` reports a real configuration error when credentials/project are wrong.
5. Queue scrolling does not scroll the full page.
6. Rectangle editing saves exact native image coordinates.
7. Activation produces a public template usable by the render endpoint.

- [ ] **Step 4: Perform a credentialed Vertex smoke test**

Only after the user has configured their own project and ADC credentials:

1. Set `DETECTION_PROVIDER=vertex`, `VERTEX_PROJECT_ID`, `VERTEX_LOCATION`,
   and `VERTEX_MODEL=gemini-2.5-flash` in `.env`.
2. Use `/admin` to press `Test connection`; confirm a real structured proposal
   is returned.
3. Import at least one square, portrait, and landscape mockup.
4. Compare each proposed rectangle visually; adjust and activate only after
   confirmation.
5. Record failed or low-confidence samples for later model benchmarking.

- [ ] **Step 5: Commit if Git exists**

```powershell
git add README.md .env.example
git commit -m "docs: document admin detection and vertex setup"
```

## Plan Self-Review

### Spec Coverage

- SQLite catalog and existing-template compatibility: Task 2.
- Protected admin interface and dynamic categories: Task 3.
- Batch import, review, foreground/mask, and activation: Task 4.
- Provider contract and classic baseline: Task 5.
- Real Vertex integration and visible connection/errors: Task 6.
- Configurable local provider: Task 7.
- Product category/orientation matching in public API: Task 8.
- Setup documentation and end-to-end verification: Task 9.

### Clarifications Locked For Implementation

- `VERTEX_MODEL` defaults to `gemini-2.5-flash` because it is explicitly
  shown in Google's Vertex structured-output documentation; UI-provided model
  changes are not considered active until an actual connection test succeeds.
- Credentials are environment/server-side only; database settings contain
  non-secret provider selections.
- Draft assets live outside publicly served `templates_data/`.
- Public rendering invokes Pillow with approved manifests only, never Vertex.

### Official Vertex References

- Structured output and Python `google-genai` example:
  <https://docs.cloud.google.com/vertex-ai/generative-ai/docs/multimodal/control-generated-output>
- Google Gen AI Python SDK, including Vertex client configuration and local
  image parts: <https://googleapis.github.io/python-genai/>
