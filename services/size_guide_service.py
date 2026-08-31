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
from typing import Any
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

# The size guides shops actually publish come in a handful of recognisable
# styles, and which one suits a listing is the seller's taste, not ours. Each
# is offered as it is, and any of them can be opened and rewritten.
GUIDE_PROMPT_PRESETS = (
    {
        "key": "room",
        "name": "Room & sofa",
        "note": "Frames above a sofa, scaled against a 90 inch reference",
        "prompt": DEFAULT_GUIDE_PROMPT,
    },
    {
        "key": "outlined",
        "name": "Outlined on the wall",
        "note": "Drawn frame outlines above a sofa, with the sofa measured",
        "prompt": (
            "An elegant wall art size guide for a print shop listing.\n"
            "Scene: a bright, airy room photographed straight on. A soft white wall fills the upper "
            "two thirds; a cream linen sofa with a few pale cushions sits along the bottom, a tall "
            "green plant in a woven basket to one side, herringbone wood floor. Warm daylight, gentle "
            "shadows, editorial styling.\n"
            "On the wall, do not hang real frames: draw {count} rectangle outlines in a fine dark "
            "brown line, all of the same {orientation} {ratio} proportion, ascending in size from "
            "left to right, their bottoms stepping up in a gentle diagonal. The outlines are empty; "
            "the wall shows through them.\n"
            "They are drawn to scale with the sofa: below them, draw a thin horizontal measuring line "
            "spanning the sofa with a small arrow at each end and the caption 90 in (229 cm) centred "
            "on it, so the sizes can be judged against real furniture.\n"
            "Inside the top-right corner of each outline, set its size on two lines in a small dark "
            "sans-serif: {sizes}.\n"
            "Nothing else on the wall: no title, no logo, no watermark, no people. Calm, expensive, "
            "high resolution."
        ),
    },
    {
        "key": "gallery",
        "name": "Gallery wall",
        "note": "Several frames spread across a wall, each labelled",
        "prompt": (
            "A photorealistic wall art size guide for a print shop listing.\n"
            "Scene: a bright minimal living room, plain white wall, a light sofa low in the frame, "
            "a small plant and a pale wooden floor. Soft daylight.\n"
            "On the wall, arrange {count} empty frames of {ratio} {orientation} proportion as a "
            "gallery wall -- not a straight row -- at different heights, all drawn to scale with "
            "each other and with the sofa. Thin light oak frames, white mattes, blank centres.\n"
            "Print each frame's size across its blank centre in a small grey sans-serif: {sizes}.\n"
            "No heading, no other text, no people. Editorial and calm."
        ),
    },
    {
        "key": "iso",
        "name": "ISO A series",
        "note": "A0-A5 in centimetres, the European chart",
        "prompt": (
            "A clean wall art size chart for the ISO A paper sizes.\n"
            "On a soft off-white background, draw the A-series rectangles in {orientation} "
            "orientation, each drawn to scale with the others and arranged so every one stays "
            "visible -- larger sizes behind, smaller ones in front. Thin dark outlines, unfilled.\n"
            "Label each with its name and both units: {sizes}.\n"
            "Title it WALL ART SIZE GUIDE (A PAPER SIZES) in a light serif at the top. "
            "No room, no furniture, no other text."
        ),
    },
    {
        "key": "figure",
        "name": "Person for scale",
        "note": "A silhouette beside the frames, for judging real size",
        "prompt": (
            "A minimal wall art size guide with a human figure for scale.\n"
            "On a plain pale background, draw a simple dark silhouette of a standing person on the "
            "left, labelled with a height of 175 cm (5 ft 9 in). To the right of the figure, draw "
            "{count} empty rectangles of {ratio} {orientation} proportion, ascending in size and "
            "drawn to scale with the figure, thin dark outlines only.\n"
            "Label each rectangle with its size in both units: {sizes}.\n"
            "Title it WALL ART SIZE GUIDE in a small elegant serif at the top. "
            "No furniture, no colour, no other text."
        ),
    },
)


def preset_prompt(key: str) -> str | None:
    """One of the studio's styles, by name."""
    for preset in GUIDE_PROMPT_PRESETS:
        if preset["key"] == key:
            return preset["prompt"]
    return None


# Image models misspell what they letter -- one chart came back reading
# "10bs15 cm" for "10x15 cm" -- and a size guide whose numbers are wrong is
# worse than none, because a buyer measures a wall by it. This is stated in the
# strongest terms and appended to every prompt, including one the admin
# rewrote: it is the single instruction that must not be edited away.
TEXT_ACCURACY_CLAUSE = (
    "TEXT ACCURACY IS THE MOST IMPORTANT REQUIREMENT. Render every label exactly as "
    "written above, character for character -- the digits, the x between them, the "
    "brackets and the unit. Do not invent, translate, abbreviate, re-order, misspell or "
    "duplicate any label, and do not letter any word that is not listed above. Each size "
    "appears exactly once, on its own frame. Every character must be crisp and legible; "
    "if a label will not fit cleanly, set it smaller rather than distorting it."
)


STYLE_EXAMPLE_FOLDER = "styles"


def style_example_path(guides_folder: Path, key: str) -> Path | None:
    """The picture a style was modelled on, if the admin has supplied one.

    Words carry a style only so far; the example is what the model is shown so
    that "elegant" means the admin's idea of it and not the model's.
    """
    cleaned = "".join(character for character in str(key or "") if character.isalnum() or character in "-_")
    if not cleaned:
        return None
    folder = Path(guides_folder) / STYLE_EXAMPLE_FOLDER
    for suffix in (".png", ".jpg", ".jpeg", ".webp"):
        candidate = folder / f"{cleaned}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def store_style_example(upload: FileStorage, guides_folder: Path, key: str) -> Path:
    """Keep one example per style, replacing whatever was there before."""
    cleaned = "".join(character for character in str(key or "") if character.isalnum() or character in "-_")
    if not cleaned:
        raise SizeGuideError("Unknown style")
    suffix = Path(secure_filename(upload.filename or "")).suffix.lower()
    if suffix not in ALLOWED_GUIDE_EXTENSIONS:
        raise SizeGuideError(
            f"Unsupported file type. Use one of: {', '.join(sorted(ALLOWED_GUIDE_EXTENSIONS))}"
        )
    folder = Path(guides_folder) / STYLE_EXAMPLE_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    existing = style_example_path(guides_folder, key)
    if existing:
        existing.unlink(missing_ok=True)
    stored = folder / f"{cleaned}{suffix}"
    upload.save(stored)
    try:
        with Image.open(stored) as image:
            image.verify()
    except (OSError, ValueError) as error:
        stored.unlink(missing_ok=True)
        raise SizeGuideError("That file is not a readable image") from error
    return stored


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
    if TEXT_ACCURACY_CLAUSE not in text:
        text = f"{text}\n{TEXT_ACCURACY_CLAUSE}"
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
    reference: tuple[bytes, str] | None = None,
) -> Image.Image:
    """Ask Vertex for a chart, from the studio's prompt or the admin's own.

    A reference image, where one is given, is sent with the wording: showing
    the model the chart to imitate carries a style further than describing it.
    """
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
    contents: list[Any] = []
    if reference is not None:
        data, mime = reference
        contents.append(types.Part.from_bytes(data=data, mime_type=mime or "image/png"))
        contents.append(
            "Draw a new size guide in the style of the image above, following these "
            "instructions exactly:"
        )
    contents.append(guide_prompt(family, sizes, unit, orientation, template))
    response = client.models.generate_content(
        model=model,
        contents=contents,
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
