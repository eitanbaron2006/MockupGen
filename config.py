import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _folder(name: str, default: str) -> str:
    return os.getenv(name, str(BASE_DIR / default))


def _enabled(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


UPLOAD_FOLDER = _folder("UPLOAD_FOLDER", "uploads")
OUTPUT_FOLDER = _folder("OUTPUT_FOLDER", "outputs")
TEMPLATES_FOLDER = _folder("TEMPLATES_FOLDER", "templates_data")
DRAFT_TEMPLATES_FOLDER = _folder("DRAFT_TEMPLATES_FOLDER", "draft_templates")
DATABASE_PATH = _folder("DATABASE_PATH", "data/mockup_catalog.sqlite3")
_max_content = os.getenv("MAX_CONTENT_LENGTH", "").strip()
MAX_CONTENT_LENGTH = int(_max_content) if _max_content else None
ENABLE_SIMPLE_MODE = _enabled("ENABLE_SIMPLE_MODE", True)
ENABLE_PSD_MODE = _enabled("ENABLE_PSD_MODE", False)
ENABLE_AI_MODE = _enabled("ENABLE_AI_MODE", False)


class Config:
    UPLOAD_FOLDER = UPLOAD_FOLDER
    OUTPUT_FOLDER = OUTPUT_FOLDER
    TEMPLATES_FOLDER = TEMPLATES_FOLDER
    DRAFT_TEMPLATES_FOLDER = DRAFT_TEMPLATES_FOLDER
    DATABASE_PATH = DATABASE_PATH
    MAX_CONTENT_LENGTH = MAX_CONTENT_LENGTH
    ENABLE_SIMPLE_MODE = ENABLE_SIMPLE_MODE
    ENABLE_PSD_MODE = ENABLE_PSD_MODE
    ENABLE_AI_MODE = ENABLE_AI_MODE
    CORS_ORIGINS = os.getenv("CORS_ORIGINS", "").strip()
    SECRET_KEY = os.getenv("SECRET_KEY", "development-only-change-me")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
    DETECTION_PROVIDER = os.getenv("DETECTION_PROVIDER", "classic").strip().lower()
    VERTEX_PROJECT_ID = os.getenv("VERTEX_PROJECT_ID", "").strip()
    VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "global").strip()
    VERTEX_MODEL = os.getenv("VERTEX_MODEL", "gemini-2.5-flash").strip()
    VERTEX_MEDIA_RESOLUTION = os.getenv("VERTEX_MEDIA_RESOLUTION", "high").strip().lower()
    VERTEX_AUTH_MODE = os.getenv("VERTEX_AUTH_MODE", "adc").strip().lower()
    DETECTION_REFINEMENT = os.getenv("DETECTION_REFINEMENT", "ai_only").strip().lower()
    LOCAL_DETECTION_URL = os.getenv("LOCAL_DETECTION_URL", "").strip()
    LOCAL_DETECTION_MODEL = os.getenv("LOCAL_DETECTION_MODEL", "").strip()
    LOCAL_DETECTION_API_KEY = os.getenv("LOCAL_DETECTION_API_KEY", "").strip()
