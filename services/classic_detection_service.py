import threading
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from services.catalog_service import orientation_for_size
from services.detection_service import DetectionError, DetectionProposal, validate_proposal
from services.frame_geometry_service import find_frames
from services.frame_refinement_service import refine_artwork_area, refine_perspective_corners
from services.green_frame_mockup_service import (
    GreenFrameSettings,
    detect_green_frames,
    green_detection_raw,
    green_mask_image,
)

# Pixels every detected opening is grown by, so artwork tucks a hair under the
# frame border instead of stopping short and letting a rim of mockup show. It
# is baked into the corners here so detection, the editor and the renderer all
# work from one quad and cannot drift apart.
_OPENING_BLEED = 3

_SAM_MODEL_INSTANCE = None
_SAM_MODEL_LOCK = threading.Lock()


def _corner_dicts(points, width: int, height: int) -> list[dict[str, int]]:
    """Clamp a quad to the canvas and express it as clockwise {x, y} dicts."""
    return [
        {
            "x": int(max(0, min(width - 1, round(float(point[0]))))),
            "y": int(max(0, min(height - 1, round(float(point[1]))))),
        }
        for point in points
    ]


def _inset_corners(
    corners: list[dict[str, int]], amount: float, width: int, height: int
) -> list[dict[str, int]]:
    """Pull each corner towards the centroid so artwork lands inside the border."""
    points = np.array([[c["x"], c["y"]] for c in corners], dtype="float64")
    centroid = points.mean(axis=0)
    moved = []
    for point in points:
        vector = centroid - point
        norm = float(np.linalg.norm(vector))
        moved.append(point + (vector / norm) * amount if norm > 0 else point)
    return _corner_dicts(moved, width, height)


def _corner_bounds(corners: list[dict[str, int]]) -> dict[str, int]:
    xs = [c["x"] for c in corners]
    ys = [c["y"] for c in corners]
    return {
        "x": min(xs),
        "y": min(ys),
        "width": max(1, max(xs) - min(xs)),
        "height": max(1, max(ys) - min(ys)),
    }


def _get_sam_model():
    global _SAM_MODEL_INSTANCE
    if _SAM_MODEL_INSTANCE is None:
        with _SAM_MODEL_LOCK:
            if _SAM_MODEL_INSTANCE is None:
                from ultralytics import SAM
                model_paths = [
                    Path(__file__).resolve().parents[1] / "models" / "sam2.1_l.pt",
                    Path("models/sam2.1_l.pt"),
                    Path("sam2.1_l.pt"),
                ]
                model_file = next((p for p in model_paths if p.is_file()), Path("sam2.1_l.pt"))
                _SAM_MODEL_INSTANCE = SAM(str(model_file))
    return _SAM_MODEL_INSTANCE


