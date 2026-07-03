"""Application settings, loaded from environment / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Backend configuration (see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ncit_sparql_url: str = "http://localhost:7878"
    uberon_sparql_url: str = "http://localhost:7879"
    ncit_expected_version: str = "26.02d"

    # caDSR CDE repository SQLite DB (read-only); defaults to fairdata's build.
    cadsr_db_path: str = "../fairdata/data/cde_repository/cde_repository.db"

    # Guarded raw-SPARQL endpoint limits.
    sparql_timeout_sec: float = 30.0
    sparql_row_cap: int = 1000


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
