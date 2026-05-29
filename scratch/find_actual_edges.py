import sys
sys.path.insert(0, r"c:\Users\Eitan Baron\Desktop\MockupGen")
from pathlib import Path
from PIL import Image, ImageFilter, ImageOps

bg_path = Path(r"c:\Users\Eitan Baron\Desktop\MockupGen\draft_templates\template_821e4eda2b03\background.png")
if not bg_path.exists():
    bg_path = Path(r"c:\Users\Eitan Baron\Desktop\MockupGen\draft_templates\template_821e4eda2b03\background.jpg")

with Image.open(bg_path) as img:
    # Let's convert to grayscale and apply FIND_EDGES
    gray = img.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    pixels = edges.load()
    
    # We want to find the real edges around the top-left area: X in [500, 560], Y in [200, 260]
    print("--- VERTICAL EDGES SCAN (X COORDINATE OF LEFT BORDER) ---")
    # Sum edge intensities vertically for each X column in a range of Y values (e.g. Y from 250 to 500)
    col_sums = []
    for x in range(500, 580):
        intensity_sum = sum(pixels[x, y] for y in range(250, 500))
        col_sums.append((intensity_sum, x))
    col_sums.sort(reverse=True)
    print("Top 5 strongest vertical edge columns (X):")
    for val, x in col_sums[:5]:
        print(f"  Col X={x}: sum of edge intensity={val}")
        
    print("\n--- HORIZONTAL EDGES SCAN (Y COORDINATE OF TOP BORDER) ---")
    # Sum edge intensities horizontally for each Y row in a range of X values (e.g. X from 550 to 800)
    row_sums = []
    for y in range(200, 280):
        intensity_sum = sum(pixels[x, y] for x in range(550, 800))
        row_sums.append((intensity_sum, y))
    row_sums.sort(reverse=True)
    print("Top 5 strongest horizontal edge rows (Y):")
    for val, y in row_sums[:5]:
        print(f"  Row Y={y}: sum of edge intensity={val}")
