# Admin Template Import And Vertex Detection Design

Date: 2026-05-27
Status: Approved design pending written-spec review

## Purpose

Extend the existing Flask mockup-render server with a real internal admin workflow for:

- Managing product categories.
- Importing multiple background mockup images at once.
- Proposing artwork rectangles automatically through a configurable detection provider.
- Reviewing and correcting each proposal before it becomes a usable template.
- Selecting public templates automatically by product category and uploaded artwork orientation.

This feature does not replace the existing `simple` PNG-layer renderer. AI is
used during template preparation only; customer render requests use saved
template coordinates and remain deterministic Pillow compositing operations.

## Scope Boundaries

In scope:

- A password-protected `/admin` interface styled consistently with the
  `EtsyAutoLister` project.
- Dynamic product categories.
- Batch template import.
- Draft review, rectangle editing, preview generation, and activation.
- Configurable detection engines, with a real Vertex AI implementation.
- Public API filtering and automatic template selection by category and
  artwork orientation.
- Clear setup documentation and automated tests.

Out of scope:

- Etsy integration, listing creation, payments, customer accounts, or a user
  dashboard.
- AI image generation for final mockup rendering.
- Photoshop or PSD Smart Object automation.
- Perspective transforms in the initial simple renderer. A rotated or
  perspective artwork region is flagged for manual review and cannot be
  activated as a `simple` template until it has an axis-aligned rectangle.

## Existing System Compatibility

The current server already supports:

- `GET /api/health`
- `GET /api/mockups/templates`
- `POST /api/mockups/render`
- `simple` rendering from `manifest.json`, `background.png`, optional
  `foreground.png`, and optional mask.

Those endpoints remain supported. The existing template in
`templates_data/template_001` remains valid and is imported into the catalog
as an active template during database initialization.

The current `services/ai_mockup_service.py` represents AI-assisted final image
rendering and remains separate. Template frame detection is a different
service boundary and must not be routed through `mode=ai`.

## Architecture

### Application Layout

The Flask application gains:

- An admin blueprint for HTML pages and authenticated admin JSON endpoints.
- A SQLite catalog for categories, templates, detection settings, and draft
  state.
- A template import service that writes asset files and manifests.
- A detection provider interface with Vertex, local endpoint, and classic
  implementations.
- A selection service for public category/orientation matching.

The admin frontend is served by Flask with HTML, CSS, and lightweight
JavaScript. It follows the approved warm paper, ink, coral, serif, and mono
visual language taken from `EtsyAutoLister`; a separate React application is
not necessary for this internal MVP.

### Source Of Truth

SQLite is authoritative for:

- Categories.
- Draft versus active status.
- Template-to-category association.
- Approved orientation.
- Detection-provider settings that are not credentials.

`manifest.json` remains authoritative for rendering geometry and image-layer
asset names. Only active templates receive a valid published manifest and are
returned by the public API.

This avoids exposing incomplete draft folders while keeping the current
simple renderer compatible with existing manifests.

## Storage Model

The application uses a SQLite database below the project data directory, for
example `data/mockup_catalog.sqlite3`.

### Categories

Fields:

- `id`
- `slug`, unique and stable for public API use
- `name`
- `created_at`
- `updated_at`

### Templates

Fields:

- `id`, internal UUID
- `template_id`, generated public folder identifier such as `template_<uuid>`
- `name`
- `category_id`
- `status`: `draft`, `review`, `active`, `archived`, or `failed`
- `orientation`: `square`, `portrait`, or `landscape`
- `fit_mode`: `cover`, `contain`, or `stretch`
- `background_filename`
- `foreground_filename`, nullable
- `mask_filename`, nullable
- `preview_filename`, nullable until generated
- `canvas_width`, `canvas_height`
- `artwork_x`, `artwork_y`, `artwork_width`, `artwork_height`, nullable until
  detection or manual editing succeeds
- `detection_provider`, `detection_model`, `detection_confidence`, nullable
- `detection_message`, nullable explanatory text
- timestamps

### Detection Settings

One active settings record stores non-secret configuration:

- `provider`: `vertex`, `local`, or `classic`
- `vertex_project_id`
- `vertex_location`
- `vertex_model`
- `local_base_url`
- `local_model`

Credentials are never stored through the admin UI or database.

## Admin Security

The MVP uses a single administrative password configured through `.env`:

