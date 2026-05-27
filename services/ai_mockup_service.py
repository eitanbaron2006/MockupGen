from abc import ABC, abstractmethod
from pathlib import Path


class AIMockupProvider(ABC):
    @abstractmethod
    def render(self, *, template_id: str, artwork_path: Path) -> Path:
        """Render a mockup and return the generated image path."""


class AIMockupService:
    def __init__(self, provider: AIMockupProvider | None = None) -> None:
        self.provider = provider

    def render(self, *, template_id: str, artwork_path: Path) -> Path:
        if self.provider is None:
            raise NotImplementedError(
                "AI rendering is not implemented yet: configure an AI mockup provider."
            )
        return self.provider.render(template_id=template_id, artwork_path=artwork_path)


def render_ai_mockup(
    *, template_id: str, artwork_path: Path, provider: AIMockupProvider | None = None
) -> Path:
    return AIMockupService(provider).render(
        template_id=template_id, artwork_path=artwork_path
    )

