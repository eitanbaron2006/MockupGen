import requests
import io
from PIL import Image

base_url = "http://127.0.0.1:5000"

try:
    resp = requests.get(f"{base_url}/api/mockups/templates")
    templates = resp.json()
    print(f"Loaded {len(templates)} templates.")
    
    # Create dummy artwork bytes
    img = Image.new("RGBA", (100, 100), (255, 0, 0, 255))
    img_bytes = io.BytesIO()
    img.save(img_bytes, format="PNG")
    
    tpl_id = "template_001"
    img_bytes.seek(0)
    files = {"artwork": ("artwork.png", img_bytes, "image/png")}
    data = {"mode": "ai", "template_id": tpl_id, "model": "gemini-3.1-flash-image"}
    
    print(f"Testing AI render mode on '{tpl_id}'...")
    resp_render = requests.post(f"{base_url}/api/mockups/render", files=files, data=data)
    print(f"Render Response Status: {resp_render.status_code}")
    
    try:
        payload = resp_render.json()
        print("Success payload:", payload)
    except Exception:
        print("Response is not JSON! Printing first 2000 characters of response:")
        print(resp_render.text[:2000])
            
except Exception as e:
    print(f"Error connecting: {e}")
