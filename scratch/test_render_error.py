import sys
from pathlib import Path
from PIL import Image

# Add root folder to sys.path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from services.simple_mockup_service import render_simple_mockup

try:
    print("Testing render_simple_mockup with realism=True...")
    templates_folder = Path("templates_data")
    output_folder = Path("outputs")
    artwork_path = Path("dashed-area.png") # use existing small image
    
    result = render_simple_mockup(
        template_id="template_001",
        artwork_path=artwork_path,
        output_format="png",
        templates_folder=templates_folder,
        output_folder=output_folder,
        realism=True,
    )
    print("Render successful! Output:", result.output_url)
except Exception as e:
    import traceback
    traceback.print_exc()
