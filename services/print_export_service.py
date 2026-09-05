"""The print files a buyer downloads, made from the artwork the seller uploaded.

A shop sells one artwork as a set of high-resolution files, one per aspect
ratio, and the buyer prints whichever matches the frame they own. The rule that
matters is that the artwork is never cropped: a ratio the artwork does not fill
gets white margins instead of a trimmed edge, because a border trimmed off a
print is a refund.

The scaling ladder here is the one from the seller's own resizer
(``archive/experiments/etsy_frame_resizer_v3_safe_fit_no_crop.py``), carried
across unchanged: stepped LANCZOS for big enlargements, bicubic with a light
unsharp for the usual case, and two AI modes that call external programs. Those
two are offered only where the program is actually installed -- the same rule
the studio applies to AVIF -- because a quality that fails at save time is
worse than one that was never offered.
"""
from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter

# Print files run to tens of megapixels; Pillow's decompression guard is aimed
# at untrusted uploads, and this is the studio's own output.
Image.MAX_IMAGE_PIXELS = None


class PrintExportError(ValueError):
    """A print file that cannot be made as asked."""


# How the artwork meets a canvas it does not exactly fill. Ported from the
# seller's own resizer, wording included, because these are the three choices
# the shop already thinks in.
OUTPUT_MODES = (
    {
        "key": "safe_fit",
        "name": "Safe Fit",
        "note": "No crop, white margins if needed",
        "cuts": False,
        "recommended": True,
    },
    {
        "key": "safe_fill",
        "name": "Safe Fill",
        "note": "No crop, blurred extension instead of margins",
        "cuts": False,
        "recommended": False,
    },
    {
        "key": "fill_crop",
        "name": "Expert: Fill / Crop",
        "note": "Fills the canvas edge to edge -- may cut the artwork",
        "cuts": True,
        "recommended": False,
    },
)

MODE_KEYS = tuple(mode["key"] for mode in OUTPUT_MODES)
DEFAULT_MODE = "safe_fit"

# The studio ships Real-ESRGAN with it, under tools/, so the AI quality
# works on a fresh checkout with nothing to install and nothing to
# configure. The usual install locations are still checked after it, and
# a path set in the settings still wins over both.
BUNDLED_ROOT = Path(__file__).resolve().parents[1] / 'tools'

TOOL_CANDIDATES = {
    "realesrgan": (
        str(BUNDLED_ROOT / 'realesrgan' / 'realesrgan-ncnn-vulkan.exe'),
        r"C:\realesrgan\realesrgan-ncnn-vulkan.exe",
        r"C:\Program Files\realesrgan\realesrgan-ncnn-vulkan.exe",
        r"C:\Tools\realesrgan\realesrgan-ncnn-vulkan.exe",
    ),
    "topaz": (
        r"C:\Program Files\Topaz Labs LLC\Topaz Photo AI\tpai.exe",
        r"C:\Program Files\Topaz Labs LLC\Topaz Gigapixel AI\gigapixel.exe",
    ),
}

PROGRAM_NAMES = {
    "realesrgan": "realesrgan-ncnn-vulkan.exe",
    "topaz": "tpai.exe",
}


def discover_tool(name: str) -> str:
    """Where this machine actually keeps one of the two upscalers, if anywhere.

    An empty setting used to mean "unavailable", which reported Real-ESRGAN as
    missing on a machine where it was installed and working. The usual
    locations are checked instead, and PATH after them.
    """
    for candidate in TOOL_CANDIDATES.get(name, ()):
        if Path(candidate).is_file():
            return candidate
    found = shutil.which(PROGRAM_NAMES.get(name, name))
    return found or ""


def resolved_tools(settings: dict[str, str] | None = None) -> dict[str, str]:
    """The configured path where there is one, and what was found where not."""
    settings = settings or {}
    resolved = {}
    for name, key in (("realesrgan", "realesrgan_path"), ("topaz", "topaz_path")):
        configured = str(settings.get(key) or settings.get(name) or "").strip()
        resolved[name] = configured or discover_tool(name)
    return resolved


