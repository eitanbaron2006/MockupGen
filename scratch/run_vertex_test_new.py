import os
from pathlib import Path
from dotenv import load_dotenv
from services.detection_service import build_provider
from services.catalog_service import CatalogService

# Load environment variables
load_dotenv()

# Build temporary dummy settings
settings = {
    "DETECTION_PROVIDER": "vertex",
    "VERTEX_PROJECT_ID": os.getenv("VERTEX_PROJECT_ID"),
    "VERTEX_LOCATION": os.getenv("VERTEX_LOCATION", "global"),
    "VERTEX_MODEL": "gemini-3.1-pro-preview",
    "VERTEX_MEDIA_RESOLUTION": "high",
    "DETECTION_REFINEMENT": "hybrid",
    "CLASSIC_SEARCH_RADIUS": "15",
}

config = {
    "VERTEX_PROJECT_ID": os.getenv("VERTEX_PROJECT_ID"),
    "VERTEX_LOCATION": os.getenv("VERTEX_LOCATION", "global"),
    "VERTEX_MODEL": "gemini-3.1-pro-preview",
    "VERTEX_MEDIA_RESOLUTION": "high",
    "DETECTION_REFINEMENT": "hybrid",
}

print(f"Testing building provider with Gemini 3.1 Pro...")
provider = build_provider(settings, config)
print(f"Provider class: {provider.__class__.__name__}")

img_path = Path("templates_data/template_71df1eb48bad/background.png")
if not img_path.exists():
    print(f"Error: {img_path} does not exist.")
    exit(1)

print("Running detect on background...")
try:
    proposal = provider.detect(img_path)
    print("SUCCESS!")
    print(f"Confidence: {proposal.confidence}")
    print(f"Reason: {proposal.reason}")
    print("Artwork Area Corners:")
    print(proposal.artwork_area.get("corners"))
    print("Raw Artwork Area Corners:")
    print(proposal.raw_artwork_area.get("corners") if proposal.raw_artwork_area else "None")
except Exception as e:
    import traceback
    traceback.print_exc()
