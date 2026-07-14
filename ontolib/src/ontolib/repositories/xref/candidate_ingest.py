"""Uberon/CL candidate ingest pipeline (PR-A3, issue #72).

Generates closeMatch SSSOM records for NCIt filler concepts against Uberon/CL
anatomy/cell-type codes using two independent sources:

1. **OBO xref annotations** — ``oboInOwl:hasDbXref`` with ``NCIT:`` prefix.
2. **Lexical matching** — exact case-folded ``rdfs:label`` equality.

See ``docs/ARCHITECTURE.md`` §8.3 (ingoest step) for the design rationale.
"""

from __future__ import annotations

import uuid
from collections import Counter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ontolib.repositories.xref.store import XrefStore
    from ontolib.terminologies.oxigraph_http_client import OxigraphHttpClient

from ontolib.repositories.xref.models import SSSOMRecord
from ontolib.repositories.xref.ttl_writer import render_ttl
from ontolib.repositories.xref.vocab import (
    CLOSE_MATCH,
    COMPOSITE_MATCHING,
    DATABASE_CROSS_REFERENCE,
    LEXICAL_MATCHING,
    NCIT_UPSTREAM_XREF_GRAPH_IRI,
)
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI

# Which pass(es) produced a candidate for a filler — the coverage report's buckets.
# `SOURCE_XREF` / `SOURCE_LEXICAL` / `SOURCE_NONE` partition the filler set;
# `SOURCE_BOTH` is a filler both passes matched (on the same upstream class or on
# different ones) and counts under `via_xref`.
SOURCE_XREF = "xref"
SOURCE_LEXICAL = "lexical"
SOURCE_BOTH = "both"
SOURCE_NONE = "none"

# Confidences are the ingest-time priors the SSSOM row carries; they order candidates,
# they never gate promotion (that is the evidence policy plus the EL/ELK gate).  Two
# independent processes agreeing on a pair is a stronger prior than either alone — and
# still short of 1.0, because agreement is not proof.
_XREF_CONFIDENCE = 0.9
_LEXICAL_CONFIDENCE = 0.5
_COMPOSITE_CONFIDENCE = 0.95

# ── Axis role codes (site / cell-origin) ────────────────────────────────
# R101 = Disease_Has_Primary_Anatomic_Site      (op:PrimarySite)
# R100 = Disease_Has_Associated_Anatomic_Site    (op:AssociatedSite)
# R102 = Disease_Has_Metastatic_Anatomic_Site    (op:MetastaticSite)
# R105 = Disease_Has_Abnormal_Cell               (op:CellOrigin)
_TARGET_ROLES: frozenset[str] = frozenset({"R101", "R100", "R102", "R105"})

# OBO xref format: "NCIT:C3262" -> NCIt code "C3262".
#
# It is `NCIT:`, not `NCI:` — verified against the live Uberon/CL store, where 2,542
# UBERON/CL classes carry an `NCIT:` xref and **zero** carry an `NCI:` one.  The pass
# was written for `NCI:`, and `STRSTARTS("NCIT:C12468", "NCI:")` is false, so it
# matched nothing on real data: no xref candidates, and `XREF_ASSERTION` evidence that
# could never fire for any candidate, anywhere.  That is the mechanical reason #73
# promoted only curated pairs.  It is pinned by `test_upstream_data_contract` — the
# only kind of test that could have caught it, since every fixture in the suite had the
# prefix wrong in exactly the same way as the code.
_OBO_NCI_PREFIX = "NCIT:"
_OBO_BASE = "http://purl.obolibrary.org/obo/"
_OBO_INOWL_NS = "http://www.geneontology.org/formats/oboInOwl#"
_RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"
_OWL_NS = "http://www.w3.org/2002/07/owl#"

_LABEL_BATCH_SIZE = 500


# -- A3.1: Filler code extraction ---------------------------------------


