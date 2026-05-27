import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.simple_mockup_service import InvalidTemplateError, TemplateNotFoundError, load_manifest


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
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self, templates_folder: Path) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    slug TEXT NOT NULL UNIQUE,
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
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
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
                }
            )

    def get_or_create_category(self, name: str) -> dict[str, Any]:
        existing = self.get_category_by_slug(slugify(name))
        return existing or self.create_category(name)

    def create_category(self, name: str) -> dict[str, Any]:
        cleaned = name.strip()
        if not cleaned:
            raise CatalogError("Category name is required")
        base_slug = slugify(cleaned)
        slug = base_slug
        suffix = 2
        with self._connect() as connection:
            while connection.execute(
                "SELECT 1 FROM categories WHERE slug = ?", (slug,)
            ).fetchone():
                slug = f"{base_slug}-{suffix}"
                suffix += 1
            try:
                cursor = connection.execute(
                    "INSERT INTO categories(name, slug, created_at) VALUES(?, ?, ?)",
                    (cleaned, slug, utc_now()),
                )
            except sqlite3.IntegrityError as error:
                raise CatalogError("Category already exists") from error
            category_id = cursor.lastrowid
        return self.get_category(category_id)

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

    def list_categories(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        condition = "WHERE t.status = 'active'" if active_only else ""
        query = f"""
            SELECT c.id, c.name, c.slug, COUNT(t.template_id) AS template_count
            FROM categories c LEFT JOIN templates t ON t.category_id = c.id
            {condition}
            GROUP BY c.id ORDER BY c.name COLLATE NOCASE
        """
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(query).fetchall()]

    def create_template(self, record: dict[str, Any]) -> dict[str, Any]:
        timestamp = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO templates(
                    template_id, name, category_id, status, canvas_width,
                    canvas_height, artwork_area, fit_mode, orientation,
                    background_name, preview_name, foreground_name, mask_name,
                    source_filename, detection_provider, detection_confidence,
                    created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    timestamp,
                    timestamp,
                ),
            )
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
            clauses.append("c.slug = ?")
            values.append(category_slug)
        if status:
            clauses.append("t.status = ?")
            values.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT t.*, c.name AS category_name, c.slug AS product_type
                FROM templates t LEFT JOIN categories c ON c.id = t.category_id
                {where} ORDER BY t.updated_at DESC
                """,
                values,
            ).fetchall()
        return [self._row_to_template(row) for row in rows]

    def update_template(self, template_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "name",
            "category_id",
            "artwork_area",
            "fit_mode",
            "orientation",
            "foreground_name",
            "mask_name",
            "detection_provider",
            "detection_confidence",
            "status",
        }
        assignments: list[str] = []
        values: list[Any] = []
        for key, value in changes.items():
            if key not in allowed:
                continue
            assignments.append(f"{key} = ?")
            values.append(json.dumps(value) if key == "artwork_area" else value)
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
        return self.get_template(template_id)

    def delete_template(self, template_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM templates WHERE template_id = ?", (template_id,)
            )
            if not cursor.rowcount:
                raise CatalogError("Template not found")

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
        return template


def orientation_for_size(width: int, height: int) -> str:
    ratio = width / height
    if 0.92 <= ratio <= 1.08:
        return "square"
    return "landscape" if ratio > 1 else "portrait"
