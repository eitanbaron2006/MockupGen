"""What the print service knows: the ratios sold, and the sets that use them.

This keeps its own SQLite file rather than sharing the mockup catalog. The two
hold different things -- one is what a mockup *is*, the other is what a shop
sells a print at -- and keeping them apart leaves the door open to running the
print side as its own application later, which is why it was asked for.

A ratio is a shape the shop sells: its key ("2:3"), the pixel size of the file
it ships at (7200x10800), and the frame sizes that file prints at. A print set
says which of those an incoming artwork should produce -- only its own ratio,
or a list the admin chose -- the same shape as a listing set.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

MODE_MATCHING = "matching"
MODE_CHOSEN = "chosen"
SET_MODES = (MODE_MATCHING, MODE_CHOSEN)

# Which ratios a set produces (above) and how the artwork meets each canvas
# (below) are two different questions, so they are two different fields.
OUTPUT_MODES = ("safe_fit", "safe_fill", "fill_crop")
DEFAULT_OUTPUT_MODE = "safe_fit"


class PrintCatalogError(ValueError):
    """Something the print catalog will not store."""


def checked_output_mode(value: Any) -> str:
    """An output mode the exporter actually knows, or a refusal.

    Storing an unknown one would only surface as a failed export later, when
    the artwork is already uploaded and the admin has moved on.
    """
    mode = str(value or DEFAULT_OUTPUT_MODE).strip().lower()
    if mode not in OUTPUT_MODES:
        raise PrintCatalogError(f"Unknown output mode: {value}")
    return mode


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# The ratios a print shop actually sells, with the file size each ships at.
# Carried over from the seller's own resizer so an existing shop's numbers are
# the numbers here, and editable afterwards like anything else.
DEFAULT_RATIOS = (
    ("2:3", "2:3 Ratio", 7200, 10800, "4x6, 8x12, 12x18, 16x24, 20x30, 24x36"),
    ("3:4", "3:4 Ratio", 5400, 7200, "6x8, 9x12, 12x16, 15x20, 18x24"),
    ("4:5", "4:5 Ratio", 7200, 9000, "4x5, 8x10, 12x15, 16x20, 20x25, 24x30"),
    ("11:14", "11:14 Ratio", 6600, 8400, "11x14, 22x28"),
    ("ISO A", "A-Series Ratio", 7016, 9933, "A5, A4, A3, A2, A1"),
    ("1:1", "1:1 Square", 7200, 7200, "5x5, 8x8, 10x10, 12x12, 16x16, 20x20, 24x24"),
    ("5:7", "5:7 Ratio", 5000, 7000, "5x7, 50x70cm"),
    ("US Letter", "US Letter", 5100, 6600, "8.5x11"),
    # The two panoramics are stored on their side because that is the shape they
    # are sold in; target_size turns any ratio to match the artwork, so storing
    # one landscape costs nothing.
    ("3:1", "3:1 Panoramic", 10800, 3600, "36x12, 30x10"),
    ("2:1", "2:1 Panoramic", 10800, 5400, "36x18, 24x12"),
)


class PrintCatalogService:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _checkpoint(self) -> None:
        try:
            with sqlite3.connect(self.database_path, timeout=15.0) as connection:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS ratios (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    sizes TEXT NOT NULL DEFAULT '',
                    active INTEGER NOT NULL DEFAULT 1,
                    builtin INTEGER NOT NULL DEFAULT 0,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS print_sets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    mode TEXT NOT NULL DEFAULT 'matching',
                    ratio_keys TEXT NOT NULL DEFAULT '[]',
                    quality TEXT NOT NULL DEFAULT 'bicubic',
                    output_mode TEXT NOT NULL DEFAULT 'safe_fit',
                    include_guide INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS exports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch TEXT NOT NULL UNIQUE,
                    artwork_name TEXT NOT NULL DEFAULT '',
                    artwork_width INTEGER NOT NULL DEFAULT 0,
                    artwork_height INTEGER NOT NULL DEFAULT 0,
                    set_id INTEGER,
                    set_name TEXT NOT NULL DEFAULT '',
                    output_mode TEXT NOT NULL DEFAULT 'safe_fit',
                    quality TEXT NOT NULL DEFAULT 'bicubic',
                    guide_file TEXT NOT NULL DEFAULT '',
                    reference TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS export_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    export_id INTEGER NOT NULL REFERENCES exports(id) ON DELETE CASCADE,
                    ratio_key TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    width INTEGER NOT NULL DEFAULT 0,
                    height INTEGER NOT NULL DEFAULT 0,
                    prints_at TEXT NOT NULL DEFAULT '',
                    bytes INTEGER NOT NULL DEFAULT 0,
                    ms INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS exports_made_at ON exports(created_at DESC);
                CREATE INDEX IF NOT EXISTS export_files_owner ON export_files(export_id);
                CREATE INDEX IF NOT EXISTS exports_reference ON exports(reference);
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(ratios)")}
            if "builtin" not in columns:
                connection.execute("ALTER TABLE ratios ADD COLUMN builtin INTEGER NOT NULL DEFAULT 0")
            file_columns = {row["name"] for row in connection.execute("PRAGMA table_info(export_files)")}
            if file_columns and "ms" not in file_columns:
                connection.execute("ALTER TABLE export_files ADD COLUMN ms INTEGER NOT NULL DEFAULT 0")
            set_columns = {row["name"] for row in connection.execute("PRAGMA table_info(print_sets)")}
            if "output_mode" not in set_columns:
                connection.execute(
                    "ALTER TABLE print_sets ADD COLUMN output_mode TEXT NOT NULL DEFAULT 'safe_fit'"
                )

            # A shop that has one of these already has all of them; an empty
            # screen would only invite typing the same numbers back in. This
            # runs every start rather than only on an empty table, so a ratio
            # added to the list later reaches a database that already exists --
            # and one the admin switched off or edited is left exactly as it is.
            known = {
                str(row["key"]).lower(): row["id"]
                for row in connection.execute("SELECT id, key FROM ratios")
            }
            order = connection.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM ratios").fetchone()[0]
            for key, name, width, height, sizes in DEFAULT_RATIOS:
                existing = known.get(key.lower())
                if existing is not None:
                    connection.execute("UPDATE ratios SET builtin = 1 WHERE id = ?", (existing,))
                    continue
                connection.execute(
                    """
                    INSERT INTO ratios(key, name, width, height, sizes, active, builtin, sort_order, created_at)
                    VALUES(?, ?, ?, ?, ?, 1, 1, ?, ?)
                    """,
                    (key, name, width, height, sizes, order, utc_now()),
                )
                order += 1
        self._checkpoint()

    # ------------------------------------------------------------- ratios

    def list_ratios(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        where = "WHERE active = 1" if active_only else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM ratios {where} ORDER BY sort_order, key"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_ratio(self, ratio_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM ratios WHERE id = ?", (ratio_id,)).fetchone()
        return dict(row) if row else None

    def get_ratio_by_key(self, key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM ratios WHERE lower(key) = lower(?)", (str(key).strip(),)
            ).fetchone()
        return dict(row) if row else None

    def create_ratio(self, record: dict[str, Any]) -> dict[str, Any]:
        key = " ".join(str(record.get("key", "")).split())
        if not key:
            raise PrintCatalogError("A ratio needs a key, such as 2:3")
        width, height = self._checked_size(record)
        with self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM ratios WHERE lower(key) = lower(?)", (key,)
            ).fetchone():
                raise PrintCatalogError(f'A ratio called "{key}" already exists')
            order = connection.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM ratios").fetchone()[0]
            cursor = connection.execute(
                """
                INSERT INTO ratios(key, name, width, height, sizes, active, builtin, sort_order, created_at)
                VALUES(?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    key,
                    " ".join(str(record.get("name") or key).split()),
                    width,
                    height,
                    str(record.get("sizes") or "").strip(),
                    1 if record.get("active", True) else 0,
                    order,
                    utc_now(),
                ),
            )
            ratio_id = cursor.lastrowid
        self._checkpoint()
        return self.get_ratio(ratio_id)

    def update_ratio(self, ratio_id: int, changes: dict[str, Any]) -> dict[str, Any]:
        current = self.get_ratio(ratio_id)
        if not current:
            raise PrintCatalogError("Ratio not found")
        allowed = {"key", "name", "width", "height", "sizes", "active", "sort_order"}
        assignments: list[str] = []
        values: list[Any] = []
        if "width" in changes or "height" in changes:
            width, height = self._checked_size({**current, **changes})
            changes = {**changes, "width": width, "height": height}
        for field, value in changes.items():
            if field not in allowed:
                continue
            if field in {"key", "name"}:
                value = " ".join(str(value).split())
                if not value:
                    raise PrintCatalogError(f"A ratio needs a {field}")
            if field == "active":
                value = 1 if value else 0
            assignments.append(f"{field} = ?")
            values.append(value)
        if not assignments:
            return current
        with self._connect() as connection:
            if "key" in changes:
                twin = connection.execute(
                    "SELECT 1 FROM ratios WHERE lower(key) = lower(?) AND id != ?",
                    (" ".join(str(changes["key"]).split()), ratio_id),
                ).fetchone()
                if twin:
                    raise PrintCatalogError("A ratio with that key already exists")
            values.append(ratio_id)
            connection.execute(f"UPDATE ratios SET {', '.join(assignments)} WHERE id = ?", values)
        self._checkpoint()
        return self.get_ratio(ratio_id)

    def delete_ratio(self, ratio_id: int) -> None:
        """Only a ratio the admin added themselves.

        The seeded ones are what the shop sells; deleting one would silently
        stop every set that names it, and there is no way back short of
        retyping the numbers. Switching it off does the same job and is
        reversible, so that is the only way out for a built-in.
        """
        current = self.get_ratio(ratio_id)
        if not current:
            raise PrintCatalogError("Ratio not found")
        if current.get("builtin"):
            raise PrintCatalogError(
                f'"{current["key"]}" is a built-in ratio and cannot be deleted -- switch it off instead'
            )
        with self._connect() as connection:
            connection.execute("DELETE FROM ratios WHERE id = ?", (ratio_id,))
        self._checkpoint()

    @staticmethod
    def _checked_size(record: dict[str, Any]) -> tuple[int, int]:
        try:
            width = int(record.get("width", 0))
            height = int(record.get("height", 0))
        except (TypeError, ValueError) as error:
            raise PrintCatalogError("A ratio's pixel size must be two numbers") from error
        if width < 100 or height < 100:
            raise PrintCatalogError("A print file is at least 100px on a side")
        if width > 30000 or height > 30000:
            raise PrintCatalogError("A print file is at most 30000px on a side")
        return width, height

    # ---------------------------------------------------------- print sets

    def list_sets(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM print_sets ORDER BY name").fetchall()
        return [self._row_to_set(row) for row in rows]

    def get_set(self, set_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM print_sets WHERE id = ?", (set_id,)).fetchone()
        return self._row_to_set(row) if row else None

    def create_set(self, record: dict[str, Any]) -> dict[str, Any]:
        name = " ".join(str(record.get("name", "")).split())
        if not name:
            raise PrintCatalogError("A print set needs a name")
        mode, ratio_keys = self._checked_mode(record)
        stamp = utc_now()
        with self._connect() as connection:
            if connection.execute(
                "SELECT 1 FROM print_sets WHERE lower(name) = lower(?)", (name,)
            ).fetchone():
                raise PrintCatalogError(f'A print set called "{name}" already exists')
            cursor = connection.execute(
                """
                INSERT INTO print_sets(name, mode, ratio_keys, quality, output_mode, include_guide, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    mode,
                    json.dumps(ratio_keys),
                    str(record.get("quality") or "bicubic"),
                    checked_output_mode(record.get("output_mode")),
                    1 if record.get("include_guide", True) else 0,
                    stamp,
                    stamp,
                ),
            )
            set_id = cursor.lastrowid
        self._checkpoint()
        return self.get_set(set_id)

    def update_set(self, set_id: int, changes: dict[str, Any]) -> dict[str, Any]:
        current = self.get_set(set_id)
        if not current:
            raise PrintCatalogError("Print set not found")
        assignments: list[str] = []
        values: list[Any] = []
        if "mode" in changes or "ratio_keys" in changes:
            mode, ratio_keys = self._checked_mode({**current, **changes})
            assignments += ["mode = ?", "ratio_keys = ?"]
            values += [mode, json.dumps(ratio_keys)]
        if "name" in changes:
            name = " ".join(str(changes["name"]).split())
            if not name:
                raise PrintCatalogError("A print set needs a name")
            with self._connect() as connection:
                twin = connection.execute(
                    "SELECT 1 FROM print_sets WHERE lower(name) = lower(?) AND id != ?", (name, set_id)
                ).fetchone()
            if twin:
                raise PrintCatalogError("A print set with that name already exists")
            assignments.append("name = ?")
            values.append(name)
        if "quality" in changes:
            assignments.append("quality = ?")
            values.append(str(changes["quality"] or "bicubic"))
        if "output_mode" in changes:
            assignments.append("output_mode = ?")
            values.append(checked_output_mode(changes["output_mode"]))
        if "include_guide" in changes:
            assignments.append("include_guide = ?")
            values.append(1 if changes["include_guide"] else 0)
        if not assignments:
            return current
        assignments.append("updated_at = ?")
        values += [utc_now(), set_id]
        with self._connect() as connection:
            connection.execute(f"UPDATE print_sets SET {', '.join(assignments)} WHERE id = ?", values)
        self._checkpoint()
        return self.get_set(set_id)

    def delete_set(self, set_id: int) -> None:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM print_sets WHERE id = ?", (set_id,))
            if not cursor.rowcount:
                raise PrintCatalogError("Print set not found")
        self._checkpoint()

    def _checked_mode(self, record: dict[str, Any]) -> tuple[str, list[str]]:
        mode = str(record.get("mode") or MODE_MATCHING).strip().lower()
        if mode not in SET_MODES:
            raise PrintCatalogError(f"mode must be one of: {', '.join(SET_MODES)}")
        raw = record.get("ratio_keys") or []
        if isinstance(raw, str):
            raw = [part.strip() for part in raw.split(",")]
        keys = [str(key).strip() for key in raw if str(key).strip()]
        if mode == MODE_CHOSEN and not keys:
            raise PrintCatalogError("Choose at least one ratio, or use the matching mode")
        known = {ratio["key"].lower() for ratio in self.list_ratios()}
        unknown = [key for key in keys if key.lower() not in known]
        if unknown:
            raise PrintCatalogError(f"Unknown ratio(s): {', '.join(unknown)}")
        return mode, keys

    @staticmethod
    def _row_to_set(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        try:
            record["ratio_keys"] = json.loads(record.get("ratio_keys") or "[]")
        except json.JSONDecodeError:
            record["ratio_keys"] = []
        record["include_guide"] = bool(record.get("include_guide"))
        record["output_mode"] = record.get("output_mode") or DEFAULT_OUTPUT_MODE
        return record

    # ------------------------------------------------------------- exports

    def record_export(self, record: dict[str, Any], files: list[dict[str, Any]]) -> dict[str, Any]:
        """What one export produced, so the files on disk stop being anonymous.

        Without this a finished print file is only a name in a folder: nothing
        says which artwork it came from, under which set, or whether it is
        still wanted -- which is also why nothing could clean up after itself.
        """
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO exports(
                    batch, artwork_name, artwork_width, artwork_height, set_id, set_name,
                    output_mode, quality, guide_file, reference, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(record.get("batch") or ""),
                    str(record.get("artwork_name") or ""),
                    int(record.get("artwork_width") or 0),
                    int(record.get("artwork_height") or 0),
                    record.get("set_id"),
                    str(record.get("set_name") or ""),
                    str(record.get("output_mode") or DEFAULT_OUTPUT_MODE),
                    str(record.get("quality") or "bicubic"),
                    str(record.get("guide_file") or ""),
                    str(record.get("reference") or ""),
                    utc_now(),
                ),
            )
            export_id = cursor.lastrowid
            for entry in files:
                connection.execute(
                    """
                    INSERT INTO export_files(
                        export_id, ratio_key, file_name, width, height, prints_at, bytes, ms
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        export_id,
                        str(entry.get("ratio") or ""),
                        str(entry.get("file") or ""),
                        int(entry.get("width") or 0),
                        int(entry.get("height") or 0),
                        str(entry.get("prints_at") or ""),
                        int(entry.get("bytes") or 0),
                        int(entry.get("ms") or 0),
                    ),
                )
        self._checkpoint()
        return self.get_export(export_id)

    def list_exports(self, *, limit: int = 50, offset: int = 0, reference: str = "") -> list[dict[str, Any]]:
        where, values = ("WHERE reference = ?", [str(reference)]) if reference else ("", [])
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM exports {where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (*values, max(1, min(int(limit), 200)), max(0, int(offset))),
            ).fetchall()
            return [self._with_files(connection, dict(row)) for row in rows]

    def count_exports(self, *, reference: str = "") -> int:
        where, values = ("WHERE reference = ?", [str(reference)]) if reference else ("", [])
        with self._connect() as connection:
            return connection.execute(f"SELECT COUNT(*) FROM exports {where}", values).fetchone()[0]

    def get_export(self, export_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM exports WHERE id = ?", (export_id,)).fetchone()
            return self._with_files(connection, dict(row)) if row else None

    @staticmethod
    def _with_files(connection: sqlite3.Connection, export: dict[str, Any]) -> dict[str, Any]:
        export["files"] = [
            dict(entry)
            for entry in connection.execute(
                "SELECT ratio_key, file_name, width, height, prints_at, bytes, ms "
                "FROM export_files WHERE export_id = ? ORDER BY id",
                (export["id"],),
            )
        ]
        return export

    def forget_export(self, export_id: int) -> list[str]:
        """Drop the record, and answer with the file names it was holding."""
        export = self.get_export(export_id)
        if not export:
            raise PrintCatalogError("Export not found")
        names = [entry["file_name"] for entry in export["files"]]
        if export["guide_file"]:
            names.append(export["guide_file"])
        with self._connect() as connection:
            connection.execute("DELETE FROM export_files WHERE export_id = ?", (export_id,))
            connection.execute("DELETE FROM exports WHERE id = ?", (export_id,))
        self._checkpoint()
        return names

    def claimed_file_names(self) -> set[str]:
        """Every file name the history still answers for.

        Asked of the database rather than of a page of results: a cleanup that
        walked only the first page would see the older exports as unclaimed
        and delete files that are still on the books.
        """
        with self._connect() as connection:
            names = {row[0] for row in connection.execute("SELECT file_name FROM export_files")}
            names |= {
                row[0]
                for row in connection.execute("SELECT guide_file FROM exports WHERE guide_file != ''")
            }
        return names

    def exports_older_than(self, days: int) -> list[dict[str, Any]]:
        """Every export past its keeping date. Zero days means keep forever."""
        if days <= 0:
            return []
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id FROM exports WHERE created_at < ? ORDER BY created_at", (cutoff,)
            ).fetchall()
        return [self.get_export(row["id"]) for row in rows]

    # ------------------------------------------------------------ settings

    def get_settings(self) -> dict[str, str]:
        with self._connect() as connection:
            return {row["key"]: row["value"] for row in connection.execute("SELECT key, value FROM settings")}

    def set_settings(self, values: dict[str, str]) -> None:
        with self._connect() as connection:
            for key, value in values.items():
                connection.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (str(key), str(value)),
                )
        self._checkpoint()


def ratios_for(
    catalog: PrintCatalogService, print_set: dict[str, Any], artwork_ratio: float
) -> list[dict[str, Any]]:
    """The ratios one artwork should be exported at, under one set's rule.

    In matching mode the artwork's own shape decides, and it decides by
    closeness rather than by an exact match: a 1.51 photograph is a 3:2 print,
    and refusing it because it is not exactly 1.5 would help nobody.
    """
    active = [ratio for ratio in catalog.list_ratios(active_only=True)]
    if not active:
        return []
    if print_set.get("mode") == MODE_CHOSEN:
        wanted = [key.lower() for key in print_set.get("ratio_keys") or []]
        return [ratio for ratio in active if ratio["key"].lower() in wanted]

    def distance(ratio: dict[str, Any]) -> float:
        shape = ratio["width"] / ratio["height"] if ratio["height"] else 1.0
        # Compared in portrait, so a landscape artwork matches the same ratio
        # its portrait twin would.
        upright = artwork_ratio if artwork_ratio <= 1 else 1 / artwork_ratio
        upright_ratio = shape if shape <= 1 else 1 / shape
        return abs(upright - upright_ratio)

    return [min(active, key=distance)]
