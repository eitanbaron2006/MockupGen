import os
from pathlib import Path
from dotenv import load_dotenv
from services.vertex_detection_service import VertexDetectionProvider
from config import Config

# Load environment variables
load_dotenv()

project_id = os.getenv("VERTEX_PROJECT_ID")
location = os.getenv("VERTEX_LOCATION", "global")
model = os.getenv("VERTEX_MODEL", "gemini-2.5-flash")
media_resolution = os.getenv("VERTEX_MEDIA_RESOLUTION", "high")

print(f"Vertex Config: ID={project_id}, Loc={location}, Model={model}, Res={media_resolution}")

if not project_id:
    print("Error: VERTEX_PROJECT_ID not set in environment.")
    exit(1)

# Initialize provider without refinement first to see raw corners
provider_raw = VertexDetectionProvider(
    project_id=project_id,
    location=location,
    model=model,
    media_resolution=media_resolution,
    refine=False
)

img_path = Path("templates_data/template_71df1eb48bad/background.png")
if not img_path.exists():
    print(f"Error: {img_path} does not exist.")
    exit(1)

print("Running raw detection...")
try:
    proposal_raw = provider_raw.detect(img_path)
    print("Raw AI corners:")
    print(proposal_raw.artwork_area.get("corners"))
except Exception as e:
    print(f"Raw detection failed: {e}")
