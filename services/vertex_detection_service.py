import json
import logging
from pathlib import Path
from typing import Any

from PIL import Image

from services.detection_service import DetectionError, DetectionProposal, validate_proposal
from services.frame_refinement_service import refine_artwork_area, refine_perspective_corners


PROMPT = """Find ALL inner artwork replacement areas in this product mockup.
For EACH separate picture frame, poster opening, canvas, or artwork slot in the mockup (whether 1 single frame, a diptych of 2 frames, a triptych of 3 frames, or a multi-frame gallery wall):
Detect the exact 4 inner corners of the frame opening (excluding the wooden frame border, matting, or external cast shadows) in clockwise order starting from the top-left corner:
1. Top-Left corner [x, y]
2. Top-Right corner [x, y]
3. Bottom-Right corner [x, y]
4. Bottom-Left corner [x, y]
Return an array of objects under the 'corners' key, with coordinates normalized to the 0-1000 format (where x is horizontal percentage 0-1000, y is vertical percentage 0-1000).
A gray dashed placeholder rectangle (often containing text like YOUR ARTWORK HERE, ART HERE, or 1/2/3) is the strongest signal.
Even if the frame is a flat, non-rotated 2D rectangle, you MUST return the exact 4 corners under the 'corners' key. Do NOT return a 2D bounding box (box_2d).
Ignore overlapping decorations and shadows.
Return an entry in the array for EACH detected frame, ordered from left to right / top to bottom."""


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


_VERTEX_HEALTH_CACHE: dict[str, Any] = {"result": None, "timestamp": 0.0, "key": None}


