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


class OpenAISettings(BaseSettings):
    """OpenAI, for the release QA report. No key, no report: the endpoint says so.

    Read from .env BEFORE the environment, unlike everything in `Settings`.
    OPENAI_API_KEY is a name other tools set machine-wide, and a stale one
    there silently beat the key this app was given in .env: every report
    failed with "OpenAI rejected the API key" while the .env key was valid.
    For these two, the app's own .env is the source of truth. A blank line in
    .env is skipped, so a key set only in the environment still works.

    The key never reaches the browser.
    """
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8",
                                      extra="ignore", env_ignore_empty=True)

    openai_api_key: str = ""
    openai_model: str = "gpt-5-mini"

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings,
                                   dotenv_settings, file_secret_settings):
        return init_settings, dotenv_settings, env_settings, file_secret_settings


@lru_cache
def get_openai_settings() -> OpenAISettings:
    return OpenAISettings()