def build_filler_codes_query() -> str:
    """Build SPARQL for distinct NCIt filler codes on target site/cell axes.

    Queries the stated NCIt graph for ``owl:someValuesFrom`` restrictions
    on the four anatomic-site / cell-origin roles.
    """
    role_iris = " ".join(f"<{NCIT_NS}{r}>" for r in sorted(_TARGET_ROLES))
    return f"""\
PREFIX rdfs: <{_RDFS_NS}>
PREFIX owl: <{_OWL_NS}>
SELECT DISTINCT ?fillerCode WHERE {{
    GRAPH <{STATED_GRAPH_IRI}> {{
        ?concept rdfs:subClassOf ?restriction .
        ?restriction a owl:Restriction ;
            owl:onProperty ?role ;
            owl:someValuesFrom ?filler .
        FILTER(STRSTARTS(STR(?filler), "{NCIT_NS}"))
        VALUES ?role {{ {role_iris} }}
    }}
    BIND(REPLACE(STR(?filler), ".*#", "") AS ?fillerCode)
}}
"""


async def get_filler_codes(client: OxigraphHttpClient) -> set[str]:
    """Query the NCIt store for distinct filler codes on target axes."""
    rows = await client.select(build_filler_codes_query())
    result: set[str] = set()
    for row in rows:
        code = row.get("fillerCode")
        if code:
            result.add(code)
    return result


# -- A3.2: Candidate generation -----------------------------------------


def build_uberon_xref_query() -> str:
    """Build SPARQL for OBO xref annotations in the Uberon/CL store."""
    return f"""\
PREFIX oboInOwl: <{_OBO_INOWL_NS}>
SELECT ?upstream ?xref WHERE {{
    ?upstream oboInOwl:hasDbXref ?xref .
    FILTER(STRSTARTS(?xref, "{_OBO_NCI_PREFIX}"))
}}
"""


async def fetch_uberon_xrefs(
    client: OxigraphHttpClient,
) -> list[dict[str, str]]:
    """Fetch Uberon/CL concepts that have ``NCIT:`` xref annotations."""
    rows = await client.select(build_uberon_xref_query())
    result: list[dict[str, str]] = []
    for row in rows:
        upstream = row.get("upstream")
        xref = row.get("xref")
        if upstream and xref:
            result.append({"upstream": str(upstream), "xref": str(xref)})
    return result


def _iri_to_curie(iri: str) -> str | None:
    """Convert an OBO IRI to a CURIE.

    Examples::

        ``http://purl.obolibrary.org/obo/UBERON_0002107`` >> ``UBERON:0002107``
        ``http://purl.obolibrary.org/obo/CL_0000057``    >> ``CL:0000057``

    Returns ``None`` for IRIs not under the OBO base.
    """
    if not iri.startswith(_OBO_BASE):
        return None
    suffix = iri.removeprefix(_OBO_BASE)
    if "_" not in suffix:
        return None
    prefix, local = suffix.split("_", 1)
    return f"{prefix}:{local}"


def build_ncit_label_query(codes: list[str]) -> str:
    """Build a batch label query for NCIt codes from the stated graph."""
    iris = " ".join(f"<{NCIT_NS}{c}>" for c in codes)
    return f"""\
PREFIX rdfs: <{_RDFS_NS}>
SELECT ?code ?label WHERE {{
    VALUES ?concept {{ {iris} }}
    ?concept rdfs:label ?label .
    BIND(REPLACE(STR(?concept), ".*#", "") AS ?code)
}}
"""


async def fetch_ncit_labels(
    client: OxigraphHttpClient,
    codes: Iterable[str],
    *,
    batch_size: int = _LABEL_BATCH_SIZE,
) -> dict[str, str]:
    """Fetch ``rdfs:label`` for NCIt codes, returned as ``{code: label}``."""
    code_list = list(codes)
    result: dict[str, str] = {}
    for i in range(0, len(code_list), batch_size):
        batch = code_list[i : i + batch_size]
        for row in await client.select(build_ncit_label_query(batch)):
            code = row.get("code")
            label = row.get("label")
            if code and label:
                result[str(code)] = str(label)
    return result


def build_upstream_labels_query() -> str:
    """Build SPARQL for all ``rdfs:label`` values in the Uberon/CL store."""
    return f"""\
PREFIX rdfs: <{_RDFS_NS}>
SELECT ?concept ?label WHERE {{
    ?concept rdfs:label ?label .
}}
"""


