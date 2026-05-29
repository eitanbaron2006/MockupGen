import sys
sys.path.insert(0, r"c:\Users\Eitan Baron\Desktop\MockupGen")
from pathlib import Path
from PIL import Image, ImageDraw

bg_path = Path(r"c:\Users\Eitan Baron\Desktop\MockupGen\draft_templates\template_821e4eda2b03\background.png")
if not bg_path.exists():
    bg_path = Path(r"c:\Users\Eitan Baron\Desktop\MockupGen\draft_templates\template_821e4eda2b03\background.jpg")

print("Image path:", bg_path)
with Image.open(bg_path) as img:
    w, h = img.size
    print("Dimensions:", w, "x", h)
    
    # Let's save a copy with the AI box drawn
    draw_img = img.copy()
    draw = ImageDraw.Draw(draw_img)
    
    # Draw Gemini 3.5 Flash box: [530, 233, 845, 789]
    draw.rectangle([530, 233, 845, 789], outline="red", width=3)
    
    # Draw Gemini 2.5 Pro box: [468, 244, 862, 805]
    draw.rectangle([468, 244, 862, 805], outline="blue", width=3)
    
    outputPath = Path(r"c:\Users\Eitan Baron\Desktop\MockupGen\outputs\test_box.png")
    outputPath.parent.mkdir(parents=True, exist_ok=True)
    draw_img.save(outputPath)
    print("Saved test_box.png to", outputPath)
