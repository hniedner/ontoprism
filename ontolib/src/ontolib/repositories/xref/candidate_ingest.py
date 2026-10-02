"""Uberon/CL candidate ingest pipeline (PR-A3, issue #72).

Generates closeMatch SSSOM records for routed NCIt anatomy and normal-cell fillers
against Uberon/CL codes using two independent sources:

1. **OBO xref annotations** — ``oboInOwl:hasDbXref`` with ``NCIT:`` prefix.
2. **Lexical matching** — exact case-folded ``rdfs:label`` equality.

See ``docs/ARCHITECTURE.md`` §8.3 (ingoest step) for the design rationale.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from ontolib.decomposition.complete_definition import SelectRows
    from ontolib.decomposition.models import SourceDefinitionOccurrence
    from ontolib.repositories.xref.store import XrefStore
    from ontolib.terminologies.sparql_http_client import SparqlHttpClient
from ontolib.decomposition.axis_contracts import (
    AXIS_CONTRACTS,
    normalized_axis_for_role,
)
from ontolib.decomposition.complete_definition import (
    AnchorDefinitionRowsCache,
    UnsupportedDefinitionConstructorError,
    read_complete_definition,
)
from ontolib.decomposition.scope import enumerate_scope_codes
from ontolib.repositories.xref.models import (
    CandidateContext,
    SSSOMRecord,
    UberonCandidateGenerationMetadata,
)
from ontolib.repositories.xref.publication import fail_run_on_error, publish_generation
from ontolib.repositories.xref.source_versions import (
    MappingSourceVersions,
    SparqlSelectClient,
    read_mapping_source_versions,
)
from ontolib.repositories.xref.vocab import (
    CLOSE_MATCH,
    COMPOSITE_MATCHING,
    DATABASE_CROSS_REFERENCE,
    LEXICAL_MATCHING,
    UBERON_CL_CURIE_PREFIXES,
)
from ontolib.terminologies.namespaces import NCIT_NS


class CandidateSourceInventoryError(RuntimeError):
    """The NCIt source cannot supply the filler inventory required for ingest."""


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

_MAPPING_RANGE_CODES = frozenset({"C12219", "C12508"})
_ABNORMAL_CELL_ROLE = "R105"

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
_OBO_NCIT_PREFIX = "NCIT:"
_OBO_BASE = "http://purl.obolibrary.org/obo/"
_OBO_INOWL_NS = "http://www.geneontology.org/formats/oboInOwl#"
_RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"

_LABEL_BATCH_SIZE = 500
_NEOPLASM_ROOT = "C3262"


# -- A3.1: Filler code extraction ---------------------------------------


def _canonical_positive_counts(
    values: tuple[tuple[str, int], ...],
    *,
    label: str,
    valid_key: Callable[[str], bool],
) -> tuple[tuple[str, int], ...]:
    counts = dict(values)
    if len(counts) != len(values):
        raise ValueError(f"{label} require unique keys")
    for key, count in counts.items():
        if not valid_key(key) or count < 1:
            raise ValueError(f"{label} require valid keys and positive counts")
    return tuple(sorted(counts.items()))


@dataclass(frozen=True, slots=True)
class CandidateInventory:
    """Distinct routed, excluded, and unrouted inputs plus unreadable definitions."""

    contexts: tuple[CandidateContext, ...]
    excluded_counts: tuple[tuple[str, int], ...]
    unrouted_counts: tuple[tuple[str, int], ...] = ()
    unknown_counts: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "contexts", tuple(sorted(set(self.contexts))))
        object.__setattr__(
            self,
            "excluded_counts",
            _canonical_positive_counts(
                self.excluded_counts,
                label="excluded counts",
                valid_key=lambda role: role.startswith("R") and role[1:].isdigit(),
            ),
        )
        object.__setattr__(
            self,
            "unrouted_counts",
            _canonical_positive_counts(
                self.unrouted_counts,
                label="unrouted counts",
                valid_key=lambda role: role.startswith("R") and role[1:].isdigit(),
            ),
        )
        object.__setattr__(
            self,
            "unknown_counts",
            _canonical_positive_counts(
                self.unknown_counts,
                label="unknown counts",
                valid_key=bool,
            ),
        )

    @property
    def excluded_by_role(self) -> dict[str, int]:
        return dict(self.excluded_counts)

    @property
    def unrouted_by_role(self) -> dict[str, int]:
        return dict(self.unrouted_counts)

    @property
    def unknown_by_reason(self) -> dict[str, int]:
        return dict(self.unknown_counts)

    @property
    def fillers(self) -> set[str]:
        return {context.source_filler for context in self.contexts}


def _routed_mapping_axis(role_code: str) -> str | None:
    axis = normalized_axis_for_role(role_code)
    if axis is None:
        return None
    contract = AXIS_CONTRACTS[axis]
    if role_code == _ABNORMAL_CELL_ROLE:
        return axis
    return axis if contract.range_code in _MAPPING_RANGE_CODES else None


def _partition_candidate_occurrences(
    occurrences: tuple[SourceDefinitionOccurrence, ...],
) -> tuple[set[CandidateContext], set[CandidateContext], set[tuple[str, str]]]:
    included: set[CandidateContext] = set()
    excluded: set[CandidateContext] = set()
    unrouted: set[tuple[str, str]] = set()
    for occurrence in occurrences:
        axis = _routed_mapping_axis(occurrence.role_code)
        if axis is None:
            unrouted.add((occurrence.role_code, occurrence.filler_code))
            continue
        context = CandidateContext(
            source_role=occurrence.role_code,
            source_filler=occurrence.filler_code,
            normalized_axis=axis,
        )
        (excluded if occurrence.role_code == _ABNORMAL_CELL_ROLE else included).add(
            context
        )
    return included, excluded, unrouted


async def extract_candidate_inventory(
    select_fn: SelectRows,
    concept_codes: Iterable[str],
) -> CandidateInventory:
    """Retain routes and count exclusions, unrouted roles, and unreadable inputs."""
    cache = AnchorDefinitionRowsCache()
    included: set[CandidateContext] = set()
    excluded: set[CandidateContext] = set()
    unrouted: set[tuple[str, str]] = set()
    unknown = Counter[str]()
    for concept_code in sorted(set(concept_codes)):
        try:
            definition = await read_complete_definition(
                select_fn,
                concept_code,
                anchor_rows_cache=cache,
            )
        except UnsupportedDefinitionConstructorError:
            unknown["unsupported-definition-constructor"] += 1
            continue
        routed, policy_excluded, not_routed = _partition_candidate_occurrences(
            definition.occurrences
        )
        included.update(routed)
        excluded.update(policy_excluded)
        unrouted.update(not_routed)
    excluded_counts = Counter(context.source_role for context in excluded)
    unrouted_counts = Counter(role for role, _filler in unrouted)
    return CandidateInventory(
        contexts=tuple(sorted(included)),
        excluded_counts=tuple(sorted(excluded_counts.items())),
        unrouted_counts=tuple(sorted(unrouted_counts.items())),
        unknown_counts=tuple(sorted(unknown.items())),
    )


async def read_candidate_inventory(client: SparqlHttpClient) -> CandidateInventory:
    """Read the Neoplasm branch through the shared scope and definition walkers."""
    concept_codes = await enumerate_scope_codes(client, _NEOPLASM_ROOT)
    return await extract_candidate_inventory(client.select, concept_codes)


# -- A3.2: Candidate generation -----------------------------------------


def build_uberon_xref_query() -> str:
    """Build SPARQL for OBO xref annotations in the Uberon/CL store."""
    return f"""\