```env
ADMIN_PASSWORD=
SECRET_KEY=
```

Behavior:

- `/admin/login` accepts the password and creates a signed Flask session.
- All `/api/admin/*` routes require an authenticated session.
- Mutating admin requests require a CSRF token stored in the session and sent
  by the admin JavaScript client.
- Login responses do not reveal whether configuration is missing beyond a
  clear server setup error in development logs.
- The password and any cloud credentials are not returned by APIs or rendered
  into HTML.

## Admin Workflow

### Categories

The admin can create, rename, and archive product categories. A category slug
is used by the public API, for example `wall-art`.

### Batch Import

The admin selects a category and uploads multiple supported images.

For each uploaded mockup:

1. Validate extension and image contents.
2. Generate a unique `template_id`.
3. Create a draft template folder and save `background.png` or the normalized
   source background asset.
4. Generate `preview.png`.
5. Insert a `draft` database record.
6. Run detection through the selected provider.
7. Store the proposal and display it in the review queue.

No draft gets a public `manifest.json` until it is approved.

### Review And Activation

The admin can:

- Adjust the artwork rectangle through drag/resize controls or numeric fields.
- Change template name, category, orientation, and fit mode.
- Optionally upload a transparent foreground or mask.
- Re-run detection through the currently selected engine.
- Approve and activate the template.

Activation validates that all geometry is inside the canvas and writes the
final `manifest.json`. Missing foreground is valid and is stored as `null`.

## Detection Provider Design

Detection is implemented behind a provider interface independent from
rendering:

```python
class ArtworkAreaDetector(Protocol):
    def detect(self, image_path: Path) -> DetectionProposal:
        ...
```

`DetectionProposal` contains:

- Axis-aligned pixel rectangle: `x`, `y`, `width`, `height`
- Orientation: `square`, `portrait`, or `landscape`
- Confidence from `0.0` through `1.0`
- Provider and model identifiers
- Optional review note

Every proposal is validated before storage:

- Rectangle dimensions must be positive.
- Rectangle must fit within the source canvas.
- Very small or highly implausible areas are rejected for review.
- Non-rectangular or perspective observations return a review-required
  message rather than a silently incorrect active template.

### Vertex AI Provider

Vertex AI is the default recommended provider because detection happens only
during template import and re-detection, keeping cloud cost limited.

Configuration:

```env
DETECTION_PROVIDER=vertex
VERTEX_PROJECT_ID=
VERTEX_LOCATION=global
VERTEX_MODEL=gemini-2.5-flash
GOOGLE_APPLICATION_CREDENTIALS=
```

Requirements:

- Authentication uses Google Application Default Credentials or a server-side
  service account credentials file configured outside the repository.
- The initial default is `gemini-2.5-flash`, which is documented by Google for
  Vertex structured-output requests. The model name remains configurable and
  is validated through a real connection test; code does not claim an
  unavailable model is configured.
- The provider sends the mockup image plus a strict instruction to identify
  only the rectangular insertion area for the user artwork.
- The provider requests structured JSON output conforming to the
  `DetectionProposal` shape.
- Provider errors, authorization failures, unavailable models, invalid JSON,
  and invalid geometry are surfaced in the admin queue with a meaningful error
  message and HTTP status.

### Local Provider

The local option uses a configured OpenAI-compatible multimodal endpoint and
model identifier, intended for a locally hosted vision model such as a Qwen
VL model.

Configuration:

```env
LOCAL_VISION_BASE_URL=
LOCAL_VISION_MODEL=
LOCAL_VISION_API_KEY=
```

The application does not download or launch a local model automatically.
Without a configured reachable endpoint, the admin UI reports the provider as
unavailable instead of simulating detection.

### Classic Provider

The classic option is an inexpensive local heuristic for clearly marked,
front-facing rectangular frames. It uses image-processing signals such as
contours and edge contrast, and returns low confidence or review-required
status when there is no reliable rectangle.

It is a fallback and benchmark baseline, not a substitute for Vertex on
visually complicated scenes.

## Admin Endpoints

HTML routes:

- `GET /admin/login`
- `GET /admin`

Authentication:

- `POST /api/admin/login`
- `POST /api/admin/logout`

Categories:

- `GET /api/admin/categories`
- `POST /api/admin/categories`
- `PATCH /api/admin/categories/<category_id>`

Detection settings:

