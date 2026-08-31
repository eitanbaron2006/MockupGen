"""The print-size charts a listing ships with.

A size chart is the one picture in a listing a buyer measures a wall against,
so it is kept as a ready-made file the admin uploaded once per ratio, not drawn
fresh on every render. Where the library has nothing for a shape, Vertex can
draw one -- with the caveat that image models are unreliable at rendering exact
numbers, which is why the library comes first and the generated chart is marked
as such in the answer.
"""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PIL import Image
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from services.image_utils import ImageProcessingError

ALLOWED_GUIDE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


class SizeGuideError(ValueError):
    """A guide that cannot be stored or drawn."""


def guide_path(guides_folder: Path, file_name: str) -> Path:
    """Where one stored guide lives, refusing anything that climbs out.

    The file name comes from the catalog, but the catalog is edited through the
    admin API, so it is treated as untrusted here the same way an uploaded name
    is.
    """
    cleaned = str(file_name or "").strip()
    if not cleaned or Path(cleaned).name != cleaned or cleaned in {".", ".."}:
        raise SizeGuideError("Invalid size guide file name")
    resolved = (guides_folder / cleaned).resolve()
    if not str(resolved).startswith(str(Path(guides_folder).resolve())):
        raise SizeGuideError("Invalid size guide file name")
    return resolved


def store_guide_upload(upload: FileStorage, guides_folder: Path) -> tuple[str, int, int]:
    """Save an uploaded chart; returns its stored name and pixel size."""
    safe_name = secure_filename(upload.filename or "")
    suffix = Path(safe_name).suffix.lower()
    if not safe_name or suffix not in ALLOWED_GUIDE_EXTENSIONS:
        raise SizeGuideError(
            f"Unsupported file type. Use one of: {', '.join(sorted(ALLOWED_GUIDE_EXTENSIONS))}"
        )
    guides_folder = Path(guides_folder)
    guides_folder.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    stored_name = f"guide_{timestamp}_{uuid4().hex}{suffix}"
    stored_path = guides_folder / stored_name
    upload.save(stored_path)
    try:
        with Image.open(stored_path) as image:
            image.verify()
        with Image.open(stored_path) as image:
            size = image.size
    except (OSError, ValueError) as error:
        stored_path.unlink(missing_ok=True)
        raise SizeGuideError("That file is not a readable image") from error
    return stored_name, size[0], size[1]


def store_guide_image(image: Image.Image, guides_folder: Path) -> tuple[str, int, int]:
    """Keep a generated chart in the library, so it is drawn once and not per render."""
    guides_folder = Path(guides_folder)
    guides_folder.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    stored_name = f"guide_{timestamp}_{uuid4().hex}.png"
    image.save(guides_folder / stored_name, format="PNG")
    return stored_name, image.width, image.height


# What a shop's size guide actually looks like: the print hanging in a room,
# drawn to scale against furniture a buyer can judge a wall by -- not a bare
# diagram. The admin can rewrite this; the placeholders are what the studio
# fills in, and any that are left out simply are not substituted.
DEFAULT_GUIDE_PROMPT = (
    "A photorealistic wall art size guide for a print shop listing.\n"
    "Scene: a calm, minimal interior. A plain warm off-white wall fills most of the frame. "
    "Along the bottom sits a low beige linen sofa seen straight on, with a potted plant to one "
    "side and a light oak floor. Soft natural daylight, no harsh shadows.\n"
    "On the wall above the sofa, hang {count} empty picture frames of the SAME {orientation} "
    "{ratio} proportion, in ascending size, arranged in a tidy row that steps upward. Thin dark "
    "wood frames with white mattes and blank white centres -- no artwork inside them.\n"
    "The frames must be drawn to scale with each other and with the sofa: a 90 inch (229 cm) sofa "
    "is the reference, so the largest frame is roughly two thirds of the sofa's width.\n"
    "Label each frame with its size in a small clean sans-serif, both units, placed just inside "
    "the top of that frame: {sizes} (measurements in {unit}).\n"
    "Add the heading WALL ART SIZE GUIDE in an elegant serif at the top of the wall, and the words "
    "{ratio} RATIO under it in small letters.\n"
    "No other text, no watermarks, no people, no clutter. Editorial, neutral, high resolution."
)

PROMPT_FIELDS = ("ratio", "sizes", "unit", "orientation", "count")


def guide_prompt(
    ratio: str,
    sizes: list[str],
    unit: str,
    orientation: str,
    template: str | None = None,
) -> str:
    """The instruction sent to the model, with the studio's own values in it.

    A template the admin edited is honoured as written -- the placeholders are
    optional. The sizes are the one thing that cannot be left to the wording,
    so when the template never mentions them they are stated at the end.
    """
    text = (template or "").strip() or DEFAULT_GUIDE_PROMPT
    values = {
        "ratio": ratio,
        "sizes": ", ".join(sizes),
        "unit": unit,
        "orientation": orientation,
        "count": len(sizes),
    }
    for field in PROMPT_FIELDS:
        text = text.replace("{" + field + "}", str(values[field]))
    if "{sizes}" not in (template or DEFAULT_GUIDE_PROMPT) and values["sizes"] not in text:
        text = f"{text}\nThe sizes to show, in {unit}: {values['sizes']}."
    return text


def generate_size_guide(
    *,
    family: str,
    sizes: list[str],
    unit: str,
    orientation: str,
    project_id: str,
    location: str = "global",
    model: str = "gemini-3.1-flash-image",
    template: str | None = None,
) -> Image.Image:
    """Ask Vertex for a chart, from the studio's prompt or the admin's own."""
    try:
        from google import genai
        from google.genai import types
    except ImportError as error:  # pragma: no cover - depends on the install
        raise SizeGuideError("google-genai is not installed") from error

    if not project_id:
        raise SizeGuideError("Vertex AI is not configured (no project id)")

    client = genai.Client(
        vertexai=True,
        project=project_id,
        location=location,
        http_options=types.HttpOptions(api_version="v1"),
    )
    response = client.models.generate_content(
        model=model,
        contents=[guide_prompt(family, sizes, unit, orientation, template)],
        config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"]),
    )
    for candidate in response.candidates or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            if inline and (inline.mime_type or "").startswith("image/"):
                try:
                    return Image.open(BytesIO(inline.data)).convert("RGB")
                except (OSError, ValueError) as error:
                    raise SizeGuideError("Vertex returned an unreadable image") from error
    raise SizeGuideError("Vertex returned no image for the size guide")


def guide_image(guides_folder: Path, record: dict) -> Image.Image:
    """One stored guide, opened."""
    try:
        with Image.open(guide_path(Path(guides_folder), record.get("file_name", ""))) as image:
            image.load()
            return image.convert("RGB")
    except (OSError, ValueError) as error:
        raise ImageProcessingError("The stored size guide could not be read") from error
