# Prompt: Integrate a client app with the MockupGen Render API

> Copy everything below this line and give it to the AI building the client
> application. It is self-contained — no access to the server codebase needed.

---

You are integrating a client application with **MockupGen**, a local mockup
rendering server. Implement the integration exactly as specified here. Do not
invent endpoints or fields that are not documented below.

## 1. Server basics

- Base URL: `http://127.0.0.1:5000` (configurable — make it a client setting).
- All endpoints below are public JSON/multipart HTTP — **no authentication,
  no CSRF token, no cookies required**. (Endpoints under `/api/admin/...` are
  a separate authenticated admin UI — do not use them.)
- All responses are JSON. Errors look like: `{"success": false, "error": "<message>"}`.
- Rendered images are returned as **relative URLs** (e.g.
  `/outputs/mockup_20260611_120000_ab12.png`). Prefix them with the base URL
  to download/display. Download results promptly — the outputs folder is not
  guaranteed to persist forever.
- Health check: `GET /api/health` → `{"status": "ok", "service": "mockup-render-server"}`.

## 2. Discover templates (mockups)

### List templates
`GET /api/mockups/templates` — optional query `?product_type=<slug>`.

Returns an array:
```json
[
  {
    "template_id": "template_fa83e6c793fe",
    "name": "Living room frame",
    "preview_url": "/templates/template_fa83e6c793fe/preview.png",
    "supported_modes": ["simple"],
    "orientation": "landscape",
    "product_type": "horizontal-wall-art"
  }
]
```
Use `preview_url` (prefix base URL) to show template thumbnails so the user
can browse and pick mockups visually.

### List categories / product types
`GET /api/mockups/categories` — array of category objects (use their slugs as
`product_type` filters).

### Template details + numbered frames
`GET /api/mockups/templates/<template_id>`:
```json
{
  "template_id": "green_set_002",
  "name": "Wall art set of 3",
  "product_type": "wall-art-set",
  "orientation": "landscape",
  "canvas_width": 40,
  "canvas_height": 16,
  "fit_mode": "stretch",
  "preview_url": "/templates/green_set_002/preview.png",
  "frames": [
    {"frame": 1, "x": 4,  "y": 4, "width": 8, "height": 8, "ratio": 1.0, "orientation": "square"},
    {"frame": 2, "x": 16, "y": 4, "width": 8, "height": 8, "ratio": 1.0, "orientation": "square"},
    {"frame": 3, "x": 28, "y": 4, "width": 8, "height": 8, "ratio": 1.0, "orientation": "square"}
  ]
}
```
- `frames` lists every artwork slot. Single-frame templates have one entry;
  multi-frame templates (wall sets, laptop/tablet/phone scenes) have several.
- Frame coordinates are in **template canvas pixels** (`canvas_width` ×
  `canvas_height`). To build a frame-picker UI: render the preview image,
  scale frame rects by `displayedWidth / canvas_width`, and draw a numbered
  badge ("1", "2", "3") on each frame so the user can assign images to frames.
- Frame numbering is canonical (top-to-bottom, then left-to-right) and matches
  exactly where the renderer places each artwork.

## 3. Render mockups — batch endpoint (preferred)

`POST /api/mockups/render/batch` as `multipart/form-data`:
- Field `spec`: a JSON **string** (the request specification).
- Any number of **file fields** with names of your choice; spec items
  reference files by those field names.

### Spec structure
```json
{
  "defaults": {
    "fit_mode": "auto",
    "realism": true,
    "output": {"format": "png", "quality": 90}
  },
  "items": [
    {
      "id": "poster-1",
      "artworks": "front",
      "template_id": "template_fa83e6c793fe"
    },
    {
      "id": "wall-set",
      "artworks": [
        {"file": "left", "frame": 1},
        {"file": "middle", "frame": 2},
        "right"
      ],
      "selection": {
        "product_type": "wall-art-set",
        "orientation": "landscape",
        "keywords": ["bedroom"]
      },
      "fit_mode": "cover",
      "output": {"format": "webp", "quality": 85}
    }
  ]
}
```

Rules — follow them exactly:
- **One item = one output mockup.** 1–20 items per request.
- `artworks`: one file field name, or a list (max 12). A list means the images
  appear **together in one mockup** (a set) — the template must have at least
  that many frames. For separate products send separate items (the same file
  field may be referenced by multiple items; it is uploaded once).
