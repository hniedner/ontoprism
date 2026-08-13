"""Uberon/CL API contracts."""

from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from backend.dependencies import (
    get_repository_metadata,
    get_uberon_search_index,
    get_uberon_store,
    get_xref_store,
)
from backend.main import create_app
from backend.repository_metadata import RepositoryUnhealthy
from ontolib.repositories.xref.models import EndpointIdentity, MappingResult
from ontolib.repositories.xref.vocab import CLOSE_MATCH
from ontolib.terminologies.uberon.graph_store import InvalidUberonCurieError
from ontolib.terminologies.uberon.models import (
    UberonConceptDetail,
    UberonGraphNode,
    UberonNeighborhood,
    UberonSearchHit,
    UberonSearchPage,
)


class _Store:
    def __init__(self) -> None:
        self.search_calls: list[tuple[str, str | None]] = []

    async def search(self, query: str, **kwargs: object) -> UberonSearchPage:
        source = kwargs.get("source")
        self.search_calls.append((query, source if isinstance(source, str) else None))
        return UberonSearchPage(
            query=query,
            total=1,
            limit=int(kwargs["limit"]),
            offset=int(kwargs["offset"]),
            hits=[
                UberonSearchHit(code="UBERON:0002048", source="uberon", label="lung")
            ],
        )

    async def list_concepts(self, **kwargs: object) -> UberonSearchPage:
        return await self.search("", **kwargs)

    async def get_concept_detail(self, code: str) -> UberonConceptDetail | None:
        if code == "bad":
            raise InvalidUberonCurieError("invalid")
        if code != "UBERON:0002048":
            return None
        return UberonConceptDetail(code=code, source="uberon", label="lung")

    async def get_neighborhood(self, code: str, *, depth: int) -> UberonNeighborhood:
        return UberonNeighborhood(
            center=code,
            nodes=[UberonGraphNode(code=code, source="uberon", label="lung")],
        )


class _Index:
    def __init__(self, populated: bool, *, fail: bool = False) -> None:
        self.populated = populated
        self.fail = fail

    async def is_populated(self, source_identity: str, source_hash: str) -> bool:
        assert source_identity == "a" * 64
        assert source_hash == "b" * 64
        if self.fail:
            raise OperationalError("cache unavailable", None, Exception())
        return self.populated

    async def search(self, query: str, **kwargs: object) -> UberonSearchPage:
        return UberonSearchPage(
            query=query,
            total=1,
            limit=int(kwargs["limit"]),
            offset=int(kwargs["offset"]),
            hits=[UberonSearchHit(code="CL:0000000", source="cl", label="cached cell")],
        )


class _Xrefs:
    def __init__(self) -> None:
        self.calls: list[set[str]] = []

    async def mappings_for_identifiers(
        self, identifiers: set[str]
    ) -> dict[str, list[MappingResult]]:
        self.calls.append(identifiers)
        return {
            "UBERON:0002048": [
                MappingResult(
                    subject=EndpointIdentity(
                        "uberon-cl", "uberon-2026-06-19", "UBERON:0002048"
                    ),
                    predicate=CLOSE_MATCH,
                    object=EndpointIdentity("ncit", "26.07d", "C12468"),
                    lifecycle="proposed",
                    confidence=0.9,
                )
            ]
        }


class _Metadata:
    async def ncit(self) -> SimpleNamespace:
        return SimpleNamespace(source_identity="n" * 64)

    def cadsr(self) -> SimpleNamespace:
        return SimpleNamespace(source_identity="c" * 64)

    async def uberon(self, *, force: bool = False) -> object:
        del force
        return SimpleNamespace(
            source_identity="a" * 64,
            observation=SimpleNamespace(serving=SimpleNamespace(sha256="b" * 64)),
        )


def _client(
    store: _Store,
    index: _Index,
    metadata: object = _Metadata(),
    xrefs: _Xrefs | None = None,
) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_uberon_store] = lambda: store
    app.dependency_overrides[get_uberon_search_index] = lambda: index
    app.dependency_overrides[get_repository_metadata] = lambda: metadata
    app.dependency_overrides[get_xref_store] = lambda: xrefs or _Xrefs()
    with TestClient(app) as client:
        yield client


@pytest.mark.api
def test_search_uses_source_bound_cache_and_serializes_source_facet() -> None:
    response = next(_client(_Store(), _Index(True))).get(
        "/api/v1/uberon/search", params={"q": "cell", "source": "cl"}
    )

    assert response.status_code == 200
    assert response.json()["hits"][0] == {
        "code": "CL:0000000",
        "source": "cl",
        "label": "cached cell",
        "matched_synonym": None,
    }


