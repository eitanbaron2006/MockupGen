"""Mockups drawn to order, instead of bought and imported.

A mockup in this studio is a photograph of a room with a flat green rectangle
where the artwork goes: the detector reads that green, and everything
downstream -- the frames, the masks, the renders -- follows from it. That means
a generated mockup does not need a model to be good at anything models are bad
at. It needs a convincing room and one perfectly flat green rectangle.

So the prompt asks for exactly that, and nothing is trusted on the model's word
afterwards: every generated image is put through the same detector the studio
uses, and reported on -- how many frames it found, how uniform each green is,
and how far from chroma-key green it sits. An image that fails is still handed
back with its report rather than silently kept, because the admin is the one
who decides whether to keep it.
"""
from __future__ import annotations

from io import BytesIO
from typing import Any

import numpy as np
from PIL import Image

CHROMA_GREEN = (0, 255, 0)

# Rooms a print is photographed in. The scene is all that changes between
# them; what makes the picture usable -- the flat green -- is the same demand
# in every one of them.
SCENE_PRESETS = (
    {
        "key": "living",
        "name": "Living room",
        "note": "Linen sofa, plant, warm daylight",
        "scene": (
            "a calm modern living room: a low beige linen sofa against a plain warm off-white "
            "wall, a potted olive tree to one side, a light oak floor, soft daylight from a "
            "window out of shot"
        ),
    },
    {
        "key": "bedroom",
        "name": "Bedroom",
        "note": "Made bed, linen bedding, morning light",
        "scene": (
            "a serene bedroom: a made bed with rumpled cream linen and two pillows below a plain "
            "chalk-white wall, a small wooden nightstand with a ceramic vase, morning light"
        ),
    },
    {
        "key": "dining",
        "name": "Dining room",
        "note": "Table, chairs, a bowl of fruit",
        "scene": (
            "a bright dining room: a solid oak table with two pale upholstered chairs against a "
            "smooth white wall, a stoneware bowl on the table, wide daylight"
        ),
    },
    {
        "key": "hallway",
        "name": "Hallway",
        "note": "Console table, mirror, tall plant",
        "scene": (
            "an elegant hallway: a narrow oak console table against a warm white wall, a woven "
            "basket with a tall plant beside it, a pale rug, gentle afternoon light"
        ),
    },
    {
        "key": "office",
        "name": "Study",
        "note": "Desk, chair, books",
        "scene": (
            "a quiet study: a wooden desk with a simple chair against a soft grey-white wall, a "
            "few books and a small lamp on the desk, clean daylight"
        ),
    },
    {
        "key": "nursery",
        "name": "Nursery",
        "note": "Cot, soft toys, pastel tones",
        "scene": (
            "a gentle nursery: a white cot with cream bedding against a pale wall, a knitted "
            "basket of soft toys, a small wooden stool, warm diffuse light"
        ),
    },
)

# Everything about the picture that the studio depends on, said in the terms a
# model responds to and repeated where it matters. The admin can rewrite this;
# the green clause is re-appended regardless, because an image without it is
# not a mockup this studio can use at all.
DEFAULT_MOCKUP_PROMPT = (
    "A photorealistic interior mockup photograph for a print shop, shot straight on with a "
    "wide lens, natural light, no people, no text, no watermark.\n"
    "Scene: {scene}.\n"
    "On the wall hang {frames} empty picture {frame_word} in {orientation} {ratio} proportion, "
    "{arrangement} Real frames with visible depth: a slim wooden moulding, a soft shadow on the "
    "wall behind each one. Photographed level, so each frame reads as a clean rectangle.\n"
    "Editorial interior photography, high resolution, sharp, uncluttered."
)

# The one thing the picture is actually for.
GREEN_CLAUSE = (
    "CRITICAL: the inside of every frame -- the whole area where a print would sit -- must be "
    "filled with one single flat chroma-key green, pure RGB 0,255,0, and nothing else. That "
    "green must be perfectly uniform: no artwork, no pattern, no text, no gradient, no shading, "
    "no shadow falling across it, no glass reflection or glare, no texture, no vignette, no "
    "colour variation of any kind, edge to edge. It must meet the inside edge of the frame "
    "exactly, with no mat, no border and no white margin between the green and the moulding. "
    "The green areas are the only green in the photograph: nothing else in the room may be "
    "green. Every other surface is lit normally."
)


class MockupGenerationError(RuntimeError):
    """The model would not, or could not, draw the mockup."""


def scene_preset(key: str) -> dict[str, Any] | None:
    for preset in SCENE_PRESETS:
        if preset["key"] == key:
            return preset
    return None


