import sys
sys.path.insert(0, r"c:\Users\Eitan Baron\Desktop\MockupGen")
from pathlib import Path
from PIL import Image, ImageFilter, ImageOps

bg_path = Path(r"c:\Users\Eitan Baron\Desktop\MockupGen\draft_templates\template_821e4eda2b03\background.png")
if not bg_path.exists():
    bg_path = Path(r"c:\Users\Eitan Baron\Desktop\MockupGen\draft_templates\template_821e4eda2b03\background.jpg")

with Image.open(bg_path) as img:
    gray = img.convert("L")
    
    # Let's test 3 options:
    # 1. No filter, just FIND_EDGES
    edges_none = gray.filter(ImageFilter.FIND_EDGES)
    
    # 2. MedianFilter(3), then FIND_EDGES
    edges_med3 = gray.filter(ImageFilter.MedianFilter(size=3)).filter(ImageFilter.FIND_EDGES)
    
    # 3. MedianFilter(3), FIND_EDGES, then Autocontrast
    edges_med3_ac = ImageOps.autocontrast(edges_med3)
    
    cy = 244
    search_radius = 80
    span_start_v = max(0, cy - search_radius)
    span_end_v = min(img.height, cy + search_radius)
    
    pix_none = edges_none.load()
    pix_med3 = edges_med3.load()
    pix_med3_ac = edges_med3_ac.load()
    
    print("X=499:")
    print("  No filter:", sum(pix_none[499, y] for y in range(span_start_v, span_end_v)))
    print("  Med3:", sum(pix_med3[499, y] for y in range(span_start_v, span_end_v)))
    print("  Med3 + Autocontrast:", sum(pix_med3_ac[499, y] for y in range(span_start_v, span_end_v)))
    
    print("\nX=528:")
    print("  No filter:", sum(pix_none[528, y] for y in range(span_start_v, span_end_v)))
    print("  Med3:", sum(pix_med3[528, y] for y in range(span_start_v, span_end_v)))
    print("  Med3 + Autocontrast:", sum(pix_med3_ac[528, y] for y in range(span_start_v, span_end_v)))
