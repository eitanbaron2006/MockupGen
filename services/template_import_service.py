import json
import shutil
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from PIL import Image, UnidentifiedImageError
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from services.catalog_service import CatalogError, CatalogService, orientation_for_size
from services.image_utils import ALLOWED_ARTWORK_EXTENSIONS


class TemplateImportError(ValueError):
    pass


def import_backgrounds(
    uploads: Iterable[FileStorage],
    *,
    category_id: int,
    drafts_folder: Path,
    catalog: CatalogService,
) -> list[dict[str, Any]]:
    if not catalog.get_category(category_id):
        raise TemplateImportError("Category not found")
    validated_uploads: list[tuple[FileStorage, str]] = []
    pending_names: set[str] = set()
    for upload in list(uploads):
        safe_name = secure_filename(upload.filename or "")
        if not safe_name or Path(safe_name).suffix.lower() not in ALLOWED_ARTWORK_EXTENSIONS:
            raise TemplateImportError("Only PNG, JPG, JPEG, and WebP mockup images are allowed")
        normalized_name = safe_name.casefold()
        if normalized_name in pending_names or catalog.source_filename_exists(safe_name):
            raise TemplateImportError(f"A mockup image named {safe_name} already exists")
        pending_names.add(normalized_name)
        validated_uploads.append((upload, safe_name))
    if not validated_uploads:
        raise TemplateImportError("Select at least one mockup image")

    imported: list[dict[str, Any]] = []
    for upload, safe_name in validated_uploads:
        try:
            image = Image.open(upload.stream).convert("RGBA")
        except (UnidentifiedImageError, OSError) as error:
            raise TemplateImportError(f"Unable to read mockup image: {safe_name}") from error
        template_id = f"template_{uuid4().hex[:12]}"
        draft_folder = drafts_folder / template_id
        draft_folder.mkdir(parents=True, exist_ok=False)
        image.save(draft_folder / "background.png", format="PNG")
        preview = image.copy()
        preview.thumbnail((420, 420), Image.Resampling.LANCZOS)
        preview.save(draft_folder / "preview.png", format="PNG")
        imported.append(
            catalog.create_template(
                {
                    "template_id": template_id,
                    "name": Path(safe_name).stem.replace("_", " ").strip(),
                    "category_id": category_id,
                    "status": "draft",
                    "canvas_width": image.width,
                    "canvas_height": image.height,
                    "orientation": orientation_for_size(image.width, image.height),
                    "source_filename": safe_name,
                }
            )
        )
    return imported


def draft_asset_path(drafts_folder: Path, template_id: str, asset_name: str) -> Path:
    if Path(template_id).name != template_id or asset_name not in {
        "background.png",
        "preview.png",
        "foreground.png",
        "mask.png",
    }:
        raise TemplateImportError("Asset not found")
    asset_path = (drafts_folder / template_id / asset_name).resolve()
    if drafts_folder.resolve() not in asset_path.parents or not asset_path.is_file():
        raise TemplateImportError("Asset not found")
    return asset_path


def _safe_template_directory(root_folder: Path, template_id: str) -> Path:
    if Path(template_id).name != template_id:
        raise TemplateImportError("Template not found")
    root = root_folder.resolve()
    template_folder = (root_folder / template_id).resolve()
    if root == template_folder or root not in template_folder.parents:
        raise TemplateImportError("Template asset path is invalid")
    return template_folder


def delete_template_assets(
    template_id: str,
    *,
    drafts_folder: Path,
    templates_folder: Path,
) -> None:
    for root_folder in (drafts_folder, templates_folder):
        template_folder = _safe_template_directory(root_folder, template_id)
        if not template_folder.exists():
            continue
        if not template_folder.is_dir():
            raise TemplateImportError("Template asset path is invalid")
        shutil.rmtree(template_folder)


def publish_template(
    template_id: str,
    *,
    catalog: CatalogService,
    drafts_folder: Path,
    templates_folder: Path,
) -> dict[str, Any]:
    template = catalog.get_template(template_id)
    if not template:
        raise TemplateImportError("Template not found")
    if not template.get("artwork_area"):
        raise TemplateImportError("Set or detect an artwork area before activation")
    if not template.get("product_type"):
        raise TemplateImportError("Select a category before activation")
    published_folder = templates_folder / template_id
    draft_folder = drafts_folder / template_id
    if draft_folder.exists():
        source_folder = draft_folder
    elif published_folder.exists():
        source_folder = published_folder
    else:
        raise TemplateImportError("Template assets not found")
    published_folder.mkdir(parents=True, exist_ok=True)
    for name in ("background.png", "preview.png", "foreground.png", "mask.png"):
        source = source_folder / name
        destination = published_folder / name
        if source.is_file() and source.resolve() != destination.resolve():
            shutil.copy2(source, published_folder / name)
    foreground = "foreground.png" if (published_folder / "foreground.png").is_file() else None
    mask = "mask.png" if (published_folder / "mask.png").is_file() else None
    manifest = {
        "template_id": template_id,
        "name": template["name"],
        "product_type": template["product_type"],
        "canvas_width": template["canvas_width"],
        "canvas_height": template["canvas_height"],
        "artwork_area": template["artwork_area"],
        "fit_mode": template["fit_mode"],
        "orientation": template["orientation"],
        "background": "background.png",
        "foreground": foreground,
        "mask": mask,
        "preview": "preview.png",
        "supported_modes": ["simple"],
        "output_format": "png",
    }
    (published_folder / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return catalog.update_template(
        template_id,
        {"status": "active", "foreground_name": foreground, "mask_name": mask},
    )
