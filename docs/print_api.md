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
  "quality": "bicubic"
}
```

- `set` — a saved print set id. It decides the ratios, the quality and whether
  the printing guide ships. Anything else in the spec is ignored except a
  `quality` you pass explicitly.
- `ratios` — a comma-separated list (or array) of ratio keys, used when no set
  is given.
- Neither one: the artwork's **own** ratio, the closest active one to its shape.
- `quality` — `bicubic` (default), `step`, `step-unsharp`, `basic`, `ai`
  (Real-ESRGAN) or `gigapixel` (Topaz). The two AI modes run external programs
  and are only available where those are installed; `GET /api/print/ratios`
  reports which ones this machine can deliver, and why not.

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

A **ratio** is a key (`2:3`), a name, a portrait pixel canvas (100–30000px per
side) and the frame sizes it prints at. Keys are unique and compared without
case. A ratio can be switched off rather than deleted.

A **print set** is `matching` (one file, the artwork's own ratio) or `chosen`
(the ratio keys you name), plus a quality and whether the printing guide ships.

## Note on mockups

Mockups are rendered from the **original artwork only**. Every print size is the
same image at a different pixel count and all of them are scaled down into the
same opening, so a mockup per size would be byte-identical work times N.
