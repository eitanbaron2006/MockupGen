import json
from pathlib import Path
from typing import Any

from PIL import Image

from services.detection_service import DetectionError, DetectionProposal, validate_proposal
from services.frame_refinement_service import refine_artwork_area


PROMPT = """Return one bounding box for the exact inner artwork replacement area in this
product mockup. A gray dashed placeholder rectangle (often around words such as YOUR
ARTWORK HERE) is the strongest signal and must take precedence over an outer wood frame,
mat border, or printed ratio text. If a visible dashed or solid inner placeholder
rectangle exists, follow its four visible boundary lines. Ignore decorations and shadows.
Do not infer shape from words in the image.
Return the bounding box in the documented normalized 0-1000 format
[y_min, x_min, y_max, x_max]."""


def _safe_refinement(
    raw_area: dict[str, int], refined_area: dict[str, int]
) -> dict[str, int] | None:
    width_ratio = refined_area["width"] / raw_area["width"]
    height_ratio = refined_area["height"] / raw_area["height"]
    area_ratio = (
        refined_area["width"] * refined_area["height"] / (raw_area["width"] * raw_area["height"])
    )
    if not (0.7 <= width_ratio <= 1.3 and 0.7 <= height_ratio <= 1.3):
        return None
    if not 0.55 <= area_ratio <= 1.45:
        return None
    return refined_area


class VertexDetectionProvider:
    def __init__(
        self,
        *,
        project_id: str,
        location: str = "global",
        model: str = "gemini-2.5-flash",
        media_resolution: str = "high",
        refine: bool = True,
        client: Any | None = None,
    ):
        if not project_id:
            raise DetectionError("Vertex Project ID is required")
        self.project_id = project_id
        self.location = location or "global"
        self.model = model or "gemini-2.5-flash"
        self.media_resolution = media_resolution or "high"
        self.refine = refine
        self.client = client or self._create_client()

    def _create_client(self):
        try:
            from google import genai
            from google.genai import types
        except ImportError as error:
            raise DetectionError("google-genai is not installed") from error
        return genai.Client(
            vertexai=True,
            project=self.project_id,
            location=self.location,
            http_options=types.HttpOptions(api_version="v1"),
        )

    def detect(self, background_path: Path) -> DetectionProposal:
        try:
            from google.genai import types
        except ImportError as error:
            raise DetectionError("google-genai is not installed") from error
        with Image.open(background_path) as image:
            width, height = image.size
            mime_type = Image.MIME.get(image.format, "image/png")
        image_data = background_path.read_bytes()
        schema = {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "box_2d": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                    "label": {"type": "STRING"},
                },
                "required": ["box_2d", "label"],
            },
        }
        resolutions = {
            "low": types.MediaResolution.MEDIA_RESOLUTION_LOW,
            "medium": types.MediaResolution.MEDIA_RESOLUTION_MEDIUM,
            "high": types.MediaResolution.MEDIA_RESOLUTION_HIGH,
        }
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=[types.Part.from_bytes(data=image_data, mime_type=mime_type), PROMPT],
                config=types.GenerateContentConfig(
                    temperature=0,
                    media_resolution=resolutions.get(
                        self.media_resolution, types.MediaResolution.MEDIA_RESOLUTION_HIGH
                    ),
                    response_mime_type="application/json",
                    response_schema=schema,
                ),
            )
            payload = getattr(response, "parsed", None) or json.loads(response.text)
            if payload and hasattr(payload[0], "model_dump"):
                payload = [box.model_dump() for box in payload]
            box = payload[0]["box_2d"]
            if not isinstance(box, list) or len(box) != 4:
                raise DetectionError("Vertex did not return a bounding box")
            raw_area = {
                "x": round(int(box[1]) * width / 1000),
                "y": round(int(box[0]) * height / 1000),
                "width": round((int(box[3]) - int(box[1])) * width / 1000),
                "height": round((int(box[2]) - int(box[0])) * height / 1000),
            }
            refinement_rejected = False
            if self.refine:
                candidate_area = refine_artwork_area(background_path, raw_area)
                proposal_area = _safe_refinement(raw_area, candidate_area) or raw_area
                refinement_rejected = proposal_area == raw_area and candidate_area != raw_area
            else:
                proposal_area = raw_area
            refined = proposal_area != raw_area
            reason = str(payload[0].get("label", "inner artwork area"))
            if refined:
                reason = f"{reason}; boundary refinement snapped the proposal to visible edges"
            elif refinement_rejected:
                reason = f"{reason}; boundary refinement ignored because it distorted the AI box"
            proposal_payload = {
                "artwork_area": proposal_area,
                "confidence": 0.9 if refined else 0.75,
                "reason": reason,
            }
        except DetectionError:
            raise
        except Exception as error:
            raise DetectionError(f"Vertex detection failed: {error}") from error
        return validate_proposal(
            proposal_payload,
            image_width=width,
            image_height=height,
            provider="vertex+edges" if refined else "vertex",
        )
