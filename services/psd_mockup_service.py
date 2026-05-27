from pathlib import Path


def render_psd_mockup(*, template_id: str, artwork_path: Path):
    """Reserve the PSD mode for a real Photoshop automation implementation.

    Pillow is useful for raster compositing, but it cannot faithfully replace
    Photoshop Smart Objects or execute PSD effects. A future implementation
    should invoke configured Photoshop automation that runs a JSX script,
    replaces the Smart Object contents, and exports the final image.
    """
    raise NotImplementedError(
        "PSD rendering is not implemented yet: configure Photoshop JSX automation."
    )

