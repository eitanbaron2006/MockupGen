import json
import logging
from pathlib import Path
from typing import Any

from PIL import Image

from services.detection_service import DetectionError, DetectionProposal, validate_proposal
from services.frame_refinement_service import refine_artwork_area, refine_perspective_corners


PROMPT = """Find the exact inner artwork replacement area in this product mockup.
Detect the exact 4 inner corners of the frame opening (excluding wood border/mat/shadows) in clockwise order starting from the top-left corner:
1. Top-Left corner [x, y]
2. Top-Right corner [x, y]
3. Bottom-Right corner [x, y]
4. Bottom-Left corner [x, y]
Return these 4 corners as an array of objects under the 'corners' key, with coordinates normalized to the 0-1000 format (x represents horizontal percentage, y represents vertical percentage).
A gray dashed placeholder rectangle (often around words like YOUR ARTWORK HERE or ART HERE) is the strongest signal.
Even if the frame is a flat, non-rotated 2D rectangle, you MUST return the exact 4 corners under the 'corners' key. Do NOT return a 2D bounding box (box_2d).
Ignore overlapping decorations and shadows. Do not infer shape from the words themselves."""


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
        search_radius: int = 20,
        client: Any | None = None,
    ):
        if not project_id:
            raise DetectionError("Vertex Project ID is required")
        self.project_id = project_id
        self.location = location or "global"
        self.model = model or "gemini-2.5-flash"
        self.media_resolution = media_resolution or "high"
        self.refine = refine
        self.search_radius = search_radius
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
                    "box_2d": {"type": "ARRAY", "items": {"type": "INTEGER"}},
                    "label": {"type": "STRING"},
                },
                "required": ["label"],
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
            raw_artwork_area = None
            
            logger = logging.getLogger("vertex_detection")
            logger.info("=== VERTEX DETECTION DEBUG ===")
            logger.info("Image dimensions: %dx%d", width, height)
            logger.info("Raw AI payload box_data: %s", json.dumps(box_data, default=str))
            
            if "box_2d" in box_data and box_data["box_2d"] and len(box_data["box_2d"]) == 4:
                box = box_data["box_2d"]
                raw_area = {
                    "x": round(int(box[1]) * width / 1000),
                    "y": round(int(box[0]) * height / 1000),
                    "width": round((int(box[3]) - int(box[1])) * width / 1000),
                    "height": round((int(box[2]) - int(box[0])) * height / 1000),
                }
                raw_artwork_area = raw_area
                if self.refine:
                    candidate_area = refine_artwork_area(background_path, raw_area)
                    proposal_area = _safe_refinement(raw_area, candidate_area) or raw_area
                    refinement_rejected = proposal_area == raw_area and candidate_area != raw_area
                else:
                    proposal_area = raw_area
                refined = proposal_area != raw_area
                

            elif "corners" in box_data and box_data["corners"] and len(box_data["corners"]) == 4:
                corners = box_data["corners"]
                logger.info("Raw AI corners (0-1000 normalized): %s", json.dumps(corners, default=str))
                normalized_corners = []
                for i, p in enumerate(corners):
                    px = round(int(p["x"]) * width / 1000)
                    py = round(int(p["y"]) * height / 1000)
                    logger.info("  Corner %d: AI(%s,%s) -> pixel(%d,%d)", i, p["x"], p["y"], px, py)
                    normalized_corners.append({"x": px, "y": py})
                
                # Sort normalized corners in clockwise order starting from top-left to avoid any model ordering issues
                from services.detection_service import sort_clockwise
                normalized_corners = sort_clockwise(normalized_corners)
                
                logger.info("Normalized pixel corners: %s", json.dumps(normalized_corners))
                logger.info("Refinement enabled: %s", self.refine)
                
                # Capture raw corners before edge refinement
                import copy
                raw_corners = copy.deepcopy(normalized_corners)
                xs_raw = [p["x"] for p in raw_corners]
                ys_raw = [p["y"] for p in raw_corners]
                raw_artwork_area = {
                    "x": min(xs_raw),
                    "y": min(ys_raw),
                    "width": max(xs_raw) - min(xs_raw),
                    "height": max(ys_raw) - min(ys_raw),
                    "corners": raw_corners
                }
                
                # Apply local edge refinement to all 4 corners!
                if self.refine:
                    refined_corners = refine_perspective_corners(background_path, normalized_corners, search_radius=self.search_radius)
                    refined = refined_corners != normalized_corners
                    logger.info("Refined corners: %s", json.dumps(refined_corners))
                    logger.info("Refinement changed corners: %s", refined)
                    for i, (orig, ref) in enumerate(zip(normalized_corners, refined_corners)):
                        dx = ref["x"] - orig["x"]
                        dy = ref["y"] - orig["y"]
                        if dx or dy:
                            logger.info("  Corner %d shifted: dx=%d, dy=%d", i, dx, dy)
                    normalized_corners = refined_corners
                else:
                    refined = False
                
                xs = [p["x"] for p in normalized_corners]
                xs_sorted = sorted(xs)
                ys = [p["y"] for p in normalized_corners]
                ys_sorted = sorted(ys)
                min_x, max_x = xs_sorted[0], xs_sorted[-1]
                min_y, max_y = ys_sorted[0], ys_sorted[-1]
                proposal_area = {
                    "x": min_x,
                    "y": min_y,
                    "width": max_x - min_x,
                    "height": max_y - min_y,
                    "corners": normalized_corners
                }
                logger.info("Final proposal_area: x=%d y=%d w=%d h=%d corners=%s",
                    proposal_area["x"], proposal_area["y"],
                    proposal_area["width"], proposal_area["height"],
                    json.dumps(proposal_area["corners"]))
            else:
                raise DetectionError("Vertex did not return perspective corners or a 2D bounding box")

            reason = str(box_data.get("label", "inner artwork area"))
            if "corners" in box_data:
                reason = f"{reason}; custom 3D perspective corners detected"
                if refined:
                    reason = f"{reason} (snapped to visible edges)"
            elif refined:
                reason = f"{reason}; boundary refinement snapped the proposal to visible edges"
            elif refinement_rejected:
                reason = f"{reason}; boundary refinement ignored because it distorted the AI box"

            proposal_payload = {
                "artwork_area": proposal_area,
                "confidence": 0.95 if "corners" in box_data else (0.9 if refined else 0.75),
                "reason": reason,
                "raw_artwork_area": raw_artwork_area,
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