def mockup_prompt(
    *,
    scene: str,
    frames: int = 1,
    orientation: str = "portrait",
    ratio: str = "2:3",
    template: str | None = None,
) -> str:
    """The instruction sent to the model, with the green demand always on it."""
    arrangement = (
        "hung side by side in a row, evenly spaced and level with each other."
        if frames > 1
        else "centred on the wall."
    )
    text = (template or "").strip() or DEFAULT_MOCKUP_PROMPT
    for field, value in {
        "scene": scene,
        "frames": frames,
        "frame_word": "frames" if frames > 1 else "frame",
        "orientation": orientation,
        "ratio": ratio,
        "arrangement": arrangement,
    }.items():
        text = text.replace("{" + field + "}", str(value))
    if "chroma-key green" not in text:
        text = f"{text}\n{GREEN_CLAUSE}"
    return text


def generate_mockup(
    *,
    prompt: str,
    project_id: str,
    location: str = "global",
    model: str = "gemini-3.1-flash-image",
    reference: tuple[bytes, str] | None = None,
) -> Image.Image:
    """Ask Vertex for one mockup photograph."""
    try:
        from google import genai
        from google.genai import types
    except ImportError as error:  # pragma: no cover - depends on the install
        raise MockupGenerationError("google-genai is not installed") from error
    if not project_id:
        raise MockupGenerationError("Vertex AI is not configured (no project id)")

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
        contents.append("Photograph a room in the style of the image above, following this exactly:")
    contents.append(prompt)
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
                    raise MockupGenerationError("Vertex returned an unreadable image") from error
    raise MockupGenerationError("Vertex returned no image")


# Calibrated on real generations rather than on flat test images. Measured on
# one opening: a clean generation reads 15-16 (the model's own compression
# noise), a light shadow across part of it 19, a glass glare 25, a real shadow
# 43. So above 22 the green is broken; between 18 and 22 it still keys but is
# worth a look, and a synthetic flat green sits near zero.
UNIFORMITY_LIMIT = 22.0
UNIFORMITY_NOTICE = 18.0
# ...and a green too far from chroma-key is a green the detector may miss at a
# tolerance the user has lowered.
DISTANCE_LIMIT = 90.0
# An opening smaller than this is not worth publishing as a frame.
MIN_OPENING_SHARE = 0.004


def inspect_green(image: Image.Image, *, expected_frames: int = 1) -> dict[str, Any]:
    """Put a generated mockup through the studio's own detector, and report.

    The model is asked for flat green and cannot be taken at its word: a
    shadow across an opening, a reflection, or a green that drifted towards
    teal all pass a glance and fail a render. Everything here is measured on
    the pixels, and nothing is rejected quietly -- the numbers go back with the
    picture so the admin can see why.
    """
    from services.green_frame_mockup_service import GreenFrameSettings, detect_green_frames

    detection = detect_green_frames(image.convert("RGBA"), GreenFrameSettings())
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    mask = detection.detect_mask
    canvas = image.width * image.height

    frames: list[dict[str, Any]] = []
    for index, region in enumerate(detection.regions, start=1):
        window = mask[region.y : region.y + region.h, region.x : region.x + region.w]
        pixels = rgb[region.y : region.y + region.h, region.x : region.x + region.w][window]
        if pixels.size == 0:
            continue
        mean = pixels.mean(axis=0)
        spread = float(pixels.std(axis=0).max())
        distance = float(np.linalg.norm(mean - np.array(CHROMA_GREEN, dtype=np.float32)))
        share = region.area / canvas if canvas else 0.0
        frames.append(
            {
                "frame": index,
                "x": region.x,
                "y": region.y,
                "width": region.w,
                "height": region.h,
                "share": round(share, 5),
                "mean_color": [round(float(value)) for value in mean],
                "uniformity": round(spread, 2),
                "distance_from_chroma": round(distance, 1),
                "flat": spread <= UNIFORMITY_LIMIT,
                "spotless": spread <= UNIFORMITY_NOTICE,
                "on_colour": distance <= DISTANCE_LIMIT,
                "big_enough": share >= MIN_OPENING_SHARE,
            }
        )

    problems: list[str] = []
    warnings: list[str] = []
    if len(frames) != expected_frames:
        problems.append(
            f"Found {len(frames)} green {'opening' if len(frames) == 1 else 'openings'}, "
            f"asked for {expected_frames}"
        )
    for frame in frames:
        if not frame["flat"]:
            problems.append(
                f"Frame {frame['frame']}: the green is not flat "
                f"({frame['uniformity']} of {UNIFORMITY_LIMIT} allowed) -- a shadow or "
                "reflection is falling across it"
            )
        elif not frame["spotless"]:
            # Still keys, but a seller should look before publishing it.
            warnings.append(
                f"Frame {frame['frame']}: the green is slightly uneven "
                f"({frame['uniformity']}) -- it will still key, but look for a soft shadow"
            )
        if not frame["on_colour"]:
            problems.append(
                f"Frame {frame['frame']}: the green is off chroma-key by "
                f"{frame['distance_from_chroma']}"
            )
        if not frame["big_enough"]:
            problems.append(f"Frame {frame['frame']}: the opening is too small to publish")

    return {
        "usable": not problems,
        "expected_frames": expected_frames,
        "found_frames": len(frames),
        "frames": frames,
        "problems": problems,
        "warnings": warnings,
    }
