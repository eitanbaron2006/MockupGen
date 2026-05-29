import sys
sys.path.insert(0, r"c:\Users\Eitan Baron\Desktop\MockupGen")
from pathlib import Path
from PIL import Image

bg_path = Path(r"c:\Users\Eitan Baron\Desktop\MockupGen\draft_templates\template_821e4eda2b03\background.png")
if not bg_path.exists():
    bg_path = Path(r"c:\Users\Eitan Baron\Desktop\MockupGen\draft_templates\template_821e4eda2b03\background.jpg")

with Image.open(bg_path) as img:
    w, h = img.size
    print("Dimensions:", w, "x", h)
    
    # Crop a region around top-left corner (530, 233)
    # Let's say a 100x100 region centered at 530, 233
    tl_region = img.crop((480, 183, 580, 283))
    tl_region.save(r"c:\Users\Eitan Baron\Desktop\MockupGen\outputs\top_left_region.png")
    print("Saved top-left crop around 530,233")
