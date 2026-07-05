"""Application configuration.

All settings are loaded from environment variables (optionally via a `.env`
file). Sensible defaults are provided so the API can boot out of the box for
the hackathon.
"""

from functools import lru_cache
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- MongoDB ----
    mongo_user: str = "hackathon"
    mongo_password: str = "hackathon"
    mongo_cluster: str = "cluster0.feewznr.mongodb.net"
    mongo_db_name: str = "vibecode"

    # ---- Mistral ----
    mistral_api_key: str = ""
    mistral_model: str = "mistral-large-latest"
    mistral_base_url: str = "https://api.mistral.ai/v1"

    # ---- Application ----
    lock_lease_seconds: int = 120
    cors_origins: str = "*"

    # ---- GitHub export ----
    # No token is stored here: the `gh` CLI must already be authenticated
    # (`gh auth login`) on the machine hosting the backend.
    github_default_visibility: str = "private"

    @property
    def mongo_uri(self) -> str:
        """Build the MongoDB Atlas URI with URL-encoded credentials."""
        user = quote_plus(self.mongo_user)
        password = quote_plus(self.mongo_password)
        return (
            f"mongodb+srv://{user}:{password}@{self.mongo_cluster}/"
            "?retryWrites=true&w=majority&appName=Cluster0"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        value = self.cors_origins.strip()
        if value == "*" or not value:
            return ["*"]
        return [origin.strip() for origin in value.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
