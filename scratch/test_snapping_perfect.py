import sys
sys.path.insert(0, r"c:\Users\Eitan Baron\Desktop\MockupGen")
from pathlib import Path
from PIL import Image, ImageFilter, ImageOps

bg_path = Path(r"c:\Users\Eitan Baron\Desktop\MockupGen\draft_templates\template_821e4eda2b03\background.png")
if not bg_path.exists():
    bg_path = Path(r"c:\Users\Eitan Baron\Desktop\MockupGen\draft_templates\template_821e4eda2b03\background.jpg")

def custom_refine_perspective_corners(image_path, corners, search_radius=40):
    with Image.open(image_path) as source:
        # Convert to grayscale directly, no aggressive MedianFilter!
        # Maybe a tiny GaussianBlur to suppress high-frequency sensor noise, but keep it sharp!
        gray = source.convert("L").filter(ImageFilter.GaussianBlur(radius=0.5))
        width, height = gray.size
        edges = ImageOps.autocontrast(gray.filter(ImageFilter.FIND_EDGES))
        pixels = edges.load()
        
    from services.frame_refinement_service import _inner_strongest_line
    
    refined_corners = []
    for idx, p in enumerate(corners):
        cx = int(p["x"])
        cy = int(p["y"])
        
        span_start_v = max(0, cy - search_radius)
        span_end_v = min(height, cy + search_radius)
        span_start_h = max(0, cx - search_radius)
        span_end_h = min(width, cx + search_radius)
        
        prefer_high_x = idx in (0, 3)
        prefer_high_y = idx in (0, 1)
        
        snapped_x = _inner_strongest_line(
            pixels,
            vertical=True,
            center=cx,
            radius=search_radius,
            span_start=span_start_v,
            span_end=span_end_v,
            boundary=width,
            prefer_high=prefer_high_x
        )
        
        snapped_y = _inner_strongest_line(
            pixels,
            vertical=False,
            center=cy,
            radius=search_radius,
            span_start=span_start_h,
            span_end=span_end_h,
            boundary=height,
            prefer_high=prefer_high_y
        )
        
        refined_corners.append({"x": snapped_x, "y": snapped_y})
    return refined_corners

# Let's test with Gemini 2.5 Pro's shifted corners:
raw_corners = [
    {"x": 468, "y": 244},
    {"x": 862, "y": 237},
    {"x": 859, "y": 805},
    {"x": 469, "y": 810}
]

print("RAW SHIFTED CORNERS:", raw_corners)
refined = custom_refine_perspective_corners(bg_path, raw_corners, search_radius=80)
print("REFINED PERFECT CORNERS:", refined)
