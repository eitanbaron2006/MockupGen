from typing import Any


FALLBACK_VERTEX_DETECTION_MODELS = [
    {"id": "gemini-3.5-flash", "label": "Gemini 3.5 Flash", "stage": "GA"},
    {
        "id": "gemini-3.1-flash-lite",
        "label": "Gemini 3.1 Flash-Lite",
        "stage": "GA / budget",
    },
    {
        "id": "gemini-3.1-pro-preview",
        "label": "Gemini 3.1 Pro Preview",
        "stage": "Preview",
    },
    {"id": "gemini-3-flash-preview", "label": "Gemini 3 Flash Preview", "stage": "Preview"},
    {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash", "stage": "GA"},
    {"id": "gemini-2.5-flash-lite", "label": "Gemini 2.5 Flash-Lite", "stage": "GA / budget"},
    {"id": "gemini-2.5-pro", "label": "Gemini 2.5 Pro", "stage": "GA"},
]


def _detection_compatible(model_id: str) -> bool:
    lowered = model_id.lower()
    excluded = ("-image", "-tts", "audio", "live", "embedding")
    return lowered.startswith("gemini-") and not any(part in lowered for part in excluded)


def _label(model_id: str) -> str:
    return model_id.replace("-", " ").title().replace("Gemini ", "Gemini ")


def _stage(raw_stage: str) -> str:
    return {
        "GA": "GA",
        "PUBLIC_PREVIEW": "Preview",
        "EXPERIMENTAL": "Experimental",
    }.get(raw_stage, raw_stage.replace("PUBLIC_", "").title())


def list_vertex_detection_models(session: Any | None = None) -> list[dict[str, str]]:
    """List current Gemini models suitable for image-to-JSON detection from Model Garden."""
    if session is None:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        credentials, _project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        session = AuthorizedSession(credentials)

    response = session.get(
        "https://aiplatform.googleapis.com/v1beta1/publishers/google/models?pageSize=100",
        timeout=30,
    )
    response.raise_for_status()
    models: list[dict[str, str]] = []
    for raw_model in response.json().get("publisherModels", []):
        model_id = str(raw_model.get("name", "")).rsplit("/", 1)[-1]
        if not _detection_compatible(model_id):
            continue
        stage = _stage(str(raw_model.get("launchStage", "")))
        models.append({"id": model_id, "label": _label(model_id), "stage": stage})
    return sorted(models, key=lambda model: model["id"], reverse=True) or FALLBACK_VERTEX_DETECTION_MODELS