async def fetch_upstream_labels(
    client: OxigraphHttpClient,
) -> dict[str, set[str]]:
    """Fetch all Uberon/CL ``rdfs:label`` values.

    Returns ``{curie: {label, ...}}`` -- one entry per unique CURIE.
    """
    rows = await client.select(build_upstream_labels_query())
    result: dict[str, set[str]] = {}
    for row in rows:
        concept_iri = row.get("concept")
        label = row.get("label")
        if concept_iri and label:
            curie = _iri_to_curie(str(concept_iri))
            if curie:
                result.setdefault(curie, set()).add(str(label))
    return result


def _build_xref_index(
    xrefs: list[dict[str, str]],
) -> dict[str, list[str]]:
    """Convert raw xref rows to ``{nci_code: [upstream_curie, ...]}``."""
    index: dict[str, list[str]] = {}
    for x in xrefs:
        xref_val = x["xref"]
        if not xref_val.startswith(_OBO_NCI_PREFIX):
            continue
        nci_code = xref_val.removeprefix(_OBO_NCI_PREFIX)
        curie = _iri_to_curie(x["upstream"])
        if curie:
            index.setdefault(nci_code, []).append(curie)
    return index


def _provenance(*, from_xref: bool, from_lexical: bool) -> tuple[str, float]:
    """The justification and confidence for a pair, given the passes that produced it.

    ``COMPOSITE_MATCHING`` is not cosmetic: it tells the evidence policy that **two
    independent processes** produced this pair, so neither of them is the pair's sole
    origin and each may corroborate the candidate the other generated (D34).  It has to
    live on the record, because the two passes cannot be kept as two rows:
    ``concept_xref`` is keyed on ``(run_id, subject_id, predicate_id, object_id)`` and
    both rows would be ``closeMatch``, so the second collides on the primary key and is
    dropped — the agreement would be lost on the way to the database.
    """
    if from_xref and from_lexical:
        return COMPOSITE_MATCHING, _COMPOSITE_CONFIDENCE
    if from_xref:
        return DATABASE_CROSS_REFERENCE, _XREF_CONFIDENCE
    return LEXICAL_MATCHING, _LEXICAL_CONFIDENCE


def _records_for_filler(
    filler: str,
    xref_curies: set[str],
    lexical_curies: set[str],
    ncit_version: str,
    uberon_version: str,
) -> list[SSSOMRecord]:
    """One candidate per distinct upstream class this filler matched, either way."""
    records: list[SSSOMRecord] = []
    for curie in sorted(xref_curies | lexical_curies):
        justification, confidence = _provenance(
            from_xref=curie in xref_curies, from_lexical=curie in lexical_curies
        )
        records.append(
            SSSOMRecord(
                subject_id=filler,
                predicate_id=CLOSE_MATCH,
                object_id=curie,
                mapping_justification=justification,
                confidence=confidence,
                subject_source_version=ncit_version,
                object_source_version=uberon_version,
                author="xref-ingest-A3",
            )
        )
    return records


def _filler_source(xref_curies: set[str], lexical_curies: set[str]) -> str:
    """Which passes produced a candidate for this filler (for the coverage report)."""
    if xref_curies and lexical_curies:
        return SOURCE_BOTH
    if xref_curies:
        return SOURCE_XREF
    if lexical_curies:
        return SOURCE_LEXICAL
    return SOURCE_NONE


def _build_label_index(
    upstream_labels: dict[str, set[str]],
) -> dict[str, list[str]]:
    """Build ``{casefolded_label: [curie, ...]}`` from upstream labels."""
    index: dict[str, list[str]] = {}
    for curie, labels in upstream_labels.items():
        for label in labels:
            index.setdefault(label.casefold(), []).append(curie)
    return index


