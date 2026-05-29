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
    
    cx, cy = 859, 805
    search_radius = 80
    
    span_start_h = max(0, cx - search_radius)
    span_end_h = min(img.width, cx + search_radius)
    
    candidates_y = []
    start_y = max(1, cy - search_radius)
    end_y = min(img.height - 2, cy + search_radius)
    for position in range(start_y, end_y + 1):
        score = sum(pixels[offset, position] for offset in range(span_start_h, span_end_h))
        candidates_y.append((score, position))
        
    candidates_y.sort(key=lambda x: x[0], reverse=True)
    print("Top 10 strongest horizontal edges for Bottom-Right corner Y:")
    for score, pos in candidates_y[:10]:
        print(f"  Row Y={pos}: score={score}")
