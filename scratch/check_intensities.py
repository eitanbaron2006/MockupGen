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
    
    cy = 244
    search_radius = 80
    span_start_v = max(0, cy - search_radius)
    span_end_v = min(img.height, cy + search_radius)
    print(f"Span: Y from {span_start_v} to {span_end_v}")
    
    # Check X=499 and X=528
    val_499 = sum(pixels[499, y] for y in range(span_start_v, span_end_v))
    val_528 = sum(pixels[528, y] for y in range(span_start_v, span_end_v))
    print(f"X=499 edge intensity sum: {val_499}")
    print(f"X=528 edge intensity sum: {val_528}")