PREFIX oboInOwl: <{_OBO_INOWL_NS}>
SELECT ?upstream ?xref WHERE {{
    ?upstream oboInOwl:hasDbXref ?xref .
    FILTER(isIRI(?upstream))
    FILTER(
      STRSTARTS(STR(?upstream), "http://purl.obolibrary.org/obo/UBERON_") ||
      STRSTARTS(STR(?upstream), "http://purl.obolibrary.org/obo/CL_")
    )
    FILTER(STRSTARTS(?xref, "{_OBO_NCIT_PREFIX}"))
}}
"""


async def fetch_uberon_xrefs(
    client: SparqlSelectClient,
) -> list[dict[str, str]]:
    """Fetch Uberon/CL concepts that have ``NCIT:`` xref annotations."""
    rows = await client.select(build_uberon_xref_query())
    result: list[dict[str, str]] = []
    for row in rows:
        upstream = row.get("upstream")
        xref = row.get("xref")
        if not upstream or not xref:
            raise CandidateSourceInventoryError("incomplete xref row")
        upstream_iri = str(upstream)
        curie = _iri_to_curie(upstream_iri)
        if curie is None or not curie.startswith(UBERON_CL_CURIE_PREFIXES):
            raise CandidateSourceInventoryError(
                "xref row belongs to an unknown source ontology"
            )
        result.append({"upstream": upstream_iri, "xref": str(xref)})
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
    client: SparqlSelectClient,
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
    client: SparqlSelectClient,
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
            if curie and curie.startswith(UBERON_CL_CURIE_PREFIXES):
                result.setdefault(curie, set()).add(str(label))
    return result


def _build_xref_index(
    xrefs: list[dict[str, str]],
) -> dict[str, list[str]]:
    """Convert raw xref rows to ``{nci_code: [upstream_curie, ...]}``."""
    index: dict[str, list[str]] = {}
    for x in xrefs:
        xref_val = x["xref"]
        if not xref_val.startswith(_OBO_NCIT_PREFIX):
            continue
        nci_code = xref_val.removeprefix(_OBO_NCIT_PREFIX)
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
    Both records would have the same typed mapping identity inside one generation, so
    the second collides on the primary key and is dropped — the agreement would be lost
    on the way to the database.
    """
    if from_xref and from_lexical:
        return COMPOSITE_MATCHING, _COMPOSITE_CONFIDENCE
    if from_xref:
        return DATABASE_CROSS_REFERENCE, _XREF_CONFIDENCE
    if from_lexical:
        return LEXICAL_MATCHING, _LEXICAL_CONFIDENCE
    raise ValueError("_provenance called with neither xref nor lexical signal")


