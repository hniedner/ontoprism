"""Application settings, loaded from environment / .env."""

from functools import lru_cache
from typing import Annotated

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Backend configuration (see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ontoprism's OWN isolated services (see docker-compose.yml); distinct from the
    # sibling fairdata app (7878/7879/5432) so both run without interference.
    ncit_sparql_url: str = "http://localhost:7888"
    uberon_sparql_url: str = "http://localhost:7889"
    ncit_expected_version: str = "26.07d"

    # caDSR CDE repository SQLite DB (ontoprism-owned CoW clone; read-only).
    cadsr_db_path: str = "data/cadsr/cde_repository.db"

    # PostgreSQL (ontoprism-owned): refresh provenance and future concept cache.
    database_url: str = (
        "postgresql+asyncpg://ontoprism:ontoprism@localhost:5433/ontoprism"
    )
    # Independent release evidence for embedding completeness. The data-build workflow
    # requires exact agreement with the enumerated source; update on source bump.
    ncit_embedding_expected_rows: Annotated[int, Field(gt=0)] = 206_860
    cadsr_embedding_expected_rows: Annotated[int, Field(gt=0)] = 79_835

    # CORS: browser origins allowed to call the API (the SvelteKit dev/prod hosts).
    cors_allow_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:5175",
    ]

    # Authorization for the mutating endpoints (refresh / reload). When unset (dev
    # default) those endpoints are open; when set, callers must send X-API-Key.
    api_key: str | None = None

    # Per-client-IP rate limit on all endpoints (fixed window). 0 disables it.
    rate_limit_per_minute: int = 600

    # NCIt OWL refresh: EVS download base + the managed artifact-pair directory.
    ncit_owl_base_url: str = "https://evs.nci.nih.gov/ftp1/NCI_Thesaurus"
    ncit_owl_dir: str = "data/ncit-owl"
    ncit_owl_max_retries: int = 3
    # Active serving-store location. Refresh candidates are built beside it but never
    # activated or renamed by the sibling workflow (#148 owns replacement).
    ncit_store_dir: str = "data/qlever-ncit"

    # Full Uberon product, including classes derived from Cell Ontology,
    # and its immutable index.
    uberon_owl_url: str = (
        "https://github.com/obophenotype/uberon/releases/download/"
        "v2026-06-23/uberon.owl"
    )
    uberon_expected_version_iri: str = (
        "http://purl.obolibrary.org/obo/uberon/releases/2026-06-19/uberon.owl"
    )
    uberon_expected_sha256: str = (
        "938f51e7c3fc9fcbe5a2863eb346da8033737e568af5836958891c4c6bfb1192"
    )
    uberon_expected_serving_sha256: str = (
        "2828f839070e49a56d843694b674663d28072ae454c94297ef9e3f2c157e7a81"
    )
    uberon_expected_serving_rows: Annotated[int, Field(gt=0)] = 223_834
    uberon_expected_uberon_classes: Annotated[int, Field(gt=0)] = 16_362
    uberon_expected_cl_classes: int = 1_484
    uberon_expected_uberon_searchable_classes: Annotated[int, Field(gt=0)] = 16_071
    uberon_expected_cl_searchable_classes: Annotated[int, Field(gt=0)] = 1_484
    uberon_owl_dir: str = "data/uberon"
    uberon_owl_max_retries: int = 3
    uberon_store_dir: str = "data/qlever-uberon"

    # caDSR CDE refresh: source archive URL + the managed dir the CDE XML zip is cached
    # in. Threaded through to ontolib.repositories.cadsr.download (mirrors NCIt keys).
    cadsr_download_url: str = (
        "https://cadsr.nci.nih.gov/ftp/caDSR_Downloads/CDE/XML/releasedCDEsXML-OD.zip"
    )
    cadsr_data_dir: str = "data/cadsr"
    cadsr_download_max_retries: int = 3

    # ClinicalTrials.gov v2 client: public API base URL (no key). Overridable to point
    # at a mirror or a test double.
    clinicaltrials_api_url: str = "https://clinicaltrials.gov/api/v2"

    # SNOMED/ICD-O-3 alignments require license confirmation (D26).
    # When False, NCIt mapping/decomposition and translation projections suppress them.
    enable_licensed_mappings: bool = False
    # Separate consumer entitlement for protected ICD-O repository content.
    icdo_entitlement_key: str | None = None
    icdo_32_morphology_source_sha256: str = (
        "7ca51dcb66107d6462b43212b26aa65d52f6b0e306c6295e8c751416b3278a21"
    )
    icdo_32_morphology_serving_sha256: str = (
        "e3f60fc47d4f3bff332501299d3050fe662fdc93b8132d788afa7bd5f791ebf2"
    )
    icdo_40_source_sha256: str = (
        "280ae87dc8bfea873a2346e7a5bee380877da1c84f8339697155fa5e77f3deef"
    )
    icdo_40_morphology_serving_sha256: str = (
        "b1757b0a5184862a5ba64b843b0b46d7739fa9e03d2a0e4d78592662ef38d86a"
    )
    icdo_40_topography_serving_sha256: str = (
        "f2ed3c5a29dd416d9f9df339fdf825015a133b2f0fb64c2fdd1b148811852439"
    )

    # PubMed / NCBI E-utilities client. An API key (optional) raises the rate limit from
    # 3 to 10 req/s; pubmed_requests_per_second throttles to stay within NCBI's policy.
    pubmed_api_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    pubmed_api_key: str | None = None
    pubmed_requests_per_second: float = 3.0


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings."""
    return Settings()
