# The catalog, the manifests, and what templates are called

## Where a template's state lives

The catalog database — `data/mockup_catalog.sqlite3` — is what a template *is*:
its name, category, orientation, artwork area, fit mode, mask, detected frames
and effects. Every admin edit writes there and nowhere else.

`templates_data/<id>/manifest.json` is the snapshot a template was **published**
with. It is written once, by the publish step, and is not rewritten when the
admin renames a template, drags a frame or moves a slider. It used to be, which
put a tracked file change in the working tree behind every interaction.

Readers lay one over the other through a single helper,
`merge_template_record(manifest, record)` in `services/simple_mockup_service.py`:

| reader | what it merges |
|---|---|
| `list_templates` | the public listing, `/api/mockups/templates` |
| `select_template_for_artwork` | automatic pick by product type |
| `rank_templates` / `select_best_template` | automatic pick for the batch API |
| `get_template_detail` | `/api/mockups/templates/<id>` |
| `execute_batch_render` | frames and render settings per item |
| `render_simple_mockup` | the render route passes the record's fields in |

A field the admin can *clear* — the mask, the detected regions, the detection
provider — is taken from the catalog even when it is empty. The rest fall back
to the manifest wherever the catalog has nothing to say, which is what keeps a
bare templates folder (a test fixture, a fresh clone) rendering on its own.

Seeding works the other way: `CatalogService.initialize` builds a catalog from
the manifests on disk when a template is missing from it, and carries the
frames, effects and detection provider across. So the published manifests are a
real backup — a catalog can be rebuilt from them.

## The database is committed

`data/mockup_catalog.sqlite3` is tracked in git. It holds application data
only; credentials live in `.env` (`ADMIN_PASSWORD`, `SECRET_KEY`,
`LOCAL_DETECTION_API_KEY`), and the catalog's `settings` table carries provider
configuration such as `DETECTION_PROVIDER` and `VERTEX_PROJECT_ID`. If anything
private ever needs storing, put it in a second database — `data/secrets.sqlite3`
is already ignored.

The write-ahead log (`-wal`) and shared-memory (`-shm`) files are **not**
tracked: they are transient, and a change sitting in the log would be missing
from the committed database. `CatalogService` checkpoints the log back into the
database file after every write, so the tracked file and a copy of it are always
current.

## Template names

Names are display labels — nothing keys off them; templates are addressed by
`template_id`, and import de-duplicates on `source_filename`. They read as a
short code:

```
H5-1    five landscape frames, first of its kind
V1-4    one portrait frame, fourth of its kind
S2-1    two square frames
M3-1    three frames that do not share one orientation
V15-1   the fifteen-frame collage
```

The letter comes from the frames' own aspect ratio (`H` landscape, `V` portrait,
`S` square within ±8%, `M` mixed), the number is how many frames the template
has, and the index makes the name unique within its group.

`scripts/rename_templates.py` prints the mapping and writes it with `--apply`:

```bash
python scripts/rename_templates.py            # show what it would do
python scripts/rename_templates.py --apply    # write the names to the catalog
```

Re-running it renumbers from scratch in `template_id` order, so a template's
code is stable as long as its frame count and orientation are.

## Which category a template belongs to

A category's slug is also the `product_type` the public API filters on and the
automatic picker matches against, so a template belongs with the shape of the
frames it holds *and* with how many of them it holds:

| frames | shape | category |
|---|---|---|
| one | portrait | `vertival-wall-art-frame` |
| one | landscape | `horizontal-wall-art` |
| one | square | `square-wall-art` |
| several | all portrait | `vertival-wall-art-frame-sets` |
| several | all landscape | `horizontal-wall-art-frame-sets` |
| several | anything else | `varient-wall-art-frame-sets` |

"Anything else" is a mockup whose frames do not share one shape — a laptop
beside a phone, a gallery wall of mixed frames — and a set of square frames,
which has no category of its own.

`scripts/sort_templates.py` prints the moves and makes them with `--apply`. It
leaves every template in a **Main** category alone:

```bash
python -m scripts.sort_templates            # show what it would move
python -m scripts.sort_templates --apply    # move them
```

