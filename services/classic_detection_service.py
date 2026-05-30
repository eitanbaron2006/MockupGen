from pathlib import Path
import cv2
import numpy as np
from PIL import Image

from services.catalog_service import orientation_for_size
from services.detection_service import DetectionError, DetectionProposal, validate_proposal
from services.frame_refinement_service import refine_artwork_area, refine_perspective_corners
from services.green_frame_mockup_service import (
    GreenFrameSettings,
    detect_green_frames,
    green_detection_raw,
    green_mask_image,
)


class ClassicDetectionProvider:
    """Conservative offline fallback using high-fidelity multi-layer geometric and SAM 2.1 boundary detection."""

    def __init__(
        self,
        blur_size: int = 3,
        search_radius: int = 20,
        default_mode: str = "auto",
        green_edge_expand: int = 1,
    ):
        self.blur_size = blur_size
        self.search_radius = search_radius
        self.default_mode = default_mode or "auto"
        self.green_edge_expand = max(0, int(green_edge_expand))

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
        return GreenFrameSettings(edge_expand=self.green_edge_expand, min_area=80)

    def _detect_green_frame(self, img: np.ndarray) -> tuple[np.ndarray | None, dict | None]:
        image = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)).convert("RGBA")
        state = detect_green_frames(image, self._green_settings())
        if not state.regions:
            return None, {"green_pixels": 0, "regions": []}
        raw = green_detection_raw(state, self.green_edge_expand)
        first = raw["regions"][0]["corners"]
        chosen_pts = np.array([[point["x"], point["y"]] for point in first], dtype="int32")
        return chosen_pts, raw

    def detect(self, background_path: Path, mode: str | None = None, point: dict = None) -> DetectionProposal:
        mode = mode or self.default_mode
        # Load background image via OpenCV
        img = cv2.imread(str(background_path))
        if img is None:
            raise FileNotFoundError(f"Could not open image: {background_path}")

        h, w, _ = img.shape
        img_area = w * h

        chosen_pts = None
        is_geometric = False
        is_sam = False
        is_green_frame = False
        all_layers = []

        if mode == "green_frames_mockups":
            chosen_pts, raw_artwork_area = self._detect_green_frame(img)
            if chosen_pts is None:
                raise DetectionError("No green frame mockup region could be detected.")
            is_green_frame = True

        # 1. Step 1 (geometry): Canny & contours (Run if mode is "auto" or "geometry")
        if chosen_pts is None and mode in ("auto", "geometry"):
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            blurred = cv2.GaussianBlur(gray, (5, 5), 0)
            edged = cv2.Canny(blurred, 50, 150)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
            dilated = cv2.dilate(edged, kernel, iterations=1)

            contours, _ = cv2.findContours(dilated, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            valid_rectangles = []

            for c in contours:
                peri = cv2.arcLength(c, True)
                approx = cv2.approxPolyDP(c, 0.02 * peri, True)
                if len(approx) == 4:
                    area = cv2.contourArea(approx)
                    # Filter by relative scale in the mockup
                    if (img_area * 0.04) < area < (img_area * 0.90):
                        x, y, box_w, box_h = cv2.boundingRect(approx)
                        aspect_ratio = float(box_w) / box_h
                        if 0.2 < aspect_ratio < 5.0:
                            valid_rectangles.append((area, approx))

            unique_layers = []
            if valid_rectangles:
                # Sort the rectangles from largest (outermost) to smallest (innermost)
                valid_rectangles.sort(key=lambda x: x[0], reverse=True)

                # Filter out duplicate or near-identical rectangles (less than 1.5% difference in area)
                for r in valid_rectangles:
                    if not unique_layers or abs(r[0] - unique_layers[-1][0]) > (img_area * 0.015):
                        unique_layers.append(r)

            if unique_layers:
                # Populate all layers clockwise
                for layer_idx, (_, approx) in enumerate(unique_layers):
                    pts = approx.reshape(4, 2)
                    sorted_pts = np.zeros((4, 2), dtype="int32")
                    s = pts.sum(axis=1)
                    sorted_pts[0] = pts[np.argmin(s)]  # TL
                    sorted_pts[2] = pts[np.argmax(s)]  # BR
                    diff = np.diff(pts, axis=1)
                    sorted_pts[1] = pts[np.argmin(diff)]  # TR
                    sorted_pts[3] = pts[np.argmax(diff)]  # BL
                    all_layers.append([{"x": int(pt[0]), "y": int(pt[1])} for pt in sorted_pts])

                # Innermost layer (smallest) is the selected one by default
                chosen_pts = np.array([[pt["x"], pt["y"]] for pt in all_layers[-1]], dtype="int32")
                is_geometric = True

        # 2. Step 2 (sam_center) or Step 3 (sam_point)
        # Run if no geometry chosen and we allow auto, or if mode is explicitly requested
        if chosen_pts is None and (mode in ("auto", "sam_center", "sam_point")):
            try:
                from ultralytics import SAM
                sam_model = SAM("sam2.1_l.pt")
                
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

        # If explicitly requesting geometry/sam modes and it failed to find corners, raise DetectionError
        if mode in ("geometry", "sam_center", "sam_point", "green_frames_mockups") and chosen_pts is None:
            raise DetectionError(f"No boundary corners could be resolved using {mode} mode.")

        # If we successfully found corners (either from Canny or SAM), apply 3px Inset
        if chosen_pts is not None:
            if is_green_frame:
                final_corners = [{"x": int(pt[0]), "y": int(pt[1])} for pt in chosen_pts]
                xs = [c["x"] for c in final_corners]
                ys = [c["y"] for c in final_corners]
                artwork_area = {
                    "x": min(xs),
                    "y": min(ys),
                    "width": max(xs) - min(xs),
                    "height": max(ys) - min(ys),
                    "corners": final_corners,
                }
                refined = True
            else:
            # Apply a 3-pixel inset towards the centroid to guarantee exact placement inside the frame borders
                centroid = np.mean(chosen_pts, axis=0)
                final_corners = []
                for pt in chosen_pts:
                    vec = centroid - pt
                    norm = np.linalg.norm(vec)
                    if norm > 0:
                        inset_pt = pt + (vec / norm) * 3
                    else:
                        inset_pt = pt
                    final_corners.append({"x": int(round(inset_pt[0])), "y": int(round(inset_pt[1]))})

                xs = [c["x"] for c in final_corners]
                ys = [c["y"] for c in final_corners]
                min_x, max_x = min(xs), max(xs)
                min_y, max_y = min(ys), max(ys)
                artwork_area = {
                    "x": min_x,
                    "y": min_y,
                    "width": max_x - min_x,
                    "height": max_y - min_y,
                    "corners": final_corners
                }
                refined = True
                raw_artwork_area = {
                    "layers": all_layers,
                    "original_corners": [{"x": int(pt[0]), "y": int(pt[1])} for pt in chosen_pts]
                }
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
