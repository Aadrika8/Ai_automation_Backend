"""Application settings, loaded from environment / .env."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Sentinel default: the server refuses to start while this is the active secret
# (see app.main lifespan), so a real random JWT_SECRET must come from .env.
PLACEHOLDER_SECRET = "dev-only-secret-change-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    mongo_uri: str = "mongodb://127.0.0.1:27017"
    mongo_db: str = "quality_insights"
    jwt_secret: str = PLACEHOLDER_SECRET
    jwt_expires_min: int = 1440
    # Vite serves on 5173, but hops to the next free port when it is taken —
    # a second dev server, or one left running. Allowing the ports it falls
    # back to keeps a browser on 5174 from failing CORS preflight with an
    # opaque 400. 5199 is the port the UI verification suite uses.
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://localhost:5199",
    ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
