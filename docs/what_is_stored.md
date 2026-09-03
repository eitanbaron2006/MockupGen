# What the studio stores, and what it deliberately does not

The rule everything here follows: **if the server fell over right now, what would hurt?**
What would not hurt is not worth a write to disk — every write is more code, another
migration, and another thing that can break.

## The two databases

| File | Holds |
|---|---|
| `data/mockup_catalog.sqlite3` | `categories`, `templates`, `settings`, `listing_sets`, `size_guides`, `error_log`, `login_attempts` |
| `data/print_sizes.sqlite3` | `ratios`, `print_sets`, `settings`, `exports`, `export_files` |

They are separate on purpose: the print service keeps its own so it can be split
into a standalone application later. Both run in WAL mode.

**The image files are not in either.** A template row names its files
(`background_name`, `preview_name`, `mask_name`); the pixels live in
`templates_data/<id>/`. Storing them as BLOBs would bloat the database and slow
every read. The consequence is the one thing to remember: **a backup of the
database alone is worthless — it has to travel with the folder.**

## Kept

### Print exports — `exports`, `export_files`

Every successful export is recorded: the artwork, the set, the output mode, the
quality, the files and their sizes. A print file is the product — losing track of
one means being unable to re-deliver a purchase without rendering it again.

It is also what makes cleanup possible: a folder of anonymous files cannot be
swept safely, because nothing says which of them still matters. See
[print_api.md](print_api.md) for the endpoints and the `retention_days` setting.

### Studio layout — `settings["UI_PREFERENCES"]`

The sidebar width, which panels are collapsed, where the canvas rails were left,
the selection style, the collapsed categories.

The database is the copy that counts; the browser keeps one too, but only as a
cache. Preferences are handed down **with the page** (a
`<script type="application/json" id="uiPreferences">` block) rather than fetched
after it, because the studio reads them synchronously in a dozen places while it
builds itself — a round trip would mean painting the wrong layout and correcting
it a moment later.

Writes go to `localStorage` immediately and to `PUT /api/admin/preferences` after
a 700ms pause, so dragging a sidebar is one save rather than one per pixel. The
save is a **merge**, so one screen cannot wipe another's keys. A `null` value
forgets a key.

`GET /api/admin/preferences` and `PUT` both need the admin; `PUT` needs the CSRF
token.

### Errors — `error_log`

An error that happened overnight and vanished at the next restart is the one
thing here that cannot be reconstructed. They are stored as they happen, bounded
to the last 1000, and **loaded back at start-up** so Server Pulse opens on the
history it had rather than on an empty list that says, wrongly, that nothing has
ever gone wrong.

`POST /api/telemetry/clear-logs` clears the stored copy as well as the live one —
otherwise the next start-up would load back the very errors that were cleared.

### Login attempts — `login_attempts`

Ten tries from one address in five minutes, cleared the moment a login succeeds.
Kept in the database because counting in memory meant that **anything which
restarted the server — a deploy, a crash, the reloader picking up an edit —
handed the guesser a fresh ten tries**, which is the one thing a rate limit must
not do. The clock is wall time; a monotonic one means nothing to the next process.

## Not kept, on purpose

| | Why not |
|---|---|
| **Requests and render timings** | A live view for Server Pulse. Their worth expires in minutes, and a write per request buys a table that grows without limit and nothing else. They stay in memory (`deque`). |
| **Rendered mockups and uploads** | Working files: the shop app fetches one and is done with it. There is nothing to look back at, only room to reclaim — so they get a **keeping time** rather than a history (below). |
| **Caches** (`_MANIFEST_CACHE`, `_GREEN_DETECTION_CACHE`) | Derived from the files and the database, and they rebuild themselves. Storing them would create a staleness problem worse than the cost of rebuilding. |
| **Secrets** (`.env`) | `SECRET_KEY`, `ADMIN_PASSWORD`, `VERTEX_*`, `LOCAL_DETECTION_API_KEY`. They do not belong in a database that is in git — which is precisely why the database can be. |

### The keeping time for working files

`outputs/` and `uploads/` are swept of anything older than
**`OUTPUT_RETENTION_HOURS`** (a studio setting, default **48**). The sweep runs on
start-up — so a server that was off over a weekend does not come back holding a
week of files — and on `POST /api/telemetry/purge-temp`. Set it to `0` to keep
everything.

Dotfiles are left alone: a `.gitkeep` holds the folder open in the repository and
is not output.
