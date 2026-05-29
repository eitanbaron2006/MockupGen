import sys
sys.path.insert(0, r"c:\Users\Eitan Baron\Desktop\MockupGen")
from pathlib import Path
from PIL import Image, ImageFilter

bg_path = Path(r"c:\Users\Eitan Baron\Desktop\MockupGen\draft_templates\template_821e4eda2b03\background.png")
if not bg_path.exists():
    bg_path = Path(r"c:\Users\Eitan Baron\Desktop\MockupGen\draft_templates\template_821e4eda2b03\background.jpg")

with Image.open(bg_path) as img:
    gray = img.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    pixels = edges.load()
    w, h = img.size
    
    # 1. Right border column scan: look around X from 800 to 900
    col_sums = []
    for x in range(800, 900):
        intensity_sum = sum(pixels[x, y] for y in range(250, 500))
        col_sums.append((intensity_sum, x))
    col_sums.sort(reverse=True)
    print("Top 5 strongest vertical edge columns on right (X):")
    for val, x in col_sums[:5]:
        print(f"  Col X={x}: sum of edge intensity={val}")
        
    # 2. Bottom border row scan: look around Y from 700 to 850
    row_sums = []
    for y in range(700, 850):
        intensity_sum = sum(pixels[x, y] for x in range(550, 800))
        row_sums.append((intensity_sum, y))
    row_sums.sort(reverse=True)
    print("\nTop 5 strongest horizontal edge rows on bottom (Y):")
    for val, y in row_sums[:5]:
        print(f"  Row Y={y}: sum of edge intensity={val}")
