"""Brand identity domain model — the single source of truth for external naming."""

from pydantic import BaseModel, HttpUrl


class BrandIdentity(BaseModel):
    """How this product presents itself to third parties (e.g. OpenRouter)."""

    app_name: str = "Nebuko"
    site_url: HttpUrl = "https://github.com/nebuko/nebuko"
    app_url: HttpUrl = "https://github.com/nebuko/nebuko"
