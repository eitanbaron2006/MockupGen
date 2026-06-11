# Mockup Batch Render API

`POST /api/mockups/render/batch` — render one or many mockups in a single
request: single artworks, several independent artworks, and artwork **sets**
that appear together in one mockup (wall-art sets, multi-device scenes).

## Request

`multipart/form-data` with:

| Field | Type | Description |
|---|---|---|
| `spec` | JSON string | The request specification (below). |
| `<any name>` | file | Artwork files. Each item references files by their form field name. |

### Spec schema

```json
{
  "defaults": {
    "fit_mode": "auto",
    "realism": true,
    "output": { "format": "png", "quality": 90 }
  },
  "items": [
    {
      "id": "poster-1",
      "artworks": "front",
      "template_id": "template_201fdf75dd7e"
    },
    {
      "id": "wall-set",
      "artworks": ["left", "middle", "right"],
      "selection": {
        "product_type": "vertival-wall-art-frame",
        "orientation": "landscape",
        "keywords": ["desk", "laptop"],
        "mockup_kind": "wall art"
      },
      "fit_mode": "cover",
      "output": { "format": "webp", "quality": 85 }
    }
  ]
}
```

- **`items`** (required, 1–20): each item produces **one mockup**.
  - `artworks` — one file field name, or a list (max 12). A list means the
    images form a **set** rendered together into one multi-frame template.
    Each entry is either a plain field name, or an object pinning the artwork
    to a specific numbered frame:

    ```json
    "artworks": [
      { "file": "left",  "frame": 2 },
      { "file": "right", "frame": 1 },
      "middle"
    ]
    ```

    Frame numbers come from `GET /api/mockups/templates/<template_id>` (see
    below). Artworks without an explicit `frame` are auto-placed on the
    remaining frames with the **closest aspect ratio**; frames still left
    over repeat the artwork list. Assigning the same frame twice or a frame
    number the template does not have is an error.
  - `template_id` — manual template selection. Omit for automatic selection.
  - `selection` — hints for automatic selection (all optional):
    - `product_type` — hard filter on the template's product type/category.
    - `orientation` — `portrait` / `landscape` / `square` bonus.
    - `keywords` / `mockup_kind` — free text matched against template names
      (e.g. "device", "desk", "frame", "bedroom").
  - `fit_mode` — `auto` / `cover` / `contain` / `stretch`.
  - `realism` — apply the full effects pipeline (default true).
  - `output.format` — `png` (lossless), `webp`, `jpeg`; `output.quality` 1–100
    (webp/jpeg only).
- **`defaults`** — fallback values for `fit_mode`, `realism` and `output`
  applied to every item that does not override them.

The same uploaded file may be referenced by multiple items; it is stored once.

### Automatic template selection

When `template_id` is omitted the server ranks all published templates:

1. `product_type` (when given) is a hard filter.
2. Sets require templates with at least as many frame slots as artworks;
   an exact slot count is preferred.
3. Aspect-ratio distance between the artworks and the template's frame slots
   (1:1, 2:3, 3:4, 4:5, 16:9, …) is the dominant score.
4. Orientation match and keyword hits break ties.

Multi-frame (green screen) templates — wall sets, laptop/tablet/phone scenes —
expose one slot per detected frame; classic templates expose a single slot.

## Response

`200` when every item succeeded, `207` when some failed, `400` for a malformed
spec. Item failures never abort sibling items.

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
      "selection": { "mode": "auto", "criteria": { "set_size": 3 } }
    },
    { "id": "poster-9", "success": false, "error": "Template not found: bad_id" }
  ]
}
```

Successful set items also report `frame_assignment` — the file field name
rendered into each frame, in frame order (1, 2, 3, …).

## Template frames endpoint

`GET /api/mockups/templates/<template_id>` returns the template's numbered
artwork frames so a client can show users a frame picker (e.g. draw "1",
"2", "3" badges over the template preview):

```json
{
  "template_id": "green_set_002",
  "name": "Wall art set of 3",
  "product_type": "wall-art-set",
  "orientation": "landscape",
  "canvas_width": 40,
  "canvas_height": 16,
  "preview_url": "/templates/green_set_002/preview.png",
  "frames": [
    { "frame": 1, "x": 4,  "y": 4, "width": 8, "height": 8, "ratio": 1.0, "orientation": "square" },
    { "frame": 2, "x": 16, "y": 4, "width": 8, "height": 8, "ratio": 1.0, "orientation": "square" },
    { "frame": 3, "x": 28, "y": 4, "width": 8, "height": 8, "ratio": 1.0, "orientation": "square" }
  ]
}
```

Frames are numbered in canonical order — top-to-bottom, then left-to-right —
matching exactly where the renderer places each artwork.

## Single render endpoint

`POST /api/mockups/render` (existing) also accepts `output_format`
(`png`/`webp`/`jpeg`) and `quality` form fields now.
