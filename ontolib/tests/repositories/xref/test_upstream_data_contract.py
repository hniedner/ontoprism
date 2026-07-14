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
from ontolib.repositories.xref.candidate_ingest import (
    _build_xref_index,
    fetch_uberon_xrefs,
    generate_candidates,
)
from ontolib.repositories.xref.promotion import (
    build_upstream_edges_query,
    build_upstream_partof_query,
)
from ontolib.repositories.xref.ttl_writer import SUPPORTED_PREFIXES
from ontolib.repositories.xref.vocab import COMPOSITE_MATCHING
from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

pytestmark = pytest.mark.integration

_OBO = "http://purl.obolibrary.org/obo/"
_LUNG = f"{_OBO}UBERON_0002048"
_RESPIRATORY_SYSTEM = f"{_OBO}UBERON_0001004"
_RESPIRATION_ORGAN = f"{_OBO}UBERON_0000171"


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


async def _ncit() -> OxigraphHttpClient | None:
    client = OxigraphHttpClient(get_settings().ncit_sparql_url)
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


# ── #78: the facts the mixed subClassOf / part_of corroboration walk relies on ──


async def test_the_organ_reaches_its_system_by_subclass_then_part_of() -> None:
    """THE #78 fact: an organ reaches its system only through ``subClassOf`` *then*
    ``part_of``, never either alone.

    On the live store ``lung ⊑* respiration organ`` (subClassOf) is true and
    ``respiration organ part_of respiratory system`` is true, but ``lung ⊑* respiratory
    system`` is false (pinned by ``test_an_organ_is_not_subsumed_by_its_system``). So
    structural corroboration for the canonical correct pair exists *only* if the walk
    crosses from subClassOf into part_of mid-path — which is exactly what
    ``promotion.corroboration`` now does.

    If this fails, either Uberon restructured the respiratory branch or
    ``build_upstream_partof_query`` stopped finding the edge — in both cases structural
    corroboration for anatomy silently dies, so fail here and name it.
    """
    client = await _uberon()
    if client is None:
        pytest.skip("Uberon store not loaded")
    try:
        lung_under_organ = await client.ask(
            "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> "
            f"ASK {{ <{_LUNG}> rdfs:subClassOf* <{_RESPIRATION_ORGAN}> }}"
        )
        partof_rows = await client.select(
            build_upstream_partof_query(["UBERON:0002048"])
        )
    finally:
        await client.aclose()

    assert lung_under_organ is True, (
        "Uberon no longer subsumes lung under 'respiration organ' — the subClassOf leg "
        "of the mixed corroboration walk is broken; revisit promotion.corroboration."
    )
    edges = {(str(r["child"]), str(r["parent"])) for r in partof_rows}
    assert (_RESPIRATION_ORGAN, _RESPIRATORY_SYSTEM) in edges, (
        "build_upstream_partof_query no longer returns 'respiration organ part_of "
        "respiratory system' for lung's ancestor cone — structural corroboration for "
        "anatomy just went dark. Uberon changed, or the query's subClassOf* prefix / "
        f"BFO_0000050 restriction pattern regressed. Got edges: {sorted(edges)[:8]}"
    )


async def test_the_upstream_spells_its_ncit_xrefs_ncit_and_ingest_reads_them() -> None:
    """The xref pass's entire input, pinned to what the store actually writes.

    Uberon/CL write ``oboInOwl:hasDbXref "NCIT:C12468"``.  The ingest and promotion
    filters were written for ``NCI:`` — and ``STRSTARTS("NCIT:C12468", "NCI:")`` is
    **false**, so the xref pass matched nothing on every real run: zero xref candidates,
    ``XREF_ASSERTION`` evidence that could never fire, and therefore no non-curated
    promotion at all.  The whole hermetic suite stayed green throughout, because the
    fixtures spelled the prefix exactly as wrongly as the code did.  That is the failure
    this file exists to catch, and it can only be caught by asking the store.
    """
    client = await _uberon()
    if client is None:
        pytest.skip("Uberon store not loaded")
    try:
        rows = await fetch_uberon_xrefs(client)
    finally:
        await client.aclose()

    index = _build_xref_index(rows)
    assert index, (
        "the xref pass reads NOTHING out of the real upstream store: one of the two "
        "ingest signals — and the only one independent of the labels — is dead. Check "
        "the prefix in `candidate_ingest._OBO_NCI_PREFIX` against what "
        "`oboInOwl:hasDbXref` actually carries."
    )
    assert index.get("C12468") == ["UBERON:0002048"], (
        "the canonical pair (NCIt Lung -> Uberon lung) is no longer reachable through "
        "the upstream's own cross-reference — source-agreement promotion loses its "
        "reference case"
    )


async def test_the_real_stores_co_generate_source_agreeing_candidates() -> None:
    """THE premise of D33 Option 1, asked of the real stores rather than of a fixture.

    Auto-promotion without curation now rests on a pair being produced by BOTH ingest
    passes: an OBO curator asserted ``NCI:<code>`` on the upstream class *and* the two
    labels agree.  A fixture can always be built so that this happens; the question no
    fixture can answer is whether it happens on the live NCIt + Uberon/CL.  If it never
    does, ``promoted_on_source_agreement`` stays zero, ``COV`` stays pinned, and #73 is
    still a curated-set importer — while every hermetic test in the suite passes.

    So run the real ingest against both live stores and demand at least one composite
    candidate.  If this drops to zero after a release, promotion has silently reverted
    to curated-pairs-only, and it must fail *here*, naming the reason.
    """
    ncit = await _ncit()
    uberon = await _uberon()
    if ncit is None or uberon is None:
        for client in (ncit, uberon):
            if client is not None:
                await client.aclose()
        pytest.skip("NCIt (:7888) and/or Uberon (:7889) store not loaded")
    try:
        records, _ = await generate_candidates(
            ncit, uberon, "ncit-contract", "uberon-contract"
        )
    finally:
        await ncit.aclose()
        await uberon.aclose()

    composites = [r for r in records if r.mapping_justification == COMPOSITE_MATCHING]
    assert composites, (
        "no filler on the anatomic-site / cell-origin axes is BOTH xref'd by an "
        "upstream class and label-identical to it. Source agreement is the only "
        "non-curated promotion path there is, so promotion is back to importing "
        "curated pairs and COV cannot move — see D33/D34 and issue #73."
    )


async def test_the_part_of_walk_never_yields_a_curie_we_cannot_expand() -> None:
    """Same trap as the subClassOf walk: the part_of query must filter BOTH ends to
    expandable prefixes, or one ``GO:``/``COB:`` filler ``object_iri`` cannot expand
    aborts the whole run.
    """
    client = await _uberon()
    if client is None:
        pytest.skip("Uberon store not loaded")
    try:
        rows = await client.select(build_upstream_partof_query(["UBERON:0002048"]))
    finally:
        await client.aclose()

    assert rows, "expected some part_of ancestor edges for lung"
    allowed = tuple(f"{_OBO}{prefix}_" for prefix in SUPPORTED_PREFIXES)
    for row in rows:
        for end in ("child", "parent"):
            iri = str(row[end])
            assert iri.startswith(allowed), (
                f"{end} {iri} carries a prefix object_iri cannot expand — this would "
                "KeyError and abort the whole run"
            )
