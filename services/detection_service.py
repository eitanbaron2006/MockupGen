from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class DetectionError(ValueError):
    pass


@dataclass(frozen=True)
class DetectionProposal:
    artwork_area: dict[str, int]
    confidence: float | None
    reason: str
    provider: str


class DetectionProvider(Protocol):
    def detect(self, background_path: Path) -> DetectionProposal:
        ...


def validate_proposal(
    payload: dict[str, Any],
    *,
    image_width: int,
    image_height: int,
    provider: str,
) -> DetectionProposal:
    area = payload.get("artwork_area", payload)
    try:
        if "corners" in area:
            corners = area["corners"]
            if not isinstance(corners, list) or len(corners) != 4:
                raise DetectionError("Quadrilateral corners must contain exactly 4 points")
            normalized_corners = []
            for p in corners:
                normalized_corners.append({
                    "x": int(p["x"]),
                    "y": int(p["y"])
                })
            xs = [p["x"] for p in normalized_corners]
            ys = [p["y"] for p in normalized_corners]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            normalized = {
                "x": min_x,
                "y": min_y,
                "width": max_x - min_x,
                "height": max_y - min_y,
                "corners": normalized_corners
            }
        else:
            normalized = {
                "x": int(area["x"]),
                "y": int(area["y"]),
                "width": int(area["width"]),
                "height": int(area["height"]),
            }
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, DetectionError):
            raise
        raise DetectionError("Detector did not return a valid artwork area") from error
    if (
        normalized["x"] < 0
        or normalized["y"] < 0
        or normalized["width"] <= 0
        or normalized["height"] <= 0
        or normalized["x"] + normalized["width"] > image_width
        or normalized["y"] + normalized["height"] > image_height
    ):
        raise DetectionError("Detected artwork area is outside the background image")
    raw_confidence = payload.get("confidence")
    confidence = float(raw_confidence) if raw_confidence is not None else None
    if confidence is not None and not 0 <= confidence <= 1:
        raise DetectionError("Detector confidence must be between 0 and 1")
    return DetectionProposal(
        artwork_area=normalized,
        confidence=confidence,
        reason=str(payload.get("reason", "")),
        provider=provider,
    )


def build_provider(settings: dict[str, str], config: dict[str, Any]) -> DetectionProvider:
    selected = settings.get("DETECTION_PROVIDER", config.get("DETECTION_PROVIDER", "classic"))
    if selected == "vertex":
        from services.vertex_detection_service import VertexDetectionProvider

        return VertexDetectionProvider(
            project_id=settings.get("VERTEX_PROJECT_ID", config.get("VERTEX_PROJECT_ID", "")),
            location=settings.get("VERTEX_LOCATION", config.get("VERTEX_LOCATION", "global")),
            model=settings.get("VERTEX_MODEL", config.get("VERTEX_MODEL", "gemini-2.5-flash")),
            media_resolution=settings.get(
                "VERTEX_MEDIA_RESOLUTION", config.get("VERTEX_MEDIA_RESOLUTION", "high")
            ),
            refine=settings.get(
                "DETECTION_REFINEMENT", config.get("DETECTION_REFINEMENT", "hybrid")
            )
            != "ai_only",
        )
    if selected == "local":
        from services.local_detection_service import LocalDetectionProvider

        return LocalDetectionProvider(
            endpoint=settings.get("LOCAL_DETECTION_URL", config.get("LOCAL_DETECTION_URL", "")),
            model=settings.get(
                "LOCAL_DETECTION_MODEL", config.get("LOCAL_DETECTION_MODEL", "")
            ),
            api_key=config.get("LOCAL_DETECTION_API_KEY", ""),
        )
    from services.classic_detection_service import ClassicDetectionProvider

    return ClassicDetectionProvider()
