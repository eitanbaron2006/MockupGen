import sys
sys.path.insert(0, r"c:\Users\Eitan Baron\Desktop\MockupGen")
from pathlib import Path
from PIL import Image, ImageFilter, ImageOps

bg_path = Path(r"c:\Users\Eitan Baron\Desktop\MockupGen\draft_templates\template_821e4eda2b03\background.png")
if not bg_path.exists():
    bg_path = Path(r"c:\Users\Eitan Baron\Desktop\MockupGen\draft_templates\template_821e4eda2b03\background.jpg")

with Image.open(bg_path) as img:
    gray = img.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    pixels = edges.load()
    
    cx, cy = 468, 244
    search_radius = 80
    
    span_start_h = max(0, cx - search_radius)
    span_end_h = min(img.width, cx + search_radius)
    
    print("--- DEBUGGING Y SNAP (NO FILTER) ---")
    candidates_y = []
    start_y = max(1, cy - search_radius)
    end_y = min(img.height - 2, cy + search_radius)
    for position in range(start_y, end_y + 1):
        score = sum(pixels[offset, position] for offset in range(span_start_h, span_end_h))
        candidates_y.append((score, position))
        
    max_score_y = max(score for score, _ in candidates_y)
    threshold_y = max(200, round(max_score_y * 0.45))
    qualified_y = [pos for score, pos in candidates_y if score >= threshold_y]
    print("max_score_y:", max_score_y, "threshold_y:", threshold_y)
    print("qualified_y rows:", qualified_y)
    
    clusters_y = []
    for pos in sorted(qualified_y):
        if not clusters_y or pos - clusters_y[-1][-1] > 3:
            clusters_y.append([pos])
        else:
            clusters_y[-1].append(pos)
    print("clusters_y:", clusters_y)
    
    best_positions_y = []
    for cluster in clusters_y:
        best_pos = cluster[0]
        best_val = -1
        for pos in cluster:
            val = next(score for score, p in candidates_y if p == pos)
            if val > best_val:
                best_val = val
                best_pos = pos
        best_positions_y.append(best_pos)
    print("best_positions_y peaks:", best_positions_y)