async def generate_candidates(
    ncit_client: OxigraphHttpClient,
    uberon_client: OxigraphHttpClient,
    ncit_version: str,
    uberon_version: str,
    *,
    batch_size: int = _LABEL_BATCH_SIZE,
) -> tuple[list[SSSOMRecord], dict[str, str]]:
    """Generate candidates for every filler, from **both** signals (#73, D33 Option 1).

    Both passes run over **all** fillers.  An earlier cut ran the lexical pass only over
    ``fillers - matched_via_xref``, which made the two signals mutually exclusive *by
    construction*: a lexically-matched pair could never also be xref-asserted, so no
    machine-generated candidate could ever carry the two independent signals D28
    requires, and promotion (#73) reduced to importing SME-curated pairs.  Where the two
    passes now converge on a pair, that agreement is recorded as one composite candidate
    (:func:`_provenance`); where they disagree, both candidates are proposed and neither
    can promote alone.
    """
    fillers = await get_filler_codes(ncit_client)
    if not fillers:
        return [], {}

    xref_index = _build_xref_index(await fetch_uberon_xrefs(uberon_client))
    ncit_labels = await fetch_ncit_labels(ncit_client, fillers, batch_size=batch_size)
    label_index = _build_label_index(await fetch_upstream_labels(uberon_client))

    records: list[SSSOMRecord] = []
    filler_to_source: dict[str, str] = {}
    for filler in sorted(fillers):
        xref_curies = set(xref_index.get(filler, ()))
        label = ncit_labels.get(filler)
        lexical_curies = set(label_index.get(label.casefold(), ())) if label else set()

        records.extend(
            _records_for_filler(
                filler, xref_curies, lexical_curies, ncit_version, uberon_version
            )
        )
        filler_to_source[filler] = _filler_source(xref_curies, lexical_curies)

    return records, filler_to_source


# -- A3.3: Persist orchestration ---------------------------------------


async def ingest_candidates(
    store: XrefStore,
    ncit_client: OxigraphHttpClient,
    uberon_client: OxigraphHttpClient,
    ncit_version: str,
    uberon_version: str,
    *,
    run_id: str | None = None,
    source: str = "uberon-cl",
) -> dict[str, Any]:  # pragma: no cover — integration-only orchestration
    """Run the full candidate-ingest pipeline and persist results.

    1. Creates an ``xref_run``.
    2. Generates candidates via :func:`generate_candidates`.
    3. Upserts records via *store*.
    4. Renders Turtle and loads it into ``NCIT_UPSTREAM_XREF_GRAPH_IRI``.
    5. Updates the run with the coverage report (metrics).

    Returns the coverage report dict.
    """
    rid = run_id or uuid.uuid4().hex
    records, filler_to_source = await generate_candidates(
        ncit_client, uberon_client, ncit_version, uberon_version
    )
    fillers = set(filler_to_source)

    await store.upsert_run(
        run_id=rid,
        source=source,
        ncit_version=ncit_version,
        source_version=uberon_version,
    )

    await store.upsert_records(rid, records)

    ttl = render_ttl(records)
    await ncit_client.load(
        ttl.encode("utf-8"),
        content_type="text/turtle",
        graph_iri=NCIT_UPSTREAM_XREF_GRAPH_IRI,
        replace=False,
    )

    report = candidate_coverage_report(fillers, records, filler_to_source)
    await store.update_run_metrics(rid, report)

    return report


# -- A3.4: Coverage report ----------------------------------------------


def candidate_coverage_report(
    fillers: set[str],
    records: list[SSSOMRecord],
    filler_to_source: dict[str, str],
) -> dict[str, Any]:
    """Filler-level ingest metrics, plus the pairs the two sources agree on.

    ``via_xref`` / ``via_lexical_only`` / ``no_candidate`` partition the filler set, and
    ``candidate_recall`` is the fraction with any candidate at all — unchanged
    definitions, so the #72 recall baseline stays comparable across this change.

    ``source_agreement_pairs`` is new and is the number that matters for #73: it counts
    the ``(subject, object)`` pairs BOTH passes produced, which is the only set that can
    promote without SME curation or structural corroboration.  A run reporting zero here
    has (again) promoted nothing but curated pairs, and should say so out loud rather
    than leave that to be inferred from a ``promoted`` count.
    """
    total = len(fillers)
    source_counts = Counter(filler_to_source.values())
    # a `both` filler holds an xref candidate — the buckets must still partition
    via_xref = source_counts.get(SOURCE_XREF, 0) + source_counts.get(SOURCE_BOTH, 0)
    via_lexical = source_counts.get(SOURCE_LEXICAL, 0)
    no_candidate = source_counts.get(SOURCE_NONE, 0)
    recall = (via_xref + via_lexical) / total if total > 0 else 0.0

    return {
        "total_fillers": total,
        "via_xref": via_xref,
        "via_lexical_only": via_lexical,
        "no_candidate": no_candidate,
        "candidate_recall": round(recall, 4),
        "source_agreement_pairs": sum(
            r.mapping_justification == COMPOSITE_MATCHING for r in records
        ),
    }
