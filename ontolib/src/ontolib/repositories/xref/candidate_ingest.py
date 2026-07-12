"""Uberon/CL candidate ingest pipeline (PR-A3, issue #72).

Generates closeMatch SSSOM records for NCIt filler concepts against Uberon/CL
anatomy/cell-type codes using two independent sources:

1. **OBO xref annotations** — ``oboInOwl:hasDbXref`` with ``NCI:`` prefix.
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
from ontolib.repositories.xref.vocab import CLOSE_MATCH, NCIT_UPSTREAM_XREF_GRAPH_IRI
from ontolib.terminologies.namespaces import NCIT_NS
from ontolib.terminologies.ncit.owl_load import STATED_GRAPH_IRI

# ── Axis role codes (site / cell-origin) ────────────────────────────────
# R101 = Disease_Has_Primary_Anatomic_Site      (op:PrimarySite)
# R100 = Disease_Has_Associated_Anatomic_Site    (op:AssociatedSite)
# R102 = Disease_Has_Metastatic_Anatomic_Site    (op:MetastaticSite)
# R105 = Disease_Has_Abnormal_Cell               (op:CellOrigin)
_TARGET_ROLES: frozenset[str] = frozenset({"R101", "R100", "R102", "R105"})

# OBO xref format: "NCI:C3262" -> NCIt code "C3262".
_OBO_NCI_PREFIX = "NCI:"
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
    """Fetch Uberon/CL concepts that have ``NCI:`` xref annotations."""
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


def _match_xref(
    fillers: set[str],
    xref_index: dict[str, list[str]],
    ncit_version: str,
    uberon_version: str,
) -> tuple[list[SSSOMRecord], set[str], dict[str, str]]:
    records: list[SSSOMRecord] = []
    matched: set[str] = set()
    filler_to_source: dict[str, str] = {}
    for filler in fillers:
        if filler in xref_index:
            matched.add(filler)
            filler_to_source[filler] = "xref"
            for upstream_code in xref_index[filler]:
                records.append(
                    SSSOMRecord(
                        subject_id=filler,
                        predicate_id=CLOSE_MATCH,
                        object_id=upstream_code,
                        mapping_justification="semapv:DatabaseCrossReference",
                        confidence=0.9,
                        subject_source_version=ncit_version,
                        object_source_version=uberon_version,
                        author="xref-ingest-A3",
                    )
                )
    return records, matched, filler_to_source


def _match_lexical(
    fillers: set[str],
    ncit_labels: dict[str, str],
    label_index: dict[str, list[str]],
    ncit_version: str,
    uberon_version: str,
) -> tuple[list[SSSOMRecord], dict[str, str]]:
    records: list[SSSOMRecord] = []
    filler_to_source: dict[str, str] = {}
    for filler in fillers:
        label = ncit_labels.get(filler)
        if not label:
            filler_to_source[filler] = "none"
            continue
        key = label.casefold()
        curies = label_index.get(key)
        if curies:
            filler_to_source[filler] = "lexical"
            for upstream_code in curies:
                records.append(
                    SSSOMRecord(
                        subject_id=filler,
                        predicate_id=CLOSE_MATCH,
                        object_id=upstream_code,
                        mapping_justification="semapv:LexicalMatching",
                        confidence=0.5,
                        subject_source_version=ncit_version,
                        object_source_version=uberon_version,
                        author="xref-ingest-A3",
                    )
                )
        else:
            filler_to_source[filler] = "none"
    return records, filler_to_source


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
    fillers = await get_filler_codes(ncit_client)
    xrefs = await fetch_uberon_xrefs(uberon_client)
    xref_index = _build_xref_index(xrefs)

    xref_records, matched_via_xref, filler_to_source = _match_xref(
        fillers, xref_index, ncit_version, uberon_version
    )

    remaining = fillers - matched_via_xref
    if remaining:
        ncit_labels = await fetch_ncit_labels(
            ncit_client, remaining, batch_size=batch_size
        )
        upstream_labels = await fetch_upstream_labels(uberon_client)
        label_index = _build_label_index(upstream_labels)

        lexical_records, lexical_sources = _match_lexical(
            remaining, ncit_labels, label_index, ncit_version, uberon_version
        )
        filler_to_source.update(lexical_sources)
        return xref_records + lexical_records, filler_to_source

    return xref_records, filler_to_source


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
    total = len(fillers)
    source_counts = Counter(filler_to_source.values())
    via_xref = source_counts.get("xref", 0)
    via_lexical = source_counts.get("lexical", 0)
    no_candidate = source_counts.get("none", 0)
    recall = (via_xref + via_lexical) / total if total > 0 else 0.0

    return {
        "total_fillers": total,
        "via_xref": via_xref,
        "via_lexical_only": via_lexical,
        "no_candidate": no_candidate,
        "candidate_recall": round(recall, 4),
    }
