"""NCBI PubMed E-utilities client (direct search) for ontoprism."""

from ontolib.repositories.pubmed.client import (
    DEFAULT_EUTILS_URL,
    PubMedClient,
)

__all__ = ["DEFAULT_EUTILS_URL", "PubMedClient"]