# What each quality does, and what it needs. The order is the order the studio
# offers them: the cheapest that looks right, first.
QUALITIES = (
    {
        "key": "bicubic",
        "name": "Bicubic + unsharp",
        "note": "The everyday choice: one bicubic pass with a light sharpen",
        "needs": None,
    },
    {
        "key": "step",
        "name": "Stepped LANCZOS",
        "note": "Grows in 1.5x steps -- gentler on big enlargements",
        "needs": None,
    },
    {
        "key": "step-unsharp",
        "name": "Stepped + unsharp",
        "note": "The same, with the sharpen applied at the end",
        "needs": None,
    },
    {
        "key": "basic",
        "name": "Single LANCZOS",
        "note": "One resize, nothing else",
        "needs": None,
    },
    {
        "key": "ai",
        "name": "Real-ESRGAN",
        "note": "4x AI upscale, then fitted -- needs realesrgan-ncnn-vulkan",
        "needs": "realesrgan",
    },
    {
        "key": "gigapixel",
        "name": "Topaz Photo AI",
        "note": "4x through Topaz -- needs Topaz Photo AI installed",
        "needs": "topaz",
    },
)


def available_qualities(tools: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """The qualities this machine can really deliver.

    A mode whose program is missing is still listed, marked unavailable and
    with the reason, so the screen can grey it out and say why rather than
    letting the admin pick something that will fail three minutes in.
    """
    tools = tools or {}
    offered = []
    for quality in QUALITIES:
        needs = quality["needs"]
        program = str(tools.get(needs, "") or "").strip() if needs else ""
        available = True
        reason = ""
        if needs:
            available = bool(program) and Path(program).is_file()
            if not program:
                reason = f"{PROGRAM_NAMES.get(needs, needs)} was not found on this machine"
            elif not available:
                reason = f"Not found at {program}"
        offered.append({**quality, "available": available, "reason": reason})
    return offered


# ---------------------------------------------------------------- the ladder


def scale_basic(image: Image.Image, width: int, height: int) -> Image.Image:
    return image.resize((width, height), Image.LANCZOS)


def scale_step(image: Image.Image, width: int, height: int) -> Image.Image:
    """Grow in 1.5x steps: a single huge jump smears fine detail."""
    current = image.copy()
    current_width, current_height = current.size
    while current_width < width or current_height < height:
        next_width = min(math.ceil(current_width * 1.5), width)
        next_height = min(math.ceil(current_height * 1.5), height)
        current = current.resize((next_width, next_height), Image.LANCZOS)
        current_width, current_height = next_width, next_height
    if current.size != (width, height):
        current = current.resize((width, height), Image.LANCZOS)
    return current


def apply_unsharp(image: Image.Image, amount: float = 0.5, radius: float = 1.0) -> Image.Image:
    return image.filter(ImageFilter.UnsharpMask(radius=radius, percent=int(amount * 100), threshold=0))


def scale_bicubic(image: Image.Image, width: int, height: int) -> Image.Image:
    source_width, source_height = image.size
    step_width = max(source_width, round(width * 2 / 3))
    step_height = max(source_height, round(height * 2 / 3))
    source = scale_step(image, step_width, step_height) if (
        step_width > source_width or step_height > source_height
    ) else image
    return source.resize((width, height), Image.BICUBIC)


def _run_external(program: str, arguments: list[str], label: str) -> None:
    if not program or not Path(program).is_file():
        raise PrintExportError(f"{label} is not installed at: {program or '(no path set)'}")
    result = subprocess.run([program, *arguments], capture_output=True, text=True)
    if result.returncode != 0:
        raise PrintExportError(f"{label} failed: {(result.stderr or result.stdout or '').strip()[:400]}")


def scale_realesrgan(image: Image.Image, width: int, height: int, program: str) -> Image.Image:
    """4x through Real-ESRGAN, then fitted to the exact size."""
    with tempfile.TemporaryDirectory() as scratch:
        source = Path(scratch) / "input.png"
        result = Path(scratch) / "output.png"
        image.convert("RGB").save(source, "PNG")
        arguments = ["-i", str(source), "-o", str(result), "-n", "realesrgan-x4plus", "-j", "1:1:1"]
        # The binary looks for its weights beside itself; saying so explicitly
        # means it still works when it is started from another directory.
        models = Path(program).parent / "models"
        if models.is_dir():
            arguments += ["-m", str(models)]
        _run_external(program, arguments, "Real-ESRGAN")
        if not result.is_file():
            raise PrintExportError("Real-ESRGAN produced no output")
        with Image.open(result) as upscaled:
            upscaled.load()
            output = upscaled.copy()
    if output.size != (width, height):
        output = output.resize((width, height), Image.LANCZOS)
    return output


def scale_topaz(image: Image.Image, width: int, height: int, program: str) -> Image.Image:
    with tempfile.TemporaryDirectory() as scratch:
        source = Path(scratch) / "input.png"
        out_dir = Path(scratch) / "out"
        out_dir.mkdir()
        image.convert("RGB").save(source, "PNG")
        _run_external(
            program,
            [str(source), "--output", str(out_dir), "--upscale", "scale=4", "--overwrite"],
            "Topaz Photo AI",
        )
        produced = sorted(out_dir.glob("*"))
        if not produced:
            raise PrintExportError("Topaz Photo AI produced no output")
        with Image.open(produced[0]) as upscaled:
            upscaled.load()
            output = upscaled.copy()
    if output.size != (width, height):
        output = output.resize((width, height), Image.LANCZOS)
    return output


def scale(image: Image.Image, width: int, height: int, quality: str, tools: dict[str, str] | None = None) -> Image.Image:
    tools = tools or {}
    if quality == "basic":
        return scale_basic(image, width, height)
    if quality == "step":
        return scale_step(image, width, height)
    if quality == "step-unsharp":
        return apply_unsharp(scale_step(image, width, height), 0.5, 1.0)
    if quality == "ai":
        return scale_realesrgan(image, width, height, str(tools.get("realesrgan", "")))
    if quality == "gigapixel":
        return scale_topaz(image, width, height, str(tools.get("topaz", "")))
    return apply_unsharp(scale_bicubic(image, width, height), 0.4, 0.8)


# ------------------------------------------------------------------ the file


def target_size(ratio: dict[str, Any], artwork: Image.Image) -> tuple[int, int]:
    """The canvas for one ratio, turned to match the artwork.

    The canvas follows the artwork's orientation rather than flipping whatever
    was stored: most ratios are kept portrait, but a panoramic is kept on its
    side, and blindly swapping would stand a 3:1 print upright. A square
    artwork has no orientation to follow, so the stored one is kept.
    """
    width, height = int(ratio["width"]), int(ratio["height"])
    if width == height or artwork.width == artwork.height:
        return width, height
    longer, shorter = max(width, height), min(width, height)
    return (longer, shorter) if artwork.width > artwork.height else (shorter, longer)


def flatten_artwork(image: Image.Image, background: tuple[int, int, int] = (255, 255, 255)) -> Image.Image:
    """An uploaded artwork as opaque pixels, transparency laid on white.

    A print file is a JPEG, so it has a background whatever happens; the only
    question is which one. Dropping the alpha channel the plain way leaves the
    colour that sat underneath it -- black, for the cut-out PNGs a shop sends --
    so the transparent areas are composited onto white deliberately instead.
    """
    if image.mode not in ("RGBA", "LA", "PA") and "transparency" not in image.info:
        return image.convert("RGB")
    source = image.convert("RGBA")
    canvas = Image.new("RGBA", source.size, (*background, 255))
    canvas.alpha_composite(source)
    return canvas.convert("RGB")


def has_transparency(image: Image.Image) -> bool:
    """Whether the upload actually carried see-through pixels."""
    if image.mode in ("RGBA", "LA", "PA"):
        alpha = image.convert("RGBA").getchannel("A")
        return alpha.getextrema()[0] < 255
    return "transparency" in image.info


def center_crop_to_ratio(image: Image.Image, width: int, height: int) -> Image.Image:
    """Trim the source to the target shape, before any resizing.

    Only the fill modes reach this: it is what makes the blurred backdrop the
    right shape, and what the expert mode does to the artwork itself.
    """
    source_ratio = image.width / image.height
    target_ratio = width / height
    if source_ratio > target_ratio:
        kept = round(image.height * target_ratio)
        left = (image.width - kept) // 2
        return image.crop((left, 0, left + kept, image.height))
    kept = round(image.width / target_ratio)
    top = (image.height - kept) // 2
    return image.crop((0, top, image.width, top + kept))


def blurred_backdrop(artwork: Image.Image, width: int, height: int) -> Image.Image:
    """A soft full-bleed background made from the artwork itself.

    The backdrop is cropped and blurred; the artwork laid over it is not. That
    is what makes this a safe mode -- frames, borders and text in the original
    survive, and the file still fills the canvas edge to edge.

    The scaling here is deliberately plain rather than following the chosen
    quality: a Gaussian blur of 80px erases any difference an AI upscaler could
    make, and running one over a second full-size canvas would double the cost
    of an export for a result nobody can see.
    """
    backdrop = scale_bicubic(center_crop_to_ratio(artwork, width, height), width, height)
    backdrop = backdrop.convert("RGB").filter(
        ImageFilter.GaussianBlur(radius=max(18, round(max(width, height) / 90)))
    )
    # A white wash keeps it quiet behind the artwork rather than busy.
    wash = Image.new("RGBA", (width, height), (255, 255, 255, 70))
    composed = backdrop.convert("RGBA")
    composed.alpha_composite(wash)
    return composed.convert("RGB")


def render_print_file(
    artwork: Image.Image,
    ratio: dict[str, Any],
    *,
    quality: str = "bicubic",
    mode: str = DEFAULT_MODE,
    tools: dict[str, str] | None = None,
    background: tuple[int, int, int] = (255, 255, 255),
) -> Image.Image:
    """One print file, in the Etsy output mode the shop chose.

    ``safe_fit``  the whole artwork, centred, white margins where the shapes
                  differ. The safe default: a border trimmed off a print is a
                  refund.
    ``safe_fill`` the whole artwork again, over a blurred extension of itself,
                  so the file fills the canvas without plain margins.
    ``fill_crop`` fills the canvas by cutting the overflow. The expert mode --
                  it is the only one that loses part of the artwork.
    """
    if mode not in MODE_KEYS:
        raise PrintExportError(f"Unknown output mode: {mode}")
    width, height = target_size(ratio, artwork)
    if width < 1 or height < 1:
        raise PrintExportError(f"Ratio {ratio.get('key')} has no usable size")

    if mode == "fill_crop":
        cropped = center_crop_to_ratio(artwork, width, height)
        return scale(cropped, width, height, quality, tools).convert("RGB")

    fit = min(width / artwork.width, height / artwork.height)
    fitted = scale(
        artwork,
        max(1, round(artwork.width * fit)),
        max(1, round(artwork.height * fit)),
        quality,
        tools,
    ).convert("RGB")

    canvas = (
        blurred_backdrop(artwork, width, height)
        if mode == "safe_fill"
        else Image.new("RGB", (width, height), background)
    )
    canvas.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2))
    return canvas


