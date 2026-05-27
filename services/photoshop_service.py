from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PhotoshopConfig:
    executable_path: Path | None = None
    jsx_script_path: Path | None = None


class PhotoshopService:
    """Configuration boundary for future, explicit Photoshop automation."""

    def __init__(self, config: PhotoshopConfig) -> None:
        self.config = config

    def is_configured(self) -> bool:
        return bool(self.config.executable_path and self.config.jsx_script_path)

    def render_smart_object(
        self, *, psd_path: Path, artwork_path: Path, output_path: Path
    ) -> Path:
        # Intentionally no process invocation until a validated Photoshop/JSX
        # integration and its argument handling are configured.
        raise NotImplementedError(
            "Photoshop automation is not configured for PSD Smart Object rendering."
        )

