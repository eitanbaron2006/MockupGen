from pathlib import Path

from PIL import Image

from services.catalog_service import orientation_for_size
from services.detection_service import DetectionProposal, validate_proposal
from services.frame_refinement_service import refine_artwork_area, refine_perspective_corners


class ClassicDetectionProvider:
    """Conservative offline fallback; review its proposed rectangle before activation."""

    def __init__(self, blur_size: int = 3, search_radius: int = 20):
        self.blur_size = blur_size
        self.search_radius = search_radius

    def detect(self, background_path: Path) -> DetectionProposal:
        with Image.open(background_path) as image:
            width, height = image.size
        orientation = orientation_for_size(width, height)
        if orientation == "portrait":
            area_width, area_height = int(width * 0.56), int(height * 0.62)
        elif orientation == "landscape":
            area_width, area_height = int(width * 0.62), int(height * 0.56)
        else:
            area_width = area_height = int(min(width, height) * 0.58)
        initial_area = {
            "x": (width - area_width) // 2,
            "y": (height - area_height) // 2,
            "width": area_width,
            "height": area_height,
        }
        refined_area = refine_artwork_area(background_path, initial_area, blur_size=self.blur_size)
        
        # Populate the 4 clockwise corner coordinates so the UI renders them immediately as perspective handles
        corners = [
            {"x": refined_area["x"], "y": refined_area["y"]},
            {"x": refined_area["x"] + refined_area["width"], "y": refined_area["y"]},
            {"x": refined_area["x"] + refined_area["width"], "y": refined_area["y"] + refined_area["height"]},
            {"x": refined_area["x"], "y": refined_area["y"] + refined_area["height"]}
        ]
        
        # Refine corners for perspective quads
        corners = refine_perspective_corners(
            background_path, 
            corners, 
            search_radius=self.search_radius, 
            blur_size=self.blur_size
        )
        
        artwork_area = {
            **refined_area,
            "corners": corners
        }
        
        refined = refined_area != initial_area
        return validate_proposal(
            {
                "artwork_area": artwork_area,
                "confidence": 0.7 if refined else 0.25,
                "reason": (
                    "Visible inner or dashed artwork boundary detected locally; manual review required."
                    if refined
                    else "Centered offline estimate; manual review required."
                ),
            },
            image_width=width,
            image_height=height,
            provider="classic",
        )
