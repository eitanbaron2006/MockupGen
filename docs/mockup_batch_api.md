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

`200` when every item succeeded, `207` when some failed, `400` when the request
as a whole cannot be acted on.

**Item failures never abort sibling items** — and that now includes items the
request got wrong, not only renders that failed. An item naming a file that was
not uploaded, or a frame number that is not a number, comes back as that item
with `"success": false` and its own message, while every other item still
renders. `400` is reserved for a request where nothing could be done at all: no
`items`, more than the limit, unreadable JSON, or every single item malformed.

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
(`png`/`webp`/`jpeg`, and `avif` where the server's Pillow can write it) and
`quality` form fields now.

`GET /api/mockups/formats` is not a route; if you need to know what a particular
server supports, ask for a format and read the error, which lists them.

## Downloading a batch as one file

`POST /api/mockups/outputs/archive` — optional, and additive: the batch
endpoint's JSON answer is unchanged, and fetching each `output_url` in turn
works exactly as before. This is for when the lot is wanted as a single file.

```json
{ "outputs": ["/outputs/mockup_a.png", "/outputs/mockup_b.png"], "name": "wall-art" }
```

Answers `application/zip` as an attachment. `outputs` takes the `output_url`
values straight from a render response (bare file names work too); at most 200
per archive. A name that is not a file in the outputs folder is refused with
`404` rather than quietly leaving a hole in the archive, and two renders with
the same name are both kept (`mockup_a.png`, `mockup_a-2.png`).

## Listing bundles

`POST /api/mockups/listing-bundle` — one artwork in, the images one shop
listing needs out. Additive: nothing about the batch endpoint changes, and a
client that never calls this keeps working exactly as before.

multipart/form-data:

- `artwork` — the artwork file (another field name can be used, see `spec.artwork`)
- `spec` — optional JSON:

```json
{
  "roles": ["hero", "closeup", "scale", "size_guide"],
  "selection": { "product_type": "wall-art", "orientation": "portrait", "keywords": ["living room"] },
  "templates": { "hero": "room_042" },
  "sizes": [{ "label": "A3", "width": 29.7, "height": 42 }],
  "format": "jpeg",
  "quality": 90,
  "realism": true,
  "artwork": "artwork"
}
```

The four roles, all optional — ask for the ones the listing needs:

| role | what it is |
| :--- | :--- |
| `hero` | the main shot: the best-matching template, chosen automatically unless `templates.hero` names one |
| `closeup` | the hero image cropped around the frame, so the buyer sees the print itself. Not a second render — it is a crop of the hero, so it costs nothing and always matches it |
| `scale` | the piece in a second room, using a different template from the hero so the listing does not show the same picture twice |
| `size_guide` | a print-size chart: nested outlines from the smallest offered size to the largest, labelled, with the artwork ghosted inside |

`selection` takes the same hints as a batch item. `templates` pins any role to a
specific template and skips selection for it. `sizes` replaces the standard
chart with the seller's own list; left out, the sizes are the retail family the
artwork's own ratio fits (2:3, 3:4, 4:5, 5:7, ISO A or 1:1), turned landscape
when the artwork is.

Response — `200` when every requested image was produced, `207` when some were:

```json
{
  "success": true,
  "artwork_ratio": 0.6667,
  "items": [
    { "role": "hero", "success": true, "template_id": "room_042", "output_url": "/outputs/mockup_a.png", "width": 3000, "height": 2000, "selection": "auto" },
    { "role": "closeup", "success": true, "template_id": "room_042", "output_url": "/outputs/mockup_b.png", "width": 980, "height": 1310, "crop": { "x": 610, "y": 320, "width": 980, "height": 1310 } },
    { "role": "scale", "success": true, "template_id": "room_101", "output_url": "/outputs/mockup_c.png", "width": 3000, "height": 2000, "selection": "auto" },
    { "role": "size_guide", "success": true, "output_url": "/outputs/mockup_d.png", "width": 2000, "height": 2000, "size_family": "2:3", "unit": "in", "sizes": [{ "label": "4x6", "width": 4, "height": 6 }] }
  ]
}
```

Roles fail one at a time, like batch items: a template that will not render
costs the listing that one picture and the rest still come back, each failed
role carrying `"success": false` and an `error`. Only a request that is wrong in
itself — an unknown role, no artwork file, an unsupported format — is refused
whole with `400`. The `output_url` values are ordinary outputs, so they can be
fetched one by one or handed to `POST /api/mockups/outputs/archive` to come
back as a single ZIP.
