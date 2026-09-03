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

### Watching it happen

A six-ratio export at full size is minutes of work. Ask for the files **as they
are made** with `?stream=1` on the export, or an `Accept: application/x-ndjson`
header. The plain single-object answer stays the default, so a caller that has
always received one JSON object keeps receiving one.

The response is newline-delimited JSON — one object per line:

```
{"event":"start","batch":"4fa08…","ratios":["2:3","3:4"],"quality":"bicubic","mode":"safe_fit"}
{"event":"file","ratio":"2:3","success":true,"file":"…","url":"/print-outputs/…","width":7200,…}
{"event":"file","ratio":"3:4","success":true,…}
{"event":"done","success":true,"export_id":12,"files":[…],"guide":{…}}
```

Each file is written to disk before its line is sent, so its `url` works the
moment it arrives. A ratio that failed comes through as a `file` line with
`"success": false` and an `error`. The `done` line carries everything the plain
answer would have, and is the point at which the export is recorded and the
retention sweep runs.

**A note for anything that adds middleware:** asking a streamed response for its
content length converts the generator to a list, which collects the whole body
before a byte of it is sent. The telemetry hook does this for ordinary responses
and skips it for streamed ones — see `_record_telemetry` in `app.py`.

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

## History

Every successful export is recorded, so a finished file is never an anonymous
name in a folder. The record says which artwork it came from, under which set
and mode, and what it weighs — which is also what makes cleanup possible.

`POST /api/print/export` accepts an optional `reference` in the spec (whatever
the shop app calls the listing) and answers with `export_id`.

| Method | Path | Needs admin |
|---|---|---|
| `GET` | `/api/print/exports` | no — `?reference=`, `?limit=`, `?offset=` |
| `GET` | `/api/print/exports/<id>` | no |
| `DELETE` | `/api/print/exports/<id>` | yes — forgets the record **and deletes its files** |
| `POST` | `/api/print/exports/sweep` | yes — runs the retention sweep now |

```json
{
  "id": 12,
  "batch": "4fa08fb5e5ea",
  "artwork_name": "seaside.png",
  "artwork_width": 1200, "artwork_height": 1800,
  "set_id": 3, "set_name": "The pack",
  "output_mode": "safe_fit", "quality": "bicubic",
  "guide_file": "4fa08fb5e5ea_printing_guide.txt",
  "reference": "listing-7781",
  "created_at": "2026-09-03T12:04:11+00:00",
  "files": [
    {"ratio_key": "2:3", "file_name": "…_2x3_ratio_24x36_inch.jpg",
     "width": 7200, "height": 10800, "prints_at": "4x6, 8x12, …", "bytes": 8421553}
  ]
}
```

### Retention

`retention_days` (a print setting, default **30**) is how long an export is
kept. The sweep runs after each export and on demand, and removes:

- exports past that age — record and files together;
- files in the output folder that **no record claims** and are past the same
  age. Nothing can name such a file, so once it is old it is only taking up
  room. This is what clears whatever was written before the history existed.

Set `retention_days` to `0` to keep everything, for a shop that archives its
deliveries elsewhere.

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
