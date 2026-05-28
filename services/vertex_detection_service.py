import json
from pathlib import Path
from typing import Any

from PIL import Image

from services.detection_service import DetectionError, DetectionProposal, validate_proposal
from services.frame_refinement_service import refine_artwork_area


PROMPT = """Find the exact inner artwork replacement area in this product mockup.
You MUST detect the exact 4 inner corners of the frame opening (where the printable paper poster/artwork belongs) in clockwise order starting from the top-left corner:
1. Top-Left corner [x, y]
2. Top-Right corner [x, y]
3. Bottom-Right corner [x, y]
4. Bottom-Left corner [x, y]

CRITICAL GUIDELINES FOR ABSOLUTE PRECISION:
- ALWAYS return these 4 corners as an array under the 'corners' key in the normalized format 0-1000 (x is horizontal percentage, y is vertical percentage).
- EXCLUDE the wood, metal, plastic, or plaster frame border entirely! The 4 corner points must sit exactly at the INNER corner boundary where the glass or poster meets the inner edge of the wood frame border.
- If there is a white matte board (passe-partout) framing the picture inside the wood frame, detect the inner border of that matte board (the actual artwork opening), NOT the outer wood frame!
- Ignore all glass reflections, overlapping shadows, plants, furniture, or other decorations.
- A gray dashed placeholder line (often with text like 'YOUR DESIGN HERE', 'YOUR ARTWORK HERE', or 'ART HERE') is the absolute strongest signal. Detect the exact corners of this dashed rectangle."""


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
                    "corners": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "x": {"type": "INTEGER"},
                                "y": {"type": "INTEGER"}
                            },
                            "required": ["x", "y"]
                        }
                    },
                    "label": {"type": "STRING"},
                },
                "required": ["corners", "label"],
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
            
            box_data = payload[0]
            refined = False
            refinement_rejected = False
            
            if "corners" in box_data and box_data["corners"] and len(box_data["corners"]) == 4:
                corners = box_data["corners"]
                normalized_corners = []
                for p in corners:
                    normalized_corners.append({
                        "x": round(int(p["x"]) * width / 1000),
                        "y": round(int(p["y"]) * height / 1000)
                    })
                xs = [p["x"] for p in normalized_corners]
                ys = [p["y"] for p in normalized_corners]
                min_x, max_x = min(xs), max(xs)
                min_y, max_y = min(ys), max(ys)
                proposal_area = {
                    "x": min_x,
                    "y": min_y,
                    "width": max_x - min_x,
                    "height": max_y - min_y,
                    "corners": normalized_corners
                }
            elif "box_2d" in box_data and box_data["box_2d"] and len(box_data["box_2d"]) == 4:
                box = box_data["box_2d"]
                raw_area = {
                    "x": round(int(box[1]) * width / 1000),
                    "y": round(int(box[0]) * height / 1000),
                    "width": round((int(box[3]) - int(box[1])) * width / 1000),
                    "height": round((int(box[2]) - int(box[0])) * height / 1000),
                }
                if self.refine:
                    candidate_area = refine_artwork_area(background_path, raw_area)
                    proposal_area = _safe_refinement(raw_area, candidate_area) or raw_area
                    refinement_rejected = proposal_area == raw_area and candidate_area != raw_area
                else:
                    proposal_area = raw_area
                refined = proposal_area != raw_area
            else:
                raise DetectionError("Vertex did not return perspective corners or a 2D bounding box")

            reason = str(box_data.get("label", "inner artwork area"))
            if refined:
                reason = f"{reason}; boundary refinement snapped the proposal to visible edges"
            elif refinement_rejected:
                reason = f"{reason}; boundary refinement ignored because it distorted the AI box"
            elif "corners" in box_data:
                reason = f"{reason}; custom 3D perspective corners detected"

            proposal_payload = {
                "artwork_area": proposal_area,
                "confidence": 0.95 if "corners" in box_data else (0.9 if refined else 0.75),
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
