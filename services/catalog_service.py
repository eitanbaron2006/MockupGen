import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.simple_mockup_service import InvalidTemplateError, TemplateNotFoundError, load_manifest

_UNCHANGED = object()


class CatalogError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(value: str) -> str:
    slug = re.sub(r"[^\w-]+", "-", value.strip().lower(), flags=re.UNICODE).strip("-")
    return slug or "category"


class CatalogService:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _checkpoint(self) -> None:
        """Fold the write-ahead log back into the database file.

        The catalog is what a template *is* -- its name, category, frames and
        effects -- and it is committed to the repository, so a change that
        lived only in the -wal file would be missing from that copy and from
        any backup taken by copying the database.
        """
        try:
            with sqlite3.connect(self.database_path, timeout=15.0) as connection:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass

    def initialize(self, templates_folder: Path) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    slug TEXT NOT NULL UNIQUE,
                    parent_id INTEGER REFERENCES categories(id),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS templates (
                    template_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    category_id INTEGER REFERENCES categories(id),
                    status TEXT NOT NULL CHECK(status IN ('draft', 'active')),
                    canvas_width INTEGER NOT NULL,
                    canvas_height INTEGER NOT NULL,
                    artwork_area TEXT,
                    fit_mode TEXT NOT NULL DEFAULT 'cover',
                    orientation TEXT NOT NULL,
                    background_name TEXT NOT NULL DEFAULT 'background.png',
                    preview_name TEXT NOT NULL DEFAULT 'preview.png',
                    foreground_name TEXT,
                    mask_name TEXT,
                    source_filename TEXT,
                    detection_provider TEXT,
                    detection_confidence REAL,
                    raw_artwork_area TEXT,
                    effects TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS listing_sets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    product_type TEXT,
                    orientation TEXT NOT NULL DEFAULT 'any',
                    status TEXT NOT NULL DEFAULT 'active',
                    items TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS size_guides (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    ratio TEXT NOT NULL,
                    orientation TEXT NOT NULL DEFAULT 'portrait',
                    file_name TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'upload',
                    created_at TEXT NOT NULL
                );
                """
            )
            try:
                # Categories group under one parent, so a name says what the
                # shelf holds and the parent says what it is for -- "Vertical"
                # under "Wall Art" rather than "Vertical Wall Art Frame".
                connection.execute(
                    "ALTER TABLE categories ADD COLUMN parent_id INTEGER REFERENCES categories(id)"
                )
            except sqlite3.OperationalError:
                pass
            try:
                connection.execute("ALTER TABLE templates ADD COLUMN raw_artwork_area TEXT")
            except sqlite3.OperationalError:
                pass
            try:
                connection.execute("ALTER TABLE templates ADD COLUMN effects TEXT")
            except sqlite3.OperationalError:
                pass
        self._seed_existing_templates(templates_folder)

    def _seed_existing_templates(self, templates_folder: Path) -> None:
        if not templates_folder.exists():
            return
        for folder in templates_folder.iterdir():
            if not folder.is_dir() or self.get_template(folder.name):
                continue
            try:
                _, manifest = load_manifest(templates_folder, folder.name)
            except (TemplateNotFoundError, InvalidTemplateError):
                continue
            product_type = str(manifest.get("product_type", "uncategorized"))
            category = self.get_or_create_category(product_type.replace("-", " ").title())
            area = manifest["artwork_area"]
            self.create_template(
                {
                    "template_id": folder.name,
                    "name": manifest["name"],
                    "category_id": category["id"],
                    "status": "active",
                    "canvas_width": manifest["canvas_width"],
                    "canvas_height": manifest["canvas_height"],
                    "artwork_area": area,
                    "fit_mode": manifest.get("fit_mode", "cover"),
                    "orientation": orientation_for_size(area["width"], area["height"]),
                    "background_name": manifest["background"],
                    "preview_name": manifest.get("preview", "preview.png"),
                    "foreground_name": manifest.get("foreground"),
                    "mask_name": manifest.get("mask"),
                    "source_filename": manifest["background"],
                    # The manifest is the snapshot a template was published
                    # with, and seeding is how a catalog is rebuilt from those
                    # snapshots -- so carry the detection across too. Leaving it
                    # behind used to hand a rebuilt catalog templates with no
                    # frames and no effects.
                    "raw_artwork_area": manifest.get("raw_artwork_area"),
                    "effects": manifest.get("effects"),
                    "detection_provider": manifest.get("detection_provider"),
                    "detection_confidence": manifest.get("detection_confidence"),
                }
            )

    def get_or_create_category(self, name: str) -> dict[str, Any]:
        existing = self.get_category_by_slug(slugify(name))
        return existing or self.create_category(name)

    def create_category(self, name: str, parent_id: int | None = None) -> dict[str, Any]:
        cleaned = " ".join(name.split())
        if not cleaned:
            raise CatalogError("Category name is required")
        base_slug = slugify(cleaned)
        slug = base_slug
        suffix = 2
        with self._connect() as connection:
            # The name column is unique, but SQLite compares text case
            # sensitively -- so "Wall Art" and "wall art" both fitted, the slug
            # collision was quietly resolved with a -2 suffix, and the sidebar
            # ended up with two categories the eye reads as one. Renaming
            # already refused that; creating has to refuse it too.
            twin = connection.execute(
                "SELECT name FROM categories WHERE lower(name) = lower(?)", (cleaned,)
            ).fetchone()
            if twin:
                raise CatalogError(f'A category named "{twin["name"]}" already exists')
            while connection.execute(
                "SELECT 1 FROM categories WHERE slug = ?", (slug,)
            ).fetchone():
                slug = f"{base_slug}-{suffix}"
                suffix += 1
            parent = self._checked_parent(connection, parent_id)
            try:
                cursor = connection.execute(
                    "INSERT INTO categories(name, slug, parent_id, created_at) VALUES(?, ?, ?, ?)",
                    (cleaned, slug, parent, utc_now()),
                )
            except sqlite3.IntegrityError as error:
                raise CatalogError("Category already exists") from error
            category_id = cursor.lastrowid
        self._checkpoint()
        return self.get_category(category_id)

    @staticmethod
    def _checked_parent(connection, parent_id: Any, moving: int | None = None) -> int | None:
        """The parent a category may sit under, or nothing.

        Grouping is one level deep on purpose: a shelf holds mockups and a
        parent holds shelves. Anything deeper is a folder tree nobody asked
        for, and it would leave the sidebar guessing how far to indent.
        """
        if parent_id in (None, "", 0):
            return None
        try:
            parent_id = int(parent_id)
        except (TypeError, ValueError) as error:
            raise CatalogError("Parent category must be a number") from error
        if moving is not None and parent_id == moving:
            raise CatalogError("A category cannot be its own parent")
        row = connection.execute(
            "SELECT id, parent_id FROM categories WHERE id = ?", (parent_id,)
        ).fetchone()
        if not row:
            raise CatalogError("Parent category not found")
        if row["parent_id"] is not None:
            raise CatalogError("Categories group one level deep")
        if moving is not None:
            has_children = connection.execute(
                "SELECT 1 FROM categories WHERE parent_id = ?", (moving,)
            ).fetchone()
            if has_children:
                raise CatalogError("A category with sub-categories cannot become one")
        return parent_id

    def get_category(self, category_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM categories WHERE id = ?", (category_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_category_by_slug(self, slug: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM categories WHERE slug = ?", (slug,)
            ).fetchone()
        return dict(row) if row else None

    def update_category(
        self, category_id: int, name: str, parent_id: Any = _UNCHANGED
    ) -> dict[str, Any]:
        current = self.get_category(category_id)
        if not current:
            raise CatalogError("Category not found")
        cleaned = " ".join(name.split())
        if not cleaned:
            raise CatalogError("Category name is required")
        slug = slugify(cleaned)
        with self._connect() as connection:
            conflict = connection.execute(
                """
                SELECT 1 FROM categories
                WHERE (lower(name) = lower(?) OR slug = ?) AND id != ?
                """,
                (cleaned, slug, category_id),
            ).fetchone()
            if conflict:
                raise CatalogError("Category already exists")
            if parent_id is _UNCHANGED:
                cursor = connection.execute(
                    "UPDATE categories SET name = ?, slug = ? WHERE id = ?",
                    (cleaned, slug, category_id),
                )
            else:
                parent = self._checked_parent(connection, parent_id, moving=category_id)
                cursor = connection.execute(
                    "UPDATE categories SET name = ?, slug = ?, parent_id = ? WHERE id = ?",
                    (cleaned, slug, parent, category_id),
                )
            if not cursor.rowcount:
                raise CatalogError("Category not found")
        self._checkpoint()
        return self.get_category(category_id)
    def delete_empty_category(self, category_id: int) -> None:
        with self._connect() as connection:
            template_count = connection.execute(
                "SELECT COUNT(*) FROM templates WHERE category_id = ?", (category_id,)
            ).fetchone()[0]
            if template_count:
                raise CatalogError("Only empty categories can be deleted")
            # A parent looks empty -- it never holds mockups itself -- so
            # without this the shelves under it would be orphaned by a click.
            children = connection.execute(
                "SELECT COUNT(*) FROM categories WHERE parent_id = ?", (category_id,)
            ).fetchone()[0]
            if children:
                raise CatalogError(
                    f"This category still holds {children} sub-categories. "
                    "Move or delete them first."
                )
            cursor = connection.execute("DELETE FROM categories WHERE id = ?", (category_id,))
            if not cursor.rowcount:
                raise CatalogError("Category not found")
        self._checkpoint()

    def list_categories(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        """Every shelf, each knowing its parent and what sits under it.

        The order is the order the sidebar draws: a parent, then its children,
        then the next parent -- so the list can be rendered straight through
        without the browser having to rebuild the tree.
        """
        condition = "WHERE t.status = 'active'" if active_only else ""
        query = f"""
            SELECT c.id, c.name, c.slug, c.parent_id, COUNT(t.template_id) AS template_count
            FROM categories c LEFT JOIN templates t ON t.category_id = c.id
            {condition}
            GROUP BY c.id ORDER BY c.name COLLATE NOCASE
        """
        with self._connect() as connection:
            rows = [dict(row) for row in connection.execute(query).fetchall()]

        by_parent: dict[Any, list[dict[str, Any]]] = {}
        for row in rows:
            by_parent.setdefault(row["parent_id"], []).append(row)
        ordered: list[dict[str, Any]] = []
        for row in by_parent.get(None, []):
            children = by_parent.get(row["id"], [])
            row["child_count"] = len(children)
            # A parent holds shelves, not mockups: what it is worth is the sum
            # of what is under it, which is the number the sidebar shows.
            row["template_count"] += sum(child["template_count"] for child in children)
            ordered.append(row)
            for child in children:
                child["child_count"] = 0
                ordered.append(child)
        return ordered

    def create_template(self, record: dict[str, Any]) -> dict[str, Any]:
        timestamp = utc_now()
        record = {
            **record,
            "name": self._named_for_category(record.get("name"), record.get("category_id")),
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO templates(
                    template_id, name, category_id, status, canvas_width,
                    canvas_height, artwork_area, fit_mode, orientation,
                    background_name, preview_name, foreground_name, mask_name,
                    source_filename, detection_provider, detection_confidence,
                    raw_artwork_area, effects, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["template_id"],
                    record["name"],
                    record.get("category_id"),
                    record.get("status", "draft"),
                    int(record["canvas_width"]),
                    int(record["canvas_height"]),
                    json.dumps(record.get("artwork_area")) if record.get("artwork_area") else None,
                    record.get("fit_mode", "cover"),
                    record["orientation"],
                    record.get("background_name", "background.png"),
                    record.get("preview_name", "preview.png"),
                    record.get("foreground_name"),
                    record.get("mask_name"),
                    record.get("source_filename"),
                    record.get("detection_provider"),
                    record.get("detection_confidence"),
                    json.dumps(record.get("raw_artwork_area")) if record.get("raw_artwork_area") else None,
                    json.dumps(record.get("effects")) if record.get("effects") else None,
                    timestamp,
                    timestamp,
                ),
            )
        self._checkpoint()
        return self.get_template(record["template_id"])
    def get_template(self, template_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT t.*, c.name AS category_name, c.slug AS product_type
                FROM templates t LEFT JOIN categories c ON c.id = t.category_id
                WHERE t.template_id = ?
                """,
                (template_id,),
            ).fetchone()
        return self._row_to_template(row)

    def source_filename_exists(self, source_filename: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM templates WHERE lower(source_filename) = lower(?) LIMIT 1",
                (source_filename,),
            ).fetchone()
        return bool(row)

    def list_templates(
        self, *, category_slug: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if category_slug:
            # A parent holds shelves rather than mockups, so asking for one
            # means asking for everything on the shelves under it -- otherwise
            # clicking "Wall Art" would show an empty studio.
            clauses.append("(c.slug = ? OR p.slug = ?)")
            values.extend([category_slug, category_slug])
        if status:
            clauses.append("t.status = ?")
            values.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT t.*, c.name AS category_name, c.slug AS product_type
                FROM templates t
                LEFT JOIN categories c ON c.id = t.category_id
                LEFT JOIN categories p ON p.id = c.parent_id
                {where} ORDER BY t.updated_at DESC
                """,
                values,
            ).fetchall()
        return [self._row_to_template(row) for row in rows]

    # A mockup's name has to say which shelf it sits on: the MAIN categories are
    # the ones a listing set draws its main shots from, and a name like "V1-3"
    # gave no way to tell one apart from an ordinary frame mockup. The prefix is
    # kept in step with the category here, so importing a template or dragging
    # one between categories cannot leave the two disagreeing.
    MAIN_CATEGORY_PREFIX = "MAIN-"

    @staticmethod
    def _is_main_category(category_name: str | None) -> bool:
        return str(category_name or "").strip().lower().startswith("main")

    def _named_for_category(self, name: str | None, category_id: Any) -> str | None:
        if not name:
            return name
        category = self.get_category(category_id) if category_id else None
        bare = name[len(self.MAIN_CATEGORY_PREFIX):] if name.upper().startswith(self.MAIN_CATEGORY_PREFIX) else name
        if self._is_main_category(category.get("name") if category else None):
            return f"{self.MAIN_CATEGORY_PREFIX}{bare}"
        return bare

    def update_template(self, template_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "name",
            "category_id",
            "artwork_area",
            "raw_artwork_area",
            "fit_mode",
            "orientation",
            "foreground_name",
            "mask_name",
            "detection_provider",
            "detection_confidence",
            "status",
            "effects",
        }
        if "name" in changes or "category_id" in changes:
            current = self.get_template(template_id) or {}
            category_id = changes.get("category_id", current.get("category_id"))
            renamed = self._named_for_category(changes.get("name", current.get("name")), category_id)
            if renamed and renamed != current.get("name"):
                changes = {**changes, "name": renamed}
        assignments: list[str] = []
        values: list[Any] = []
        for key, value in changes.items():
            if key not in allowed:
                continue
            assignments.append(f"{key} = ?")
            values.append(json.dumps(value) if key in {"artwork_area", "raw_artwork_area", "effects"} else value)
        if not assignments:
            current = self.get_template(template_id)
            if not current:
                raise CatalogError("Template not found")
            return current
        assignments.append("updated_at = ?")
        values.append(utc_now())
        values.append(template_id)
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE templates SET {', '.join(assignments)} WHERE template_id = ?",
                values,
            )
            if not cursor.rowcount:
                raise CatalogError("Template not found")
        self._checkpoint()
        return self.get_template(template_id)

    def delete_template(self, template_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM templates WHERE template_id = ?", (template_id,)
            )
            if not cursor.rowcount:
                raise CatalogError("Template not found")
        self._checkpoint()

    # ------------------------------------------------------------------
    # Listing sets: what a shop listing is made of, decided in advance.
    #
    # Automatic selection scores aspect-ratio fit and nothing else, so it can
    # neither know that a MAIN mockup is the thumbnail Etsy shows in search nor
    # keep one out of a filler slot. A set is the admin saying, once, exactly
    # which mockups a listing gets and in what order.
    # ------------------------------------------------------------------

    def create_listing_set(self, record: dict[str, Any]) -> dict[str, Any]:
        timestamp = utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO listing_sets(
                    name, product_type, orientation, status, items, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(record["name"]).strip(),
                    record.get("product_type"),
                    record.get("orientation", "any"),
                    record.get("status", "active"),
                    json.dumps(record.get("items") or []),
                    timestamp,
                    timestamp,
                ),
            )
            set_id = cursor.lastrowid
        self._checkpoint()
        return self.get_listing_set(set_id)

    def get_listing_set(self, set_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM listing_sets WHERE id = ?", (set_id,)
            ).fetchone()
        return self._row_to_listing_set(row)

    def list_listing_sets(
        self, *, product_type: str | None = None, orientation: str | None = None
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if product_type:
            clauses.append("product_type = ?")
            values.append(product_type)
        if orientation:
            # A set for one orientation and a set for any both serve that artwork.
            clauses.append("(orientation = ? OR orientation = 'any')")
            values.append(orientation)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM listing_sets {where} ORDER BY name", values
            ).fetchall()
        return [self._row_to_listing_set(row) for row in rows]

    def update_listing_set(self, set_id: int, changes: dict[str, Any]) -> dict[str, Any]:
        allowed = {"name", "product_type", "orientation", "status", "items"}
        assignments: list[str] = []
        values: list[Any] = []
        for key, value in changes.items():
            if key not in allowed:
                continue
            assignments.append(f"{key} = ?")
            values.append(json.dumps(value) if key == "items" else value)
        if not assignments:
            current = self.get_listing_set(set_id)
            if not current:
                raise CatalogError("Listing set not found")
            return current
        assignments.append("updated_at = ?")
        values.extend([utc_now(), set_id])
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE listing_sets SET {', '.join(assignments)} WHERE id = ?", values
            )
            if not cursor.rowcount:
                raise CatalogError("Listing set not found")
        self._checkpoint()
        return self.get_listing_set(set_id)

    def delete_listing_set(self, set_id: int) -> None:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM listing_sets WHERE id = ?", (set_id,))
            if not cursor.rowcount:
                raise CatalogError("Listing set not found")
        self._checkpoint()

    @staticmethod
    def _row_to_listing_set(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        record = dict(row)
        try:
            record["items"] = json.loads(record.get("items") or "[]")
        except json.JSONDecodeError:
            record["items"] = []
        return record

    # ------------------------------------------------------------------
    # Size guides: the print-size charts a listing ships with, kept as ready
    # made images instead of being drawn on every render.
    # ------------------------------------------------------------------

    def create_size_guide(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO size_guides(name, ratio, orientation, file_name, source, created_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    str(record["name"]).strip(),
                    str(record["ratio"]).strip(),
                    record.get("orientation", "portrait"),
                    str(record["file_name"]).strip(),
                    record.get("source", "upload"),
                    utc_now(),
                ),
            )
            guide_id = cursor.lastrowid
        self._checkpoint()
        return self.get_size_guide(guide_id)

    def get_size_guide(self, guide_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM size_guides WHERE id = ?", (guide_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_size_guides(
        self, *, ratio: str | None = None, orientation: str | None = None
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if ratio:
            clauses.append("ratio = ?")
            values.append(ratio)
        if orientation:
            clauses.append("orientation = ?")
            values.append(orientation)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM size_guides {where} ORDER BY ratio, name", values
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_size_guide(self, guide_id: int) -> dict[str, Any]:
        guide = self.get_size_guide(guide_id)
        if not guide:
            raise CatalogError("Size guide not found")
        with self._connect() as connection:
            connection.execute("DELETE FROM size_guides WHERE id = ?", (guide_id,))
        self._checkpoint()
        return guide

    def set_settings(self, settings: dict[str, str]) -> None:
        with self._connect() as connection:
            for key, value in settings.items():
                connection.execute(
                    """
                    INSERT INTO settings(key, value) VALUES(?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (key, value),
                )
        self._checkpoint()

    def get_settings(self) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: row["value"] for row in rows}

    @staticmethod
    def _row_to_template(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if not row:
            return None
        template = dict(row)
        template["artwork_area"] = (
            json.loads(template["artwork_area"]) if template["artwork_area"] else None
        )
        template["raw_artwork_area"] = (
            json.loads(template["raw_artwork_area"]) if template.get("raw_artwork_area") else None
        )
        template["effects"] = (
            json.loads(template["effects"]) if template.get("effects") else None
        )
        return template


def orientation_for_size(width: int, height: int) -> str:
    ratio = width / height
    if 0.92 <= ratio <= 1.08:
        return "square"
    return "landscape" if ratio > 1 else "portrait"
