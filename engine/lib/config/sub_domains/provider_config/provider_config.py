"""Provider configuration entity."""

from pydantic import BaseModel


class ProviderConfig(BaseModel):
    """API endpoint + auth token environment variable name."""

    api_url: str
    api_token_env: str
