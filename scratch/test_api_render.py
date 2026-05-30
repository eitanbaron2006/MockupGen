import sys
import io
from pathlib import Path
from PIL import Image

sys.path.append(str(Path(__file__).resolve().parent.parent))

from app import create_app

app = create_app()
client = app.test_client()

# Prepare dummy artwork
stream = io.BytesIO()
Image.new("RGBA", (200, 300), (255, 0, 0, 255)).save(stream, format="PNG")
stream.seek(0)

# Let's find an active template ID first
templates_folder = Path("templates_data")
active_templates = [p.name for p in templates_folder.iterdir() if p.is_dir()]
print("Available templates:", active_templates)

template_id = active_templates[0] if active_templates else "template_001"
print(f"Testing API render with template_id: {template_id}")

try:
    response = client.post(
        "/api/mockups/render",
        data={
            "mode": "simple",
            "template_id": template_id,
            "artwork": (stream, "test.png"),
            "realism": "true",
            "fit_mode": "cover",
        },
        content_type="multipart/form-data"
    )
    print("Response Status Code:", response.status_code)
    print("Response JSON:", response.get_json())
except Exception as e:
    import traceback
    traceback.print_exc()
