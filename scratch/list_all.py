import sys
import json
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from services.catalog_service import CatalogService

catalog = CatalogService(Path("data/mockup_catalog.sqlite3"))
templates = catalog.list_templates()
for t in templates:
    print(f"ID: {t['template_id']}, Name: {t['name']}, Status: {t['status']}")
    print(f"  Effects: {json.dumps(t.get('effects'))}")
    print(f"  Fit Mode: {t.get('fit_mode')}")
    print(f"  Artwork Area: {json.dumps(t.get('artwork_area'))}")
    print("-" * 50)
