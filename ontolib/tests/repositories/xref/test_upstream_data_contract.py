"""Data-shape contract: what the REAL upstream store actually looks like.

**Why this file exists.** A hand-made fixture can only encode what its author believed
about the data. Several #73 bugs were exactly that gap — the fixtures were plausible and
wrong, and the code was written to match them:

* Uberon relates an organ to its system with ``part_of``, **not** ``subClassOf``.
  Assuming
  subsumption made a "the two ontologies disagree" veto fire on the *canonical correct*
  mapping (NCIt Lung -> Uberon lung) and would have pinned coverage at zero.
* The ``subClassOf*`` walk climbs out of UBERON/CL into ``GO:``/``COB:`` upper-ontology
  classes, whose CURIEs ``object_iri`` cannot expand — one such candidate aborts the
  run.
* Uberon carries no ``owl:versionInfo``, only ``owl:versionIRI`` — so a guard demanding
  versionInfo hard-failed on the project's own documented happy path.

None of that is discoverable from a fixture; it is only discoverable by asking the
store.
These tests pin those facts. When Uberon changes, they fail *here* and name the
assumption,
instead of silently changing what the promotion pass promotes.

**These MUST run against the real store, and they SKIP IN CI** (CI has no Uberon
endpoint). That is deliberate, not an oversight: seeding a hand-made Uberon fixture
into CI would make them assert facts about *that fixture* — reintroducing the exact
fiction they exist to prevent. A data-shape contract is worth something only if it
interrogates the real thing.

So they are a **pre-merge local gate**: run `pdm run test-integration` against the live
Uberon store (:7889) before merging anything that touches the upstream read path. A skip
here is not a pass — see AGENTS.md.
"""

from __future__ import annotations

import pytest

from backend.config import get_settings
from ontolib.repositories.xref.promotion import build_upstream_edges_query
from ontolib.repositories.xref.ttl_writer import SUPPORTED_PREFIXES
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

pytestmark = pytest.mark.integration

_OBO = "http://purl.obolibrary.org/obo/"
_LUNG = f"{_OBO}UBERON_0002048"
_RESPIRATORY_SYSTEM = f"{_OBO}UBERON_0001004"


async def _uberon() -> OxigraphHttpClient | None:
    client = OxigraphHttpClient(get_settings().uberon_sparql_url)
    try:
        if await client.count() == 0:
            await client.aclose()
            return None
    except Exception:
        await client.aclose()
        return None
    return client


async def test_an_organ_is_not_subsumed_by_its_system() -> None:
    """Uberon models organ -> system with ``part_of``, not ``subClassOf``.

    This is THE fact that invalidated the "contradicted" veto. Our corroboration walk
    follows only ``subClassOf``, so for most correct anatomy mappings the object is
    simply
    **not entailed** to sit under the anchored system — which under the open-world
    assumption is *unknown*, not *false*, and must never be read as a contradiction.

    If this test ever fails, Uberon has changed its modelling and structural
    corroboration
    suddenly means something much stronger — go and re-read `promotion.corroboration`.
    """
    client = await _uberon()
    if client is None:
        pytest.skip("Uberon store not loaded")
    try:
        subsumed = await client.ask(
            "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> "
            f"ASK {{ <{_LUNG}> rdfs:subClassOf* <{_RESPIRATORY_SYSTEM}> }}"
        )
    finally:
        await client.aclose()

    assert subsumed is False, (
        "Uberon now subsumes lung under respiratory system. Structural corroboration "
        "just became far stronger than promotion.corroboration assumes — revisit the "
        "NOT_ENTAILED semantics and issue #78 (part_of)."
    )


async def test_the_upstream_walk_never_yields_a_curie_we_cannot_expand() -> None:
    """The ancestor walk climbs into upper ontologies (GO:, COB:, BFO:).

    ``object_iri`` raises ``KeyError`` for those prefixes, and the exception is not
    caught
    per-candidate — one such candidate anywhere in the corpus aborts the entire
    promotion
    run, discarding every promotion computed before it. The query must therefore filter
    BOTH ends to the prefixes we can expand.
    """
    client = await _uberon()
    if client is None:
        pytest.skip("Uberon store not loaded")
    try:
        rows = await client.select(build_upstream_edges_query(["UBERON:0002048"]))
    finally:
        await client.aclose()

    assert rows, "expected some ancestor edges for lung"
    allowed = tuple(f"{_OBO}{prefix}_" for prefix in SUPPORTED_PREFIXES)
    for row in rows:
        for end in ("child", "parent"):
            iri = str(row[end])
            assert iri.startswith(allowed), (
                f"{end} {iri} carries a prefix object_iri cannot expand — this would "
                "KeyError and abort the whole run"
            )


async def test_the_upstream_store_can_name_its_own_version() -> None:
    """A promoted bridge asserts "validated against these endpoint versions", and the
    D29
    sweep compares exactly those strings — so a run that cannot name what it validated
    against cannot honour D29.

    Uberon publishes no ``owl:versionInfo``; it publishes ``owl:versionIRI``. A guard
    that
    demanded versionInfo hard-failed on the documented happy path and pushed the
    operator
    toward hand-typing a version, which is precisely what makes the sweep self-
    consistent
    forever.
    """
    client = await _uberon()
    if client is None:
        pytest.skip("Uberon store not loaded")
    try:
        rows = await client.select(
            "PREFIX owl: <http://www.w3.org/2002/07/owl#> "
            "SELECT ?info ?iri WHERE { ?ont a owl:Ontology . "
            "OPTIONAL { ?ont owl:versionInfo ?info } "
            "OPTIONAL { ?ont owl:versionIRI ?iri } } LIMIT 1"
        )
    finally:
        await client.aclose()

    assert rows, "the Uberon store has no owl:Ontology header at all"
    assert rows[0].get("info") or rows[0].get("iri"), (
        "the Uberon store can no longer name its own version — "
        "`data-build xref-promote` will refuse to run (by design: D29 staleness "
        "would be undetectable)"
    )
