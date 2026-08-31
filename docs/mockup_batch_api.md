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
  "set": 3,
  "selection": { "product_type": "wall-art", "orientation": "portrait" },
  "templates": { "hero": "room_042" },
  "format": "jpeg",
  "quality": 90,
  "realism": true,
  "artwork": "artwork"
}
```

### Listing sets

`spec.set` is the id of a set an admin saved in the studio, and it decides
which mockups the listing gets. A set is three things, and their order is not
one of them:

| part | what it is |
| :--- | :--- |
| the main image | one `mockup` item marked `hero`. **Only a MAIN template may be it**, because the MAIN categories hold the picture Etsy shows as the product's thumbnail in search |
| the mockups | up to 18 more `mockup` items — each a template pinned by id, or `count` of them drawn from a category so the set keeps working as the catalog grows |
| the size guide | one `size_guide` item, taken from the library of ready-made charts and matched to the artwork's ratio |

The MAIN rule is enforced when the set is saved, not left to whoever builds it:
a MAIN template outside the hero, or a hero that is not MAIN, is refused with
`400`. Each picture is named after the mockup it came from.

Sets are managed at `/api/admin/listing-sets` (`GET`, `POST`, `PATCH /<id>`,
`DELETE /<id>`); the `GET` also returns `product_types` — what the listing is
for, as the shop-side app names it (Printable Wall Art, PNG Artwork Pack,
Lightroom Presets, Digital Planners), which is not the same thing as the
categories the mockups are filed on.

### Without a set

Left with no `set`, the endpoint chooses by aspect-ratio fit: the best-fitting
MAIN template leads, and the next two non-MAIN templates follow, then the size
guide. `selection` takes the same hints as a batch item, and `templates.hero`
pins the main image.

### Size guides

The chart is the one picture a buyer measures a wall against, so it comes from
a real file rather than being drawn per render. The library lives at
`/api/admin/size-guides` (`GET`; `POST` multipart with `guide` and `ratio`;
`DELETE /<id>`; `GET /<id>/asset`), and `POST /api/admin/size-guides/generate`
with `{"ratio": "2:3"}` has Vertex draw one and keeps it.

A guide is tagged with the ratio it is drawn for, **the way round it is drawn**
— `2:3` and `3:2` are different charts — so the ratio is the only field there
is: `2:3`, `3:2`, `3:4`, `4:3`, `4:5`, `5:4`, `5:7`, `7:5`, `ISO A portrait`,
`ISO A landscape`, `1:1`. The artwork's own shape picks the match. Where the
library has nothing for that shape and `ENABLE_AI_MODE` is on with a Vertex
project configured, one is generated for that render and the item comes back
with `"source": "ai"` — image models are unreliable at rendering exact numbers,
so that is the fallback, never the default. With neither, that one item fails
and the rest of the listing is still delivered.

### Response

`200` when every requested image was produced, `207` when some were:

```json
{
  "success": true,
  "artwork_ratio": 0.6667,
  "items": [
    { "item": 0, "kind": "mockup", "label": "MAIN-V1-3", "success": true, "template_id": "room_042", "output_url": "/outputs/mockup_a.jpg", "width": 3000, "height": 2000, "hero": true },
    { "item": 1, "kind": "mockup", "label": "H1-5", "success": true, "template_id": "room_101", "output_url": "/outputs/mockup_b.jpg", "width": 3000, "height": 2000, "hero": false },
    { "item": 2, "kind": "size_guide", "label": "Size guide", "success": true, "output_url": "/outputs/mockup_c.jpg", "width": 2000, "height": 2000, "size_family": "2:3", "guide_id": 4, "source": "upload" }
  ]
}
```

`item` is the row of the set the picture came from — a row drawing several
mockups from a category answers with several items carrying the same number.
The main image always comes first. Pictures fail one at a time, like batch
items: a template that will not render costs the listing that one picture and
the rest still come back, each failed one carrying `"success": false` and an
`error`. Only a request that is wrong in itself — no artwork file, an unknown
set, an unsupported format — is refused whole with `400`. The `output_url`
values are ordinary outputs, so they can be fetched one by one or handed to
`POST /api/mockups/outputs/archive` to come back as a single ZIP.

### Drawing a chart

`POST /api/admin/size-guides/generate` takes `ratio` and one of:

- `preset` — one of the studio's styles (`room`, `outlined`, `gallery`, `iso`,
  `figure`). The style's own example image, if one has been attached at
  `POST /api/admin/size-guides/styles/<key>/example`, is sent with it.
- `prompt` — wording of the admin's own, which is remembered and used for every
  later chart. Nothing of the studio's is attached to it: it is their design.

A `reference` image may be attached to either (multipart), and every prompt —
including one written from scratch — carries a closing demand that each label
be lettered exactly as given, because image models misspell what they draw.

**Known limit, measured:** the model draws something that *looks* to scale
rather than something that *is*. Frames against a sofa or a figure come out
convincing but not proportionally exact, so a chart uploaded by hand always
wins: the library is searched first, and generation is only the fallback.
