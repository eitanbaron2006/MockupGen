import sys
sys.path.insert(0, r"c:\Users\Eitan Baron\Desktop\MockupGen")
from pathlib import Path
from PIL import Image, ImageFilter, ImageOps
import services.frame_refinement_service as frs

bg_path = Path(r"c:\Users\Eitan Baron\Desktop\MockupGen\draft_templates\template_821e4eda2b03\background.png")
if not bg_path.exists():
    bg_path = Path(r"c:\Users\Eitan Baron\Desktop\MockupGen\draft_templates\template_821e4eda2b03\background.jpg")

with Image.open(bg_path) as img:
    # Let's test with a simulated top-left corner shifted to 468, 244 (like Gemini 2.5 Pro returned)
    # The actual corner should be around x=528, y=201 (inner wood border or wood frame edge)
    cx, cy = 468, 244
    print(f"Simulated raw corner: ({cx}, {cy})")
    
    # Let's run the existing refine_perspective_corners with search_radius=10
    corners = [{"x": cx, "y": cy}, {"x": 862, "y": 237}, {"x": 859, "y": 805}, {"x": 469, "y": 810}]
    refined_10 = frs.refine_perspective_corners(bg_path, corners, search_radius=10)
    print("Refined with radius=10:", refined_10[0])
    
    # Let's run with search_radius=40
    refined_40 = frs.refine_perspective_corners(bg_path, corners, search_radius=40)
    print("Refined with radius=40:", refined_40[0])
    
    # Let's run with search_radius=60
    refined_60 = frs.refine_perspective_corners(bg_path, corners, search_radius=60)
    print("Refined with radius=60:", refined_60[0])
    
    # Let's run with search_radius=80
    refined_80 = frs.refine_perspective_corners(bg_path, corners, search_radius=80)
    print("Refined with radius=80:", refined_80[0])