- Each artworks entry is either `"fieldName"` or
  `{"file": "fieldName", "frame": <number>}` to pin it to a specific numbered
  frame (numbers from the template details endpoint). Entries without `frame`
  are placed automatically on the remaining frames with the closest aspect
  ratio. Duplicate frame numbers → request rejected (400); a frame number the
  template doesn't have → that item fails (the others still render).
- `template_id` (manual) **or** `selection` (automatic). With neither, the
  server auto-selects from all templates using the artworks' aspect ratios and
  count. `selection` hints:
  - `product_type` — hard filter (use category slugs),
  - `orientation` — `portrait` / `landscape` / `square`,
  - `keywords` / `mockup_kind` — free text matched against template names.
- `fit_mode`: `auto` (recommended default), `cover`, `contain`, `stretch`.
- `realism`: `true` renders the full effects pipeline (default), `false` is a
  fast flat render.
- `output.format`: `png` (lossless, default), `webp`, `jpeg`; `output.quality`
  1–100 applies to webp/jpeg.
- `defaults` apply to every item that doesn't override the same key.

### Response
- HTTP `200` — all items succeeded; `207` — at least one failed (check each
  item); `400` — malformed spec (nothing rendered).
```json
{
  "success": true,
  "items": [
    {
      "id": "wall-set",
      "success": true,
      "template_id": "green_set_002",
      "output_url": "/outputs/mockup_20260611_120000_ab12.webp",
      "width": 1254,
      "height": 1254,
      "artworks": ["left", "middle", "right"],
      "frame_assignment": ["left", "middle", "right"],
      "selection": {"mode": "auto", "criteria": {"set_size": 3}}
    },
    {"id": "poster-9", "success": false, "error": "Template not found: bad_id"}
  ]
}
```
- `frame_assignment[i]` is the file field rendered into frame `i+1` — show it
  to the user so they can verify/fix the placement and resend with explicit
  `frame` values if desired.
- Handle item-level failures individually (offer per-item retry); never treat
  a `207` as a total failure.

### JavaScript example
```js
async function renderBatch(baseUrl, spec, fileMap) {
  const form = new FormData();
  form.append("spec", JSON.stringify(spec));
  for (const [field, file] of Object.entries(fileMap)) form.append(field, file);
  const res = await fetch(`${baseUrl}/api/mockups/render/batch`, { method: "POST", body: form });
  const payload = await res.json();
  if (res.status === 400) throw new Error(payload.error);
  return payload; // inspect payload.items[i].success individually
}
```

## 4. Single render endpoint (simple cases)

`POST /api/mockups/render` (`multipart/form-data`) — one artwork, one mockup:

| Field | Required | Notes |
|---|---|---|
| `mode` | yes | `"simple"` |
| `artwork` | yes | the image file (png/jpg/jpeg/webp) |
| `template_id` | yes* | *or send `product_type` for auto-selection by ratio |
| `product_type` | no | used when `template_id` omitted |
| `fit_mode` | no | `auto`/`cover`/`contain`/`stretch` |
| `realism` | no | `"true"` (default) / `"false"` |
| `output_format` | no | `png` (default) / `webp` / `jpeg` |
| `quality` | no | 1–100 for webp/jpeg |

Response: `{"success": true, "mode": "simple", "template_id": "...", "output_url": "/outputs/...", "width": ..., "height": ...}`.

## 5. UX & performance guidance

- Artwork uploads: accept png/jpg/jpeg/webp only; other types are rejected.
- The **first** render of a given template after server start takes ~1s
  (frame detection); subsequent renders of the same template take ~0.2–0.4s.
  Show a per-item progress indicator; a batch of N items returns only when
  all items finished, so for large batches prefer chunking into a few requests
  if you need incremental UI updates.
- Recommended flow for wall sets / multi-device mockups:
  1. user picks images →
  2. fetch templates (optionally filtered by `product_type`), show previews →
  3. on template pick, fetch `/api/mockups/templates/<id>`, draw numbered
     frames over the preview, let the user drag images onto frames (or skip —
     auto ratio matching is a good default) →
  4. POST the batch with explicit `frame` values for user assignments →
  5. display `output_url` images, offer downloads, show `frame_assignment`.
- Timeouts: allow at least 120s per request for large batches with realism on.
- Always render user-visible errors from the `error` strings; they are
  human-readable and specific.
