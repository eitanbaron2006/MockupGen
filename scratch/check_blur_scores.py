import sys
sys.path.insert(0, r"c:\Users\Eitan Baron\Desktop\MockupGen")
from pathlib import Path
from PIL import Image, ImageFilter, ImageOps

bg_path = Path(r"c:\Users\Eitan Baron\Desktop\MockupGen\draft_templates\template_821e4eda2b03\background.png")
if not bg_path.exists():
    bg_path = Path(r"c:\Users\Eitan Baron\Desktop\MockupGen\draft_templates\template_821e4eda2b03\background.jpg")

with Image.open(bg_path) as img:
    gray = img.convert("L").filter(ImageFilter.GaussianBlur(radius=0.5))
    edges = ImageOps.autocontrast(gray.filter(ImageFilter.FIND_EDGES))
    pixels = edges.load()
    
    cx, cy = 468, 244
    search_radius = 80
    
    span_start_v = max(0, cy - search_radius)
    span_end_v = min(img.height, cy + search_radius)
    
    candidates_x = []
    start_x = max(1, cx - search_radius)
    end_x = min(img.width - 2, cx + search_radius)
    for position in range(start_x, end_x + 1):
        score = sum(pixels[position, offset] for offset in range(span_start_v, span_end_v))
        candidates_x.append((score, position))
        
    max_score_x = max(score for score, _ in candidates_x)
    threshold_x = max(200, round(max_score_x * 0.45))
    qualified_x = [pos for score, pos in candidates_x if score >= threshold_x]
    print("max_score_x:", max_score_x, "threshold_x:", threshold_x)
    
    # Let's print candidate scores around 480-535
    for score, pos in sorted(candidates_x, key=lambda x: x[1]):
        if 480 <= pos <= 535 and score >= 200:
            print(f"  Col X={pos}: score={score} {'*' if pos in qualified_x else ''}")
