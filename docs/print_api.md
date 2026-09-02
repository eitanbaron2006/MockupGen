# Print Export API

`/api/print/...` — the high-resolution files a buyer downloads, made from the
same artwork the mockups were rendered from. The service has its own page
(`/print`), its own database (`data/print_sizes.sqlite3`) and its own settings,
so it can be split into a standalone application without touching the studio.

The rule the whole service exists for: **the artwork is never cropped.** A ratio
the artwork does not fill gets white margins instead of a trimmed edge, because
a border cut off a print is a refund.

Reading is open — the shop-side application calls it the way it calls the render
API. Changing the catalog needs an admin session and the CSRF header.

## Export

`POST /api/print/export` — `multipart/form-data`

| Field | Type | Description |
|---|---|---|
| `artwork` | file | The original artwork, full size. Required. |
| `spec` | JSON string | What to produce (below). Optional. |

```json
{
  "set": 3,
  "ratios": "2:3, 4:5",
  "quality": "bicubic",
  "mode": "safe_fit"
}
```

- `set` — a saved print set id. It decides the ratios, the quality and whether
  the printing guide ships. Anything else in the spec is ignored except a
  `quality` you pass explicitly.
- `ratios` — a comma-separated list (or array) of ratio keys, used when no set
  is given.
- Neither one: the artwork's **own** ratio, the closest active one to its shape.
- `quality` — `bicubic` (default), `step`, `step-unsharp`, `basic`, `ai`
  (Real-ESRGAN) or `gigapixel` (Topaz). The two AI modes run external programs.
  Real-ESRGAN **ships with the studio** in `tools/realesrgan/`, so the AI
  quality works on a fresh checkout with nothing to install and nothing to
  configure. Failing that the usual install locations (`C:\realesrgan\`,
  Topaz's own folder) and PATH are checked, and a path typed into the
  settings wins over all of them -- an empty setting does not mean missing.
  `GET /api/print/ratios` reports which ones this machine can deliver, and
  why not.
- `mode` — the Etsy output mode (below). Taken from the set when one is given;
  passing it explicitly overrides the set for that one export without changing
  it. An unknown mode is refused with `400`.

At most 12 files per export.

### Response

`200` when every file was made, `207` when some ratio failed.

```json
{
  "success": true,
  "artwork_ratio": 0.6667,
  "quality": "bicubic",
  "files": [
    {
      "ratio": "2:3",
      "success": true,
      "file": "4fa08fb5e5ea_2x3_ratio_24x36_inch.jpg",
      "url": "/print-outputs/4fa08fb5e5ea_2x3_ratio_24x36_inch.jpg",
      "width": 7200,
      "height": 10800,
      "prints_at": "4x6, 8x12, 12x18, 16x24, 20x30, 24x36"
    }
  ],
  "guide": {
    "file": "4fa08fb5e5ea_printing_guide.txt",
    "url": "/print-outputs/4fa08fb5e5ea_printing_guide.txt"
  }
}
```

A failed entry carries `"success": false` and an `error` instead of a file.
Files are JPEG quality 95 with a 300 DPI header. A landscape artwork turns the
canvas on its side and the name gains `_landscape`. The leading hex is the batch
id — it keeps one export apart from another on disk and is stripped from the
archive, so it is not part of what the buyer sees.

## Etsy output modes

How the artwork meets a canvas whose shape it does not match. `GET
/api/print/ratios` returns the list under `modes`, with the wording the screen
shows and a `cuts` flag.

| Key | What it does |
|---|---|
| `safe_fit` | The whole artwork, centred, **white margins** where the shapes differ. The default and the recommendation. |
| `safe_fill` | The whole artwork again, over a **blurred extension of itself**, so the file fills the canvas with no plain margins. |
| `fill_crop` | Fills the canvas edge to edge by **cutting** the overflow. The only mode that loses part of the artwork — not for work with internal frames, borders or text. |

The blurred backdrop is always scaled plainly, whatever quality was asked for:
an 80px Gaussian blur erases any difference an AI upscaler could make, and
running one over a second full-size canvas would double the cost of the export
for a result nobody can see.

## Archive

`POST /api/print/archive`

```json
{ "files": ["4fa08fb5e5ea_2x3_ratio_24x36_inch.jpg"], "name": "print-files.zip" }
```

Returns the files as one `.zip`, named without the batch id. Either the `file`
or the `url` from an export is accepted; anything else carrying a path
separator is refused with `400`.

## Catalog

| Method | Path | Needs admin |
|---|---|---|
| `GET` | `/api/print/ratios` | no — returns the ratios **and** the qualities this machine can deliver |
| `POST` `PATCH` `DELETE` | `/api/print/ratios[/<id>]` | yes |
| `GET` | `/api/print/sets` | no |
| `POST` `PATCH` `DELETE` | `/api/print/sets[/<id>]` | yes |
| `GET` `PUT` | `/api/print/settings` | yes — where the two AI programs live |

A **ratio** is a key (`2:3`), a name, a pixel canvas (100–30000px per side) and
the frame sizes it prints at. Keys are unique and compared without case.

Ten ratios ship with the service — `2:3`, `3:4`, `4:5`, `11:14`, `ISO A`, `1:1`,
`5:7`, `US Letter`, `3:1` and `2:1` — and carry `"builtin": 1`. **A built-in
ratio cannot be deleted** (`400`): deleting one would silently stop every set
that names it, with no way back but retyping the numbers, so `active: false`
is the way out instead. A ratio the admin adds is `"builtin": 0` and is theirs
to delete. New built-ins added in a later version reach an existing database on
the next start, and never overwrite a row the admin has edited.

Most ratios are stored portrait; the two panoramics are stored on their side
because that is the shape they sell in. The export orients each canvas to the
artwork either way, so a landscape artwork never stands a 3:1 print upright.

A **print set** is `matching` (one file, the artwork's own ratio) or `chosen`
(the ratio keys you name), plus a quality, an `output_mode` and whether the
printing guide ships. The guide's wording follows the mode, so a buyer reading
it is told what was actually done to the artwork.

## Note on mockups

Mockups are rendered from the **original artwork only**. Every print size is the
same image at a different pixel count and all of them are scaled down into the
same opening, so a mockup per size would be byte-identical work times N.