def _records_for_filler(
    filler: str,
    xref_curies: set[str],
    lexical_curies: set[str],
    versions: MappingSourceVersions,
    contexts: tuple[CandidateContext, ...],
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
                subject_system="ncit",
                predicate_id=CLOSE_MATCH,
                object_id=curie,
                object_system="uberon-cl",
                mapping_justification=justification,
                confidence=confidence,
                subject_source_version=versions.ncit,
                object_source_version=versions.upstream_for(curie),
                author="xref-ingest-A3",
                candidate_contexts=contexts,
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
    ncit_client: SparqlSelectClient,
    uberon_client: SparqlSelectClient,
    versions: MappingSourceVersions,
    *,
    batch_size: int = _LABEL_BATCH_SIZE,
    inventory: CandidateInventory | None = None,
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
    if inventory is None:
        inventory = await read_candidate_inventory(
            cast("SparqlHttpClient", ncit_client)
        )
    fillers = inventory.fillers
    if not fillers:
        return [], {}

    xref_index = _build_xref_index(await fetch_uberon_xrefs(uberon_client))
    ncit_labels = await fetch_ncit_labels(ncit_client, fillers, batch_size=batch_size)
    label_index = _build_label_index(await fetch_upstream_labels(uberon_client))

    records: list[SSSOMRecord] = []
    filler_to_source: dict[str, str] = {}
    contexts_by_filler: dict[str, list[CandidateContext]] = defaultdict(list)
    for context in inventory.contexts:
        contexts_by_filler[context.source_filler].append(context)
    for filler in sorted(fillers):
        xref_curies = set(xref_index.get(filler, ()))
        label = ncit_labels.get(filler)
        lexical_curies = set(label_index.get(label.casefold(), ())) if label else set()

        records.extend(
            _records_for_filler(
                filler,
                xref_curies,
                lexical_curies,
                versions,
                tuple(contexts_by_filler[filler]),
            )
        )
        filler_to_source[filler] = _filler_source(xref_curies, lexical_curies)

    return records, filler_to_source


# -- A3.3: Persist orchestration ---------------------------------------


def _require_routed_inventory(inventory: CandidateInventory) -> None:
    if inventory.contexts:
        return
    observed_but_unrouted = (
        inventory.excluded_counts
        or inventory.unrouted_counts
        or inventory.unknown_counts
    )
    message = (
        "NCIt filler inventory has no routed fillers"
        if observed_but_unrouted
        else "NCIt filler inventory is empty"
    )
    raise CandidateSourceInventoryError(message)


async def ingest_candidates(
    store: XrefStore,
    ncit_client: SparqlHttpClient,
    uberon_client: SparqlSelectClient,
    ncit_version: str,
    uberon_version: str,
    *,
    ncit_source_identity: str,
    uberon_source_identity: str,
    uberon_serving_identity: str,
    observe_source_identities: Callable[[], Awaitable[tuple[str, str, str]]],
    run_id: str | None = None,
    source: str = "uberon-cl",
    inventory: CandidateInventory | None = None,
) -> dict[str, Any]:
    """Run the full candidate-ingest pipeline and persist results.

    1. Reads all three ontology versions, cross-checking certified NCIt and Uberon
       releases and validating the CL release-IRI shape.
    2. Reads complete definitions, routes source roles, and generates candidates.
    3. Checks source identities after generation and before creating an ``xref_run``.
    4. Publishes one immutable, source-specific PostgreSQL/RDF generation.
    5. Updates the run with the coverage report (metrics).

    Returns the coverage report dict.
    """
    rid = run_id or uuid.uuid4().hex
    source_metadata = UberonCandidateGenerationMetadata(
        ncit_source_identity=ncit_source_identity,
        uberon_source_identity=uberon_source_identity,
        uberon_serving_identity=uberon_serving_identity,
    )
    versions = await read_mapping_source_versions(
        ncit_client,
        uberon_client,
        expected_ncit_version=ncit_version,
        expected_uberon_version=uberon_version,
    )
    inventory = inventory or await read_candidate_inventory(ncit_client)
    _require_routed_inventory(inventory)
    records, filler_to_source = await generate_candidates(
        ncit_client,
        uberon_client,
        versions,
        inventory=inventory,
    )
    if await observe_source_identities() != (
        ncit_source_identity,
        uberon_source_identity,
        uberon_serving_identity,
    ):
        raise ValueError("candidate source identity changed during generation")
    fillers = set(filler_to_source)

    await store.upsert_run(
        run_id=rid,
        source=source,
        ncit_version=ncit_version,
        source_version=uberon_version,
    )
    async with fail_run_on_error(store, rid):
        await publish_generation(
            store,
            ncit_client,
            source=source,
            run_id=rid,
            records=records,
            source_metadata=source_metadata,
        )

        report = candidate_coverage_report(
            fillers,
            records,
            filler_to_source,
            inventory,
        )
        report["ncit_source_identity"] = ncit_source_identity
        report["uberon_source_identity"] = uberon_source_identity
        await store.update_run_metrics(rid, report)

    return report


# -- A3.4: Coverage report ----------------------------------------------


def candidate_coverage_report(
    fillers: set[str],
    records: list[SSSOMRecord],
    filler_to_source: dict[str, str],
    inventory: CandidateInventory,
) -> dict[str, Any]:
    """Filler-level ingest metrics, plus the pairs the two sources agree on.

    ``via_xref`` / ``via_lexical_only`` / ``no_candidate`` partition the routed filler
    set, and ``candidate_recall`` is the fraction with any candidate at all. Role-level
    metrics separately expose routed inputs, generated candidates, R105 exclusions,
    distinct unrouted role/filler pairs, and definitions whose unsupported constructors
    remain unknown.

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
    extracted_by_role = Counter(context.source_role for context in inventory.contexts)
    generated_by_role = Counter(
        context.source_role
        for record in records
        for context in record.candidate_contexts
    )
    excluded_by_role = inventory.excluded_by_role

    return {
        "total_fillers": total,
        "via_xref": via_xref,
        "via_lexical_only": via_lexical,
        "no_candidate": no_candidate,
        "candidate_recall": round(recall, 4),
        "source_agreement_pairs": sum(
            r.mapping_justification == COMPOSITE_MATCHING for r in records
        ),
        "extracted_candidates_by_role": dict(sorted(extracted_by_role.items())),
        "generated_candidates_by_role": dict(sorted(generated_by_role.items())),
        "excluded_candidates_by_role": excluded_by_role,
        "excluded_r105_candidates": excluded_by_role.get(_ABNORMAL_CELL_ROLE, 0),
        "unrouted_candidates_by_role": inventory.unrouted_by_role,
        "unknown_definitions_by_reason": inventory.unknown_by_reason,
    }
