"""Application settings, loaded from environment / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Backend configuration (see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ontoprism's OWN isolated services (see docker-compose.yml); distinct from the
    # sibling fairdata app (7878/7879/5432) so both run without interference.
    ncit_sparql_url: str = "http://localhost:7888"
    uberon_sparql_url: str = "http://localhost:7889"
    ncit_expected_version: str = "26.02d"

    # caDSR CDE repository SQLite DB (ontoprism-owned CoW clone; read-only).
    cadsr_db_path: str = "data/cadsr/cde_repository.db"

    # PostgreSQL (ontoprism-owned): refresh provenance and future concept cache.
    database_url: str = (
        "postgresql+asyncpg://ontoprism:ontoprism@localhost:5433/ontoprism"
    )

    # Guarded raw-SPARQL endpoint limits.
    sparql_timeout_sec: float = 30.0
    sparql_row_cap: int = 1000


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
