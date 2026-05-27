import base64
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests
from PIL import Image

from services.detection_service import DetectionError, DetectionProposal, validate_proposal


class LocalDetectionProvider:
    def __init__(self, *, endpoint: str, model: str = "", api_key: str = ""):
        if not endpoint:
            raise DetectionError("Local detection endpoint is required")
        self.endpoint = endpoint
        self.model = model
        self.api_key = api_key

    def detect(self, background_path: Path) -> DetectionProposal:
        with Image.open(background_path) as image:
            width, height = image.size
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            payload = {"image_base64": base64.b64encode(background_path.read_bytes()).decode("ascii")}
            if self.model:
                payload["model"] = self.model
            response = requests.post(
                self.endpoint,
                json=payload,
                headers=headers,
                timeout=90,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as error:
            raise DetectionError(f"Local detector request failed: {error}") from error
        return validate_proposal(
            payload,
            image_width=width,
            image_height=height,
            provider="local",
        )


def discover_local_models(endpoint: str, api_key: str = "") -> list[dict[str, str]]:
    """Read installed models from common local OpenAI-compatible or Ollama APIs."""
    if not endpoint:
        return []
    parts = urlsplit(endpoint.rstrip("/"))
    path = parts.path.rstrip("/")
    base_path = path
    if path.endswith("/chat/completions"):
        base_path = path[: -len("/chat/completions")]
    elif path.endswith("/detect"):
        base_path = path[: -len("/detect")]
    candidates = [
        urlunsplit((parts.scheme, parts.netloc, f"{base_path}/models", "", "")),
        urlunsplit((parts.scheme, parts.netloc, "/v1/models", "", "")),
        urlunsplit((parts.scheme, parts.netloc, "/api/tags", "", "")),
    ]
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    seen: set[str] = set()
    for url in dict.fromkeys(candidates):
        try:
            response = requests.get(url, headers=headers, timeout=4)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError):
            continue
        raw_models = payload.get("data", payload.get("models", []))
        models: list[dict[str, str]] = []
        for raw_model in raw_models:
            model_id = (
                raw_model.get("id") or raw_model.get("name") or raw_model.get("model")
                if isinstance(raw_model, dict)
                else str(raw_model)
            )
            if model_id and model_id not in seen:
                seen.add(model_id)
                models.append({"id": model_id, "label": model_id})
        if models:
            return models
    return []
