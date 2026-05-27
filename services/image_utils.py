from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename


ALLOWED_ARTWORK_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


class ImageProcessingError(ValueError):
    pass


def store_uploaded_artwork(upload: FileStorage, upload_folder: Path) -> Path:
    safe_name = secure_filename(upload.filename or "")
    suffix = Path(safe_name).suffix.lower()
    if not safe_name or suffix not in ALLOWED_ARTWORK_EXTENSIONS:
        raise ImageProcessingError("Unsupported artwork file type")

    upload_folder.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    stored_name = f"{Path(safe_name).stem}_{timestamp}_{uuid4().hex}{suffix}"
    stored_path = upload_folder / stored_name
    upload.save(stored_path)

    try:
        load_rgba(stored_path)
    except ImageProcessingError:
        stored_path.unlink(missing_ok=True)
        raise
    return stored_path


def load_rgba(image_path: Path) -> Image.Image:
    try:
        with Image.open(image_path) as image:
            return image.convert("RGBA")
    except (FileNotFoundError, UnidentifiedImageError, OSError) as error:
        raise ImageProcessingError(f"Unable to read image: {image_path.name}") from error


def load_mask(image_path: Path) -> Image.Image:
    try:
        with Image.open(image_path) as image:
            if "A" in image.getbands():
                return image.getchannel("A")
            return image.convert("L")
    except (FileNotFoundError, UnidentifiedImageError, OSError) as error:
        raise ImageProcessingError(f"Unable to read mask: {image_path.name}") from error


def fit_artwork(
    artwork: Image.Image, size: tuple[int, int], fit_mode: str = "cover"
) -> Image.Image:
    if size[0] <= 0 or size[1] <= 0:
        raise ImageProcessingError("Artwork area dimensions must be positive")

    normalized_mode = (fit_mode or "cover").lower()
    if normalized_mode == "cover":
        return ImageOps.fit(
            artwork, size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5)
        )
    if normalized_mode == "stretch":
        return artwork.resize(size, Image.Resampling.LANCZOS)
    if normalized_mode == "contain":
        contained = ImageOps.contain(artwork, size, method=Image.Resampling.LANCZOS)
        layer = Image.new("RGBA", size, (0, 0, 0, 0))
        offset = (
            (size[0] - contained.width) // 2,
            (size[1] - contained.height) // 2,
        )
        layer.alpha_composite(contained, dest=offset)
        return layer
    raise ImageProcessingError(f"Unsupported fit mode: {fit_mode}")
