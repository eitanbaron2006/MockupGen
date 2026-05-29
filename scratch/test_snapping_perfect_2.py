import sys
sys.path.insert(0, r"c:\Users\Eitan Baron\Desktop\MockupGen")
from pathlib import Path
from PIL import Image, ImageFilter, ImageOps

bg_path = Path(r"c:\Users\Eitan Baron\Desktop\MockupGen\draft_templates\template_821e4eda2b03\background.png")
if not bg_path.exists():
    bg_path = Path(r"c:\Users\Eitan Baron\Desktop\MockupGen\draft_templates\template_821e4eda2b03\background.jpg")

def _custom_inner_strongest_line(
    pixels,
    *,
    vertical: bool,
    center: int,
    radius: int,
    span_start: int,
    span_end: int,
    boundary: int,
    prefer_high: bool,
) -> int:
    candidates = []
    start = max(1, center - radius)
    end = min(boundary - 2, center + radius)
    for position in range(start, end + 1):
        if vertical:
            score = sum(pixels[position, offset] for offset in range(span_start, span_end))
        else:
            score = sum(pixels[offset, position] for offset in range(span_start, span_end))
        candidates.append((score, position))
    if not candidates:
        return center
    
    max_score = max(score for score, _ in candidates)
    if max_score < 100:
        return center
    
    # Lower threshold to 0.25 to make it highly sensitive to the inner opening border!
    threshold = max(200, round(max_score * 0.25))
    qualified = [pos for score, pos in candidates if score >= threshold]
    if not qualified:
        return center
    
    clusters = []
    for pos in sorted(qualified):
        if not clusters or pos - clusters[-1][-1] > 3:
            clusters.append([pos])
        else:
            clusters[-1].append(pos)
            
    best_positions = []
    for cluster in clusters:
        best_pos = cluster[0]
        best_val = -1
        for pos in cluster:
            val = next(score for score, p in candidates if p == pos)
            if val > best_val:
                best_val = val
                best_pos = pos
        best_positions.append(best_pos)
        
    if not best_positions:
        return center
        
    if len(best_positions) > 1:
        return best_positions[-1] if prefer_high else best_positions[0]
    return best_positions[0]

def custom_refine_perspective_corners(image_path, corners, search_radius=60):
    with Image.open(image_path) as source:
        # Gentle GaussianBlur instead of MedianFilter
        gray = source.convert("L").filter(ImageFilter.GaussianBlur(radius=0.5))
        width, height = gray.size
        edges = ImageOps.autocontrast(gray.filter(ImageFilter.FIND_EDGES))
        pixels = edges.load()
        
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
        
        snapped_x = _custom_inner_strongest_line(
            pixels,
            vertical=True,
            center=cx,
            radius=search_radius,
            span_start=span_start_v,
            span_end=span_end_v,
            boundary=width,
            prefer_high=prefer_high_x
        )
        
        snapped_y = _custom_inner_strongest_line(
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
refined = custom_refine_perspective_corners(bg_path, raw_corners, search_radius=60)
print("REFINED PERFECT CORNERS (radius=60):", refined)