- `GET /api/admin/detection/settings`
- `PUT /api/admin/detection/settings`
- `POST /api/admin/detection/test`

Templates:

- `GET /api/admin/templates?category=<slug>&status=<status>`
- `POST /api/admin/templates/import` with multiple image files
- `POST /api/admin/templates/<template_id>/detect`
- `PATCH /api/admin/templates/<template_id>`
- `POST /api/admin/templates/<template_id>/activate`
- `POST /api/admin/templates/<template_id>/archive`

All admin JSON responses return `success: true` on success or
`{"success": false, "error": "..."}` with an appropriate HTTP status.

## Public API Changes

### Categories

Add:

- `GET /api/mockups/categories`

This returns active categories available to customers.

### Templates

Enhance:

- `GET /api/mockups/templates?product_type=<category_slug>&orientation=<orientation>`

Only active templates are returned.

### Render Selection

`POST /api/mockups/render` retains explicit `template_id` support.

When `template_id` is omitted, clients submit:

- `product_type=<category_slug>`
- `artwork=<uploaded image>`
- `output_format=png`

The server:

1. Validates and stores the artwork.
2. Determines the artwork orientation from its dimensions.
3. Filters active templates in the selected category by orientation.
4. Ranks compatible templates by closest artwork-area aspect ratio, with a
   stable deterministic tie-breaker.
5. Renders using the selected saved manifest through the existing simple
   renderer.

No Vertex or local AI call occurs during this customer render path.

## Error Handling

Admin errors are visible both in JSON and in the interface:

- Incorrect login: `401`.
- Missing CSRF/session: `401` or `403`.
- Missing category, invalid upload, or invalid rectangle: `400`.
- Provider not configured or connection unavailable: `503`.
- Vertex authentication/model/API failure: `502` or `503` with useful text.
- Failed detection output validation: `422`, leaving the template in review.
- Missing template: `404`.

The UI must never silently ignore an admin action. Every detection, import,
save, activation, and connection test shows loading, success, or failure
status.

## Configuration

The `.env.example` additions are:

```env
ADMIN_PASSWORD=
SECRET_KEY=
DATABASE_PATH=data/mockup_catalog.sqlite3
DETECTION_PROVIDER=vertex
VERTEX_PROJECT_ID=
VERTEX_LOCATION=global
VERTEX_MODEL=gemini-2.5-flash
GOOGLE_APPLICATION_CREDENTIALS=
LOCAL_VISION_BASE_URL=
LOCAL_VISION_MODEL=
LOCAL_VISION_API_KEY=
```

API keys and service-account JSON contents are never checked into the project.

## Testing And Verification

Automated tests include:

- Admin login, logout, session enforcement, and CSRF handling.
- Category create/update/archive behavior.
- Batch import creating draft assets and preview files.
- Optional foreground handling for newly activated templates.
- Detection provider contract tests using fake providers.
- Vertex provider response parsing and error mapping with mocked SDK calls.
- Provider connection-test success and failure paths.
- Template activation manifest output.
- Public category filtering.
- Automatic orientation/aspect-ratio selection.
- Preservation of explicit `template_id` behavior.
- Existing simple rendering tests remain green.

Manual verification includes:

- Configure real ADC credentials and a real Vertex project/model.
- Test connection from `/admin` and confirm errors are shown when deliberately
  misconfigured.
- Import representative square, portrait, and landscape mockups.
- Confirm, adjust, and activate proposals.
- Render customer artwork through automatic category selection and verify the
  selected template and output image.

## Delivery Order

1. Catalog database, admin authentication, and categories.
2. Admin UI shell based on the approved EtsyAutoLister visual system.
3. Batch draft import, preview generation, manual rectangle review, and
   activation.
4. Detection-provider interface and classic detector baseline.
5. Real Vertex AI provider and connection/test UI.
6. Configured local-provider adapter.
7. Public category and automatic template-selection API changes.
8. Documentation, integration tests, and manual Vertex verification.

## Accepted Decisions

- The production admin screen is a real Flask-served application, not the
  visual companion prototype.
- Vertex is used for template detection, not final mockup rendering.
- Detection remains reviewable and is never silently auto-published.
- Missing `foreground.png` remains valid.
- Model/provider selection is visible in the admin UI, while credentials
  remain server-side.
- Public render calls stay inexpensive and deterministic after template
  approval.