class ClassicDetectionProvider:
    """Conservative offline fallback using high-fidelity multi-layer geometric and SAM 2.1 boundary detection."""

    def __init__(
        self,
        blur_size: int = 3,
        search_radius: int = 20,
        default_mode: str = "auto",
        green_edge_expand: int = 1,
        green_tolerance: int = 130,
    ):
        self.blur_size = blur_size
        self.search_radius = search_radius
        self.default_mode = default_mode or "auto"
        self.green_edge_expand = max(0, int(green_edge_expand))
        # 442 is the whole colour space; at that setting every pixel is green
        # and detection finds one region the size of the canvas, so the useful
        # range stops well short of it.
        self.green_tolerance = max(10, min(442, int(green_tolerance)))

    def _green_raw_mask(self, img: np.ndarray) -> np.ndarray:
        state = detect_green_frames(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)), self._green_settings())
        return state.raw_mask.astype(np.uint8)

    def _expanded_green_mask(self, raw_mask: np.ndarray) -> np.ndarray:
        if self.green_edge_expand <= 0:
            return raw_mask
        kernel_size = self.green_edge_expand * 2 + 1
        kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
        return cv2.dilate(raw_mask, kernel, iterations=1)

    def build_green_frame_mask(self, background_path: Path) -> Image.Image:
        image = Image.open(background_path).convert("RGBA")
        state = detect_green_frames(image, self._green_settings())
        if not state.regions:
            raise DetectionError("No green frame mockup region could be detected.")
        return green_mask_image(state)

    def _green_settings(self) -> GreenFrameSettings:
        return GreenFrameSettings(
            edge_expand=self.green_edge_expand, min_area=2500, tolerance=self.green_tolerance
        )

    def _detect_green_frame(self, img: np.ndarray) -> tuple[np.ndarray | None, dict | None]:
        image = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)).convert("RGBA")
        state = detect_green_frames(image, self._green_settings())
        if not state.regions:
            return None, {"green_pixels": 0, "regions": []}
        raw = green_detection_raw(state, self.green_edge_expand)
        first = raw["regions"][0]["corners"]
        chosen_pts = np.array([[point["x"], point["y"]] for point in first], dtype="int32")
        return chosen_pts, raw

    def _geometric_regions(self, img: np.ndarray) -> tuple[list[dict], list[list[dict]]]:
        """Detect every artwork quad geometrically.

        Returns the region dictionaries (green-frame schema, so the shared
        multi-frame rendering path applies) plus the nesting border layers of
        the first frame, which drive the single-frame layer picker.
        """
        frames = find_frames(img)
        if not frames:
            return [], []

        width, height = img.shape[1], img.shape[0]
        regions = []
        for frame in frames:
            corners = _inset_corners(
                _corner_dicts(frame["corners"], width, height), -_OPENING_BLEED, width, height
            )
            regions.append(
                {
                    **_corner_bounds(corners),
                    "area": int(round(frame["area"])),
                    # The corners are the whole story: the renderer warps onto
                    # them and the editor draws on them. A second copy of the
                    # quad would go stale the moment a frame is dragged.
                    "corners": corners,
                }
            )
        layers = [_corner_dicts(layer, width, height) for layer in frames[0]["layers"]]
        return regions, layers

    def detect(self, background_path: Path, mode: str | None = None, point: dict = None) -> DetectionProposal:
        mode = mode or self.default_mode
        # Load background image via OpenCV
        img = cv2.imread(str(background_path))
        if img is None:
            raise FileNotFoundError(f"Could not open image: {background_path}")

        h, w, _ = img.shape

        chosen_pts = None
        is_geometric = False
        is_sam = False
        is_green_frame = False
        all_layers = []
        multi_regions = None

        if mode == "green_frames_mockups":
            chosen_pts, raw_artwork_area = self._detect_green_frame(img)
            if chosen_pts is None:
                raise DetectionError("No green frame mockup region could be detected.")
            is_green_frame = True

        # 1. Step 1 (geometry): every artwork quad in the mockup, not just one
        if chosen_pts is None and mode in ("auto", "geometry"):
            geometric_regions, all_layers = self._geometric_regions(img)

            if geometric_regions:
                if len(geometric_regions) > 1:
                    multi_regions = geometric_regions
                # The innermost layer of the first frame drives the single-frame
                # preview; the layer picker walks the rest.
                chosen_pts = np.array(
                    [[c["x"], c["y"]] for c in geometric_regions[0]["corners"]], dtype="int32"
                )
                is_geometric = True
            else:
                # Fallback to geometric gradient inner opening
                orientation = orientation_for_size(w, h)
                if orientation == "portrait":
                    area_width, area_height = int(w * 0.56), int(h * 0.62)
                elif orientation == "landscape":
                    area_width, area_height = int(w * 0.62), int(h * 0.56)
                else:
                    area_width = area_height = int(min(w, h) * 0.58)

                initial_area = {
                    "x": (w - area_width) // 2,
                    "y": (h - area_height) // 2,
                    "width": area_width,
                    "height": area_height,
                }
                refined_area = refine_artwork_area(background_path, initial_area, blur_size=self.blur_size)
                corners = [
                    {"x": max(0, min(w - 1, refined_area["x"])), "y": max(0, min(h - 1, refined_area["y"]))},
                    {"x": max(0, min(w - 1, refined_area["x"] + refined_area["width"])), "y": max(0, min(h - 1, refined_area["y"]))},
                    {"x": max(0, min(w - 1, refined_area["x"] + refined_area["width"])), "y": max(0, min(h - 1, refined_area["y"] + refined_area["height"]))},
                    {"x": max(0, min(w - 1, refined_area["x"])), "y": max(0, min(h - 1, refined_area["y"] + refined_area["height"]))}
                ]
                corners = refine_perspective_corners(
                    background_path,
                    corners,
                    search_radius=self.search_radius,
                    blur_size=self.blur_size
                )
                clamped_corners = [{"x": max(0, min(w - 1, c["x"])), "y": max(0, min(h - 1, c["y"]))} for c in corners]
                all_layers.append(clamped_corners)
                chosen_pts = np.array([[c["x"], c["y"]] for c in clamped_corners], dtype="int32")
                is_geometric = True

        # 2. Step 2 (sam_center) or Step 3 (sam_point)
        # Run ONLY if SAM mode is explicitly requested
        if chosen_pts is None and (mode in ("sam_center", "sam_point")):
            try:
                sam_model = _get_sam_model()
                
                if mode == "sam_point" and point:
                    pts_list = [[int(point["x"]), int(point["y"])]]
                else:
                    # Positive point prompt at the exact center of the image
                    pts_list = [[w // 2, h // 2]]
                
                results = sam_model.predict(
                    source=str(background_path),
                    points=pts_list,
                    labels=[1] * len(pts_list),
                    device="cpu",
                    verbose=False
                )
                if len(results) > 0 and results[0].masks is not None:
                    mask = results[0].masks.data[0].cpu().numpy().astype(np.uint8) * 255
                    if mask.shape[0] != h or mask.shape[1] != w:
                        mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
                    sam_contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    if sam_contours:
                        largest_contour = max(sam_contours, key=cv2.contourArea)
                        pts = np.zeros((4, 2), dtype="int32")
                        s = largest_contour.sum(axis=2)
                        pts[0] = largest_contour[np.argmin(s)]
                        pts[2] = largest_contour[np.argmax(s)]
                        diff = np.diff(largest_contour, axis=2)
                        pts[1] = largest_contour[np.argmin(diff)]
                        pts[3] = largest_contour[np.argmax(diff)]
                        
                        # Sort corners clockwise: TL, TR, BR, BL
                        sorted_pts = np.zeros((4, 2), dtype="int32")
                        s = pts.sum(axis=1)
                        sorted_pts[0] = pts[np.argmin(s)]  # TL
                        sorted_pts[2] = pts[np.argmax(s)]  # BR
                        diff = np.diff(pts, axis=1)
                        sorted_pts[1] = pts[np.argmin(diff)]  # TR
                        sorted_pts[3] = pts[np.argmax(diff)]  # BL
                        
                        chosen_pts = sorted_pts
                        is_sam = True
            except Exception as e:
                import logging
                logging.getLogger("classic_detection").warning(f"Local SAM 2.1 model prediction failed: {e}")

        # If explicitly requesting sam/green modes and it failed to find corners, raise DetectionError
        if mode in ("sam_center", "sam_point", "green_frames_mockups") and chosen_pts is None:
            raise DetectionError(f"No boundary corners could be resolved using {mode} mode.")

        if chosen_pts is not None:
            if is_green_frame:
                final_corners = _corner_dicts(chosen_pts, w, h)
            elif is_geometric:
                # chosen_pts already carries the bleed, having come from the
                # regions themselves, so one frame and many render identically.
                final_corners = _corner_dicts(chosen_pts, w, h)
                # The wizard approves the innermost layer, so it has to be the
                # very quad proposed here. Leaving the raw cluster quad there
                # would silently approve a frame a few pixels off the opening.
                if all_layers:
                    all_layers[-1] = final_corners
                else:
                    all_layers = [final_corners]
                raw_artwork_area = {
                    "mode": "geometry",
                    "layers": all_layers,
                    "original_corners": final_corners,
                }
                if multi_regions:
                    raw_artwork_area["regions"] = multi_regions
            else:
                # A SAM outline traces the artwork loosely, so it keeps the 3px
                # inset that guarantees placement inside the frame borders.
                final_corners = _inset_corners(_corner_dicts(chosen_pts, w, h), 3, w, h)
                raw_artwork_area = {
                    "mode": "sam",
                    "original_corners": _corner_dicts(chosen_pts, w, h),
                }

            refined = True
            # With several frames the regions carry the geometry; artwork_area
            # stays on the first one so orientation reflects a single artwork
            # rather than the meaningless box around the whole set.
            artwork_area = {**_corner_bounds(final_corners), "corners": final_corners}
        else:
            # 3. Fallback to legacy centered offline estimation if both failed
            refined = False
            orientation = orientation_for_size(w, h)
            if orientation == "portrait":
                area_width, area_height = int(w * 0.56), int(h * 0.62)
            elif orientation == "landscape":
                area_width, area_height = int(w * 0.62), int(h * 0.56)
            else:
                area_width = area_height = int(min(w, h) * 0.58)

            initial_area = {
                "x": (w - area_width) // 2,
                "y": (h - area_height) // 2,
                "width": area_width,
                "height": area_height,
            }
            refined_area = refine_artwork_area(background_path, initial_area, blur_size=self.blur_size)

            corners = [
                {"x": refined_area["x"], "y": refined_area["y"]},
                {"x": refined_area["x"] + refined_area["width"], "y": refined_area["y"]},
                {"x": refined_area["x"] + refined_area["width"], "y": refined_area["y"] + refined_area["height"]},
                {"x": refined_area["x"], "y": refined_area["y"] + refined_area["height"]}
            ]
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
            raw_artwork_area = None

        return validate_proposal(
            {
                "artwork_area": artwork_area,
                "confidence": 0.92 if is_green_frame else (0.90 if is_sam else (0.85 if is_geometric else (0.70 if refined else 0.25))),
                "reason": (
                    "Green frame mockup region detected from color mask with perspective corners."
                    if is_green_frame
                    else f"{len(multi_regions)} artwork frames detected geometrically with 3px edge inset."
                    if is_geometric and multi_regions
                    else "Innermost geometric mockup frame layer detected with 3px edge inset."
                    if is_geometric
                    else "Innermost local SAM 2.1 prediction frame isolated with 3px edge inset."
                    if is_sam
                    else "Visible inner or dashed artwork boundary detected locally; manual review required."
                    if refined
                    else "Centered offline estimate; manual review required."
                ),
                "raw_artwork_area": raw_artwork_area,
            },
            image_width=w,
            image_height=h,
            provider="classic",
        )
