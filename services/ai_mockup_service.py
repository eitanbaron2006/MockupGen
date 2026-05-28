from abc import ABC, abstractmethod
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4
from PIL import Image

from services.simple_mockup_service import RenderResult, load_manifest


class AIMockupProvider(ABC):
    @abstractmethod
    def render(self, *, template_id: str, artwork_path: Path) -> Path:
        """Render a mockup and return the generated image path."""


class AIMockupService:
    def __init__(self, provider: AIMockupProvider | None = None) -> None:
        self.provider = provider

    def render(self, *, template_id: str, artwork_path: Path) -> Path:
        if self.provider is None:
            raise NotImplementedError(
                "AI rendering is not implemented yet: configure an AI mockup provider."
            )
        return self.provider.render(template_id=template_id, artwork_path=artwork_path)


def render_ai_mockup(
    *,
    template_id: str,
    artwork_path: Path,
    templates_folder: Path,
    output_folder: Path,
    project_id: str,
    location: str = "global",
    model: str = "gemini-3.1-flash-image",
) -> RenderResult:
    # 1. Load the mockup template details to locate the background image
    template_folder, manifest = load_manifest(templates_folder, template_id)
    background_filename = manifest.get("background", "background.png")
    background_path = template_folder / background_filename

    if not background_path.is_file():
        raise FileNotFoundError(f"Mockup background image not found: {background_path}")

    # 2. Read bytes of background and artwork
    background_bytes = background_path.read_bytes()
    with Image.open(background_path) as bg_img:
        bg_format = bg_img.format or "PNG"
        bg_mime = Image.MIME.get(bg_format, "image/png")

    artwork_bytes = artwork_path.read_bytes()
    with Image.open(artwork_path) as art_img:
        art_format = art_img.format or "PNG"
        art_mime = Image.MIME.get(art_format, "image/png")

    # 3. Call Vertex AI using google-genai
    try:
        from google import genai
        from google.genai import types
    except ImportError as error:
        raise RuntimeError("google-genai is not installed") from error

    try:
        if model.startswith("imagen-"):
            raise RuntimeError(
                f"Imagen models (like {model}) are pure image generation/editing models that do not support multimodal image-to-image merging. "
                "Please use Gemini models (such as gemini-3.1-flash-image or gemini-2.5-flash-image) which are designed to accept multiple input images "
                "and merge them with correct perspective and shadows."
            )

        client = genai.Client(
            vertexai=True,
            project=project_id,
            location=location,
            http_options=types.HttpOptions(api_version="v1"),
        )
        
        prompt = (
            "Merge the second image (artwork) exactly into the frame area of the first image (mockup background). "
            "Ensure the perspective is correct, shadows and highlights are blended naturally, and all wood/metal borders "
            "remain visible and crisp."
        )

        response = client.models.generate_content(
            model=model,
            contents=[
                types.Part.from_bytes(data=background_bytes, mime_type=bg_mime),
                types.Part.from_bytes(data=artwork_bytes, mime_type=art_mime),
                prompt
            ]
        )

        # 4. Extract generated image from the response parts (select the last inline image part)
        generated_image_bytes = None
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                    generated_image_bytes = part.inline_data.data

        if not generated_image_bytes:
            raise RuntimeError("Vertex AI did not return a generated image in the response.")

    except Exception as error:
        raise RuntimeError(f"Vertex AI mockup rendering failed: {error}") from error

    # 5. Save the generated image to the outputs folder
    output_folder.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_name = f"mockup_ai_{timestamp}_{uuid4().hex[:12]}.png"
    output_path = output_folder / output_name
    output_path.write_bytes(generated_image_bytes)

    # 6. Read dimensions of saved image and return RenderResult
    with Image.open(output_path) as out_img:
        width, height = out_img.size

    return RenderResult(
        mode="ai",
        template_id=template_id,
        output_url=f"/outputs/{output_name}",
        width=width,
        height=height,
    )