def check_vertex_health(
    project_id: str | None,
    location: str = "global",
    model: str = "gemini-2.5-flash",
    timeout_seconds: float = 6.0,
    force: bool = False,
) -> dict[str, Any]:
    """Test if Vertex AI is configured, credentials are valid, and model endpoint is reachable."""
    global _VERTEX_HEALTH_CACHE
    import time

    cache_key = f"{project_id}:{location}:{model}"
    now = time.time()
    if not force and _VERTEX_HEALTH_CACHE["result"] is not None:
        if (
            _VERTEX_HEALTH_CACHE["key"] == cache_key
            and (now - _VERTEX_HEALTH_CACHE["timestamp"]) < 60.0
        ):
            return _VERTEX_HEALTH_CACHE["result"]

    if not project_id or not project_id.strip():
        result = {
            "available": False,
            "provider": "vertex",
            "error": "VERTEX_PROJECT_ID is not configured in settings.",
        }
        _VERTEX_HEALTH_CACHE = {"result": result, "timestamp": now, "key": cache_key}
        return result

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        result = {
            "available": False,
            "provider": "vertex",
            "error": "google-genai package is not installed.",
        }
        _VERTEX_HEALTH_CACHE = {"result": result, "timestamp": now, "key": cache_key}
        return result

    try:
        import httpx
        http_client = httpx.Client(timeout=timeout_seconds)
        client = genai.Client(
            vertexai=True,
            project=project_id.strip(),
            location=location or "global",
            http_options=types.HttpOptions(httpx_client=http_client),
        )
        # Fast lightweight ping
        res = client.models.generate_content(
            model=model or "gemini-2.5-flash",
            contents="ping",
        )
        result = {
            "available": True,
            "provider": "vertex",
            "model": model or "gemini-2.5-flash",
            "location": location or "global",
            "message": "Vertex AI is connected and operational",
        }
    except Exception as exc:
        err_msg = str(exc)
        if (
            "DefaultCredentialsError" in err_msg
            or "Could not automatically determine credentials" in err_msg
        ):
            err_msg = "Google Cloud Application Default Credentials (ADC) are not configured."
        elif "PermissionDenied" in err_msg or "403" in err_msg:
            err_msg = "Permission denied. Check Vertex AI API permissions for this project."
        elif "NotFound" in err_msg or "404" in err_msg:
            err_msg = f"Model '{model}' not found in location '{location}'."
        result = {
            "available": False,
            "provider": "vertex",
            "error": err_msg,
        }

    _VERTEX_HEALTH_CACHE = {"result": result, "timestamp": now, "key": cache_key}
    return result


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
            import httpx
            from google import genai
            from google.genai import types
        except ImportError as error:
            raise DetectionError("google-genai is not installed") from error
        http_client = httpx.Client(timeout=30.0)
        return genai.Client(
            vertexai=True,
            project=self.project_id,
            location=self.location,
            http_options=types.HttpOptions(httpx_client=http_client),
        )

    def build_green_frame_mask(
        self, background_path: Path, regions: list[dict[str, Any]] | None = None
    ) -> Image.Image:
        from PIL import ImageDraw

        with Image.open(background_path) as img:
            w, h = img.size
        mask = Image.new("L", (w, h), 0)
        draw = ImageDraw.Draw(mask)
        if not regions:
            proposal = self.detect(background_path)
            raw = proposal.raw_artwork_area or {}
            regions = raw.get("regions") or []
        for region in regions:
            corners = region.get("corners")
            if corners and len(corners) >= 3:
                pts = [(int(round(p["x"])), int(round(p["y"]))) for p in corners]
                draw.polygon(pts, fill=255)
            else:
                rx, ry = int(region["x"]), int(region["y"])
                rw, rh = int(region["width"]), int(region["height"])
                draw.rectangle([rx, ry, rx + rw, ry + rh], fill=255)
        return mask

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
            
            if not payload or not isinstance(payload, list):
                raise DetectionError("Vertex AI did not return any detected frames")

            logger = logging.getLogger("vertex_detection")
            logger.info("=== VERTEX DETECTION DEBUG ===")
            logger.info("Image dimensions: %dx%d, Total boxes detected: %d", width, height, len(payload))

            from services.detection_service import sort_clockwise

            regions = []
            any_refined = False
            refinement_rejected = False

            for idx, box_data in enumerate(payload):
                if not isinstance(box_data, dict):
                    continue

                corners = box_data.get("corners")
                box_2d = box_data.get("box_2d")

                if corners and len(corners) == 4:
                    normalized_corners = []
                    for p in corners:
                        px = round(int(p["x"]) * width / 1000)
                        py = round(int(p["y"]) * height / 1000)
                        normalized_corners.append({"x": px, "y": py})

                    normalized_corners = sort_clockwise(normalized_corners)

                    if self.refine:
                        refined_corners = refine_perspective_corners(
                            background_path, normalized_corners, search_radius=self.search_radius
                        )
                        if refined_corners != normalized_corners:
                            any_refined = True
                        final_corners = refined_corners
                    else:
                        final_corners = normalized_corners

                    xs = [p["x"] for p in final_corners]
                    ys = [p["y"] for p in final_corners]
                    min_x, max_x = min(xs), max(xs)
                    min_y, max_y = min(ys), max(ys)

                    regions.append({
                        "x": min_x,
                        "y": min_y,
                        "width": max_x - min_x,
                        "height": max_y - min_y,
                        "area": (max_x - min_x) * (max_y - min_y),
                        "corners": final_corners,
                        "label": box_data.get("label", f"Frame {idx + 1}"),
                        "has_perspective": True,
                    })

                elif box_2d and len(box_2d) == 4:
                    raw_area = {
                        "x": round(int(box_2d[1]) * width / 1000),
                        "y": round(int(box_2d[0]) * height / 1000),
                        "width": round((int(box_2d[3]) - int(box_2d[1])) * width / 1000),
                        "height": round((int(box_2d[2]) - int(box_2d[0])) * height / 1000),
                    }
                    if self.refine:
                        candidate_area = refine_artwork_area(background_path, raw_area)
                        proposal_area = _safe_refinement(raw_area, candidate_area) or raw_area
                        if proposal_area != raw_area:
                            any_refined = True
                        elif candidate_area != raw_area:
                            refinement_rejected = True
                    else:
                        proposal_area = raw_area

                    corners = [
                        {"x": proposal_area["x"], "y": proposal_area["y"]},
                        {"x": proposal_area["x"] + proposal_area["width"], "y": proposal_area["y"]},
                        {"x": proposal_area["x"] + proposal_area["width"], "y": proposal_area["y"] + proposal_area["height"]},
                        {"x": proposal_area["x"], "y": proposal_area["y"] + proposal_area["height"]},
                    ]
                    regions.append({
                        "x": proposal_area["x"],
                        "y": proposal_area["y"],
                        "width": proposal_area["width"],
                        "height": proposal_area["height"],
                        "area": proposal_area["width"] * proposal_area["height"],
                        "corners": corners,
                        "label": box_data.get("label", f"Frame {idx + 1}"),
                        "has_perspective": False,
                        "raw_box": raw_area,
                    })

            if not regions:
                raise DetectionError("Vertex did not return valid perspective corners or bounding boxes for any frames")

            # Canonical ordering (top-to-bottom, left-to-right)
            regions.sort(key=lambda r: (round(float(r["y"]) / 25), float(r["x"])))

            if len(regions) == 1:
                first = regions[0]
                has_perspective = first.get("has_perspective", True)
                if has_perspective:
                    proposal_area = {
                        "x": first["x"],
                        "y": first["y"],
                        "width": first["width"],
                        "height": first["height"],
                        "corners": first["corners"],
                    }
                    raw_artwork_area = {
                        "x": first["x"],
                        "y": first["y"],
                        "width": first["width"],
                        "height": first["height"],
                        "corners": first["corners"],
                        "regions": [
                            {
                                "x": first["x"],
                                "y": first["y"],
                                "width": first["width"],
                                "height": first["height"],
                                "area": first["area"],
                                "corners": first["corners"],
                            }
                        ],
                    }
                    reason = str(first.get("label", "inner artwork area")) + "; custom 3D perspective corners detected"
                    if any_refined:
                        reason += " (snapped to visible edges)"
                else:
                    proposal_area = {
                        "x": first["x"],
                        "y": first["y"],
                        "width": first["width"],
                        "height": first["height"],
                    }
                    raw_artwork_area = first.get("raw_box", proposal_area)
                    reason = str(first.get("label", "inner artwork area"))
                    if any_refined:
                        reason += "; boundary refinement snapped the proposal to visible edges"
                    elif refinement_rejected:
                        reason += "; boundary refinement ignored because it distorted the AI box"

                proposal_payload = {
                    "artwork_area": proposal_area,
                    "confidence": 0.95 if has_perspective else (0.9 if any_refined else 0.75),
                    "reason": reason,
                    "raw_artwork_area": raw_artwork_area,
                }
            else:
                all_xs = [p["x"] for r in regions for p in r["corners"]]
                all_ys = [p["y"] for r in regions for p in r["corners"]]
                min_all_x, max_all_x = min(all_xs), max(all_xs)
                min_all_y, max_all_y = min(all_ys), max(all_ys)

                clean_regions = []
                for r in regions:
                    clean_regions.append({
                        "x": r["x"],
                        "y": r["y"],
                        "width": r["width"],
                        "height": r["height"],
                        "area": r["area"],
                        "corners": r["corners"],
                    })

                first = regions[0]
                proposal_area = {
                    "x": min_all_x,
                    "y": min_all_y,
                    "width": max_all_x - min_all_x,
                    "height": max_all_y - min_all_y,
                    "corners": first["corners"],
                }
                raw_artwork_area = {
                    "mode": "green_frames_mockups",
                    "regions": clean_regions,
                    "original_corners": first["corners"],
                    "frame_count": len(clean_regions),
                }
                reason = f"Detected {len(clean_regions)} artwork frames in mockup"
                if any_refined:
                    reason += " (snapped to visible edges)"

                proposal_payload = {
                    "artwork_area": proposal_area,
                    "confidence": 0.95,
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
            provider="vertex+edges" if any_refined else "vertex",
        )