@pytest.mark.api
def test_search_identity_mismatch_falls_back_to_certified_store() -> None:
    store = _Store()

    response = next(_client(store, _Index(False))).get(
        "/api/v1/uberon/search", params={"q": "lung", "source": "uberon"}
    )

    assert response.status_code == 200
    assert store.search_calls == [("lung", "uberon")]


@pytest.mark.api
def test_search_database_failure_returns_explicit_unavailable_response() -> None:
    store = _Store()

    response = next(_client(store, _Index(False, fail=True))).get(
        "/api/v1/uberon/search", params={"q": "lung", "source": "uberon"}
    )

    assert response.status_code == 503
    assert "cache is unavailable" in response.json()["detail"]
    assert store.search_calls == []


@pytest.mark.api
def test_unhealthy_repository_refuses_search_before_reads() -> None:
    class _Unhealthy(_Metadata):
        async def uberon(self, *, force: bool = False) -> RepositoryUnhealthy:
            del force
            return RepositoryUnhealthy(
                repository="uberon",
                reason="release-mismatch",
                message="configured and indexed releases differ",
            )

    store = _Store()
    response = next(_client(store, _Index(True), _Unhealthy())).get(
        "/api/v1/uberon/search", params={"q": "lung"}
    )

    assert response.status_code == 503
    assert store.search_calls == []


@pytest.mark.api
def test_detail_and_neighborhood_use_curie_path_segment() -> None:
    client = next(_client(_Store(), _Index(False)))

    detail = client.get("/api/v1/uberon/concepts/UBERON:0002048")
    graph = client.get("/api/v1/uberon/concepts/UBERON:0002048/neighborhood")

    assert detail.status_code == graph.status_code == 200
    assert detail.json()["source"] == "uberon"
    assert graph.json()["nodes"][0]["source"] == "uberon"


@pytest.mark.api
def test_unknown_neighborhood_is_404_not_empty_success() -> None:
    class _UnknownStore(_Store):
        async def get_neighborhood(
            self, code: str, *, depth: int
        ) -> UberonNeighborhood:
            del depth
            raise LookupError(code)

    response = next(_client(_UnknownStore(), _Index(False))).get(
        "/api/v1/uberon/concepts/CL:9999999/neighborhood"
    )

    assert response.status_code == 404
    assert "Concept not found" in response.json()["detail"]


@pytest.mark.api
def test_list_preserves_source_facet_and_detail_refuses_unknown_or_invalid() -> None:
    client = next(_client(_Store(), _Index(False)))

    listed = client.get("/api/v1/uberon/list", params={"source": "cl"})
    unknown = client.get("/api/v1/uberon/concepts/CL:9999999")
    invalid = client.get("/api/v1/uberon/concepts/bad")

    assert listed.status_code == 200
    assert listed.json()["hits"][0]["source"] == "uberon"
    assert unknown.status_code == 404
    assert "Concept not found" in unknown.json()["detail"]
    assert invalid.status_code == 422


@pytest.mark.api
def test_detail_alignments_return_ncit_targets_in_one_indexed_lookup() -> None:
    xrefs = _Xrefs()
    client = next(_client(_Store(), _Index(False), xrefs=xrefs))

    response = client.get("/api/v1/uberon/concepts/UBERON:0002048/alignments")

    assert response.status_code == 200
    assert response.json() == {
        "code": "UBERON:0002048",
        "alignments": [
            {
                "code": "C12468",
                "system": "ncit",
                "version": "26.07d",
                "predicate": CLOSE_MATCH,
                "lifecycle": "proposed",
            }
        ],
    }
    assert xrefs.calls == [{"UBERON:0002048"}]


@pytest.mark.api
def test_alignments_refuse_uncertified_uberon_before_xref_read() -> None:
    class _Unhealthy(_Metadata):
        async def uberon(self, *, force: bool = False) -> RepositoryUnhealthy:
            del force
            return RepositoryUnhealthy(
                repository="uberon", reason="observation-mismatch", message="drift"
            )

    xrefs = _Xrefs()
    response = next(
        _client(_Store(), _Index(False), metadata=_Unhealthy(), xrefs=xrefs)
    ).get("/api/v1/uberon/concepts/UBERON:0002048/alignments")

    assert response.status_code == 503
    assert xrefs.calls == []