def print_file_name(ratio: dict[str, Any], artwork: Image.Image, extension: str = "jpg") -> str:
    """What the buyer sees in their download, and what it is for."""
    key = str(ratio.get("key", "ratio")).replace(":", "x").replace(" ", "-").lower()
    largest = str(ratio.get("sizes", "")).split(",")[-1].strip().replace(" ", "")
    # What the file actually is, not what the ratio was stored as.
    width, height = target_size(ratio, artwork)
    landscape = width > height
    parts = [f"{key}_ratio"]
    if largest:
        parts.append(f"{largest}_inch")
    if landscape:
        parts.append("landscape")
    return f"{'_'.join(parts)}.{extension}"


def printing_guide(ratios: list[dict[str, Any]], mode: str = DEFAULT_MODE) -> str:
    """The note that ships with the files, so the buyer knows which to print."""
    told = {
        "safe_fit": "prepared in safe-fit mode, so nothing has been cropped from it.",
        "safe_fill": "complete in every file -- the soft border around it is an",
        "fill_crop": "filled to the edge of each file, which trims the overflow.",
    }[mode if mode in MODE_KEYS else DEFAULT_MODE]
    lines = [
        "Thank you for your purchase!",
        "",
        "This package contains high-resolution files in several aspect ratios.",
        "Choose the file that matches your frame before printing. The artwork is",
        told,
    ]
    if mode == "safe_fill":
        lines.append("extension of the artwork itself, not part of the image.")
    lines.append("")
    for ratio in ratios:
        sizes = str(ratio.get("sizes", "")).strip()
        if not sizes:
            continue
        lines.append(f"{ratio.get('name') or ratio.get('key')}:")
        lines.append(sizes)
        lines.append("")
    lines.append("For the sharpest result, print at 300 DPI on matte or fine art paper.")
    return "\n".join(lines)


def realesrgan_ready(program: str) -> bool:
    return bool(program) and (Path(program).is_file() or bool(shutil.which(program)))
