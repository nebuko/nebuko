"""Brand identity domain model — the single source of truth for external naming."""

from pydantic import BaseModel, HttpUrl


class BrandIdentity(BaseModel):
    """How this product presents itself to third parties (e.g. OpenRouter)."""

    app_name: str = "Nebuko"
    site_url: HttpUrl = "https://github.com/nebuko/nebuko"
    app_url: HttpUrl = "https://github.com/nebuko/nebuko"
    # Single source of truth for the slash-command prefix (issue #749).
    command_name: str = "nebuko"

    # Comment logo rendering (issue #778). URLs are theme-tagged by the viewer
    # theme each one serves; the comment as a whole is theme-adaptive.
    comment_logo_light_url: HttpUrl = (
        "https://raw.githubusercontent.com/nebuko/nebuko/main/logo/final/mark-light.svg"
    )
    comment_logo_dark_url: HttpUrl = (
        "https://raw.githubusercontent.com/nebuko/nebuko/main/logo/final/mark-dark.svg"
    )
    comment_logo_size: int = 16

    def logo_mark(self) -> str:  # @stub-protected
        """Single-line theme-adaptive ``<picture>`` fragment for bot comment prefixes.

        Exactly one line (no newline) so it can prefix both a notice line and a
        ``<summary>`` label. The ``<source>`` serves the dark-theme viewers; the
        ``<img>`` is the light-theme fallback, sized to emoji scale.
        """
        return (
            f'<picture><source srcset="{self.comment_logo_dark_url}"'
            ' media="(prefers-color-scheme: dark)">'
            f'<img src="{self.comment_logo_light_url}" alt="{self.app_name}"'
            f' width="{self.comment_logo_size}" height="{self.comment_logo_size}">'
            "</picture>"
        )
