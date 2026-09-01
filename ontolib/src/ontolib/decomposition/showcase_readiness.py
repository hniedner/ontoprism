"""Generate local graph-readiness evidence for the enhanced-NCIt showcase."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ontolib.decomposition.enhanced_showcase import (
    SHOWCASE_GRAPH_IRI,
    OverlayAlgorithm,
    RepresentationName,
    ShowcaseDecisionSet,
    SourceRelease,
    activate_showcase_decision_graph,
    load_packaged_showcase_decision_set,
    qualify_showcase_orthogonality,
    require_complete_active_showcase,
)

if TYPE_CHECKING:
    from pathlib import Path

_SHA256 = r"^[0-9a-f]{64}$"
_GIT_HEAD = r"^[0-9a-f]{40,64}$"


class ShowcaseReadinessClient(Protocol):
    async def select(self, query: str) -> list[dict[str, str]]: ...

    async def load(
        self,
        data: bytes,
        *,
        content_type: str,
        graph_iri: str | None = None,
        replace: bool = True,
    ) -> None: ...

    async def update(self, update: str) -> None: ...


def _canonical(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")


class ShowcaseReadinessReport(BaseModel):
    """Identity-bound evidence that the local showcase graph is API-ready."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1]
    showcase_complete: Literal[True]
    local_graph_activated: Literal[True]
    api_ready: Literal[True]
    production_ready: Literal[False]
    scientific_publication_ready: Literal[False]
    equivalence_established: Literal[False]
    nci_adoption_asserted: Literal[False]
    decision_set_identity: str = Field(pattern=_SHA256)
    representation: RepresentationName
    overlay_algorithm: OverlayAlgorithm
    graph_iri: Literal[
        "http://ncicb.nci.nih.gov/xml/owl/EVS/"
        "Thesaurus-decomposed.owl/enhanced-ncit-showcase"
    ]
    source_release: SourceRelease
    concept_count: int
    candidate_count: int
    concept_candidate_counts: dict[str, int]
    disposition_counts: dict[str, int]
    authority_counts: dict[str, int]
    support_counts: dict[str, int]
    producing_command: str
    git_head: str = Field(pattern=_GIT_HEAD)
    report_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _require_identity(self) -> ShowcaseReadinessReport:
        payload = self.model_dump(mode="json", exclude={"report_identity"})
        if self.report_identity != hashlib.sha256(_canonical(payload)).hexdigest():
            raise ValueError("showcase readiness report identity differs")
        return self


def _activation_token(policy: ShowcaseDecisionSet, git_head: str) -> str:
    inputs = {
        "decision_set_identity": policy.decision_set_identity,
        "git_head": git_head,
        "graph_iri": SHOWCASE_GRAPH_IRI,
        "source_release": policy.source_release,
    }
    return hashlib.sha256(_canonical(inputs)).hexdigest()


def _decision_summaries(
    policy: ShowcaseDecisionSet,
) -> tuple[int, dict[str, int], dict[str, int], dict[str, int]]:
    decisions = [
        decision for concept in policy.concepts for decision in concept.decisions
    ]
    dispositions = Counter(str(item.disposition) for item in decisions)
    authorities = Counter(str(item.authority) for item in decisions)
    supports = Counter(str(value) for item in decisions for value in item.support)
    return (
        len(decisions),
        dict(sorted(dispositions.items())),
        dict(sorted(authorities.items())),
        dict(sorted(supports.items())),
    )


def _report_payload(
    policy: ShowcaseDecisionSet, *, git_head: str, producing_command: str
) -> dict[str, object]:
    qualify_showcase_orthogonality(policy)
    candidate_count, dispositions, authorities, supports = _decision_summaries(policy)
    return {
        "schema_version": 1,
        "showcase_complete": True,
        "local_graph_activated": True,
        "api_ready": True,
        "production_ready": False,
        "scientific_publication_ready": False,
        "equivalence_established": False,
        "nci_adoption_asserted": False,
        "decision_set_identity": policy.decision_set_identity,
        "representation": policy.representation,
        "overlay_algorithm": policy.overlay_algorithm,
        "graph_iri": SHOWCASE_GRAPH_IRI,
        "source_release": policy.source_release,
        "concept_count": len(policy.concepts),
        "candidate_count": candidate_count,
        "concept_candidate_counts": {
            concept.code: len(concept.decisions) for concept in policy.concepts
        },
        "disposition_counts": dispositions,
        "authority_counts": authorities,
        "support_counts": supports,
        "producing_command": producing_command,
        "git_head": git_head,
    }


def _write_atomic(output: Path, report: ShowcaseReadinessReport) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


async def _read_and_report(
    client: ShowcaseReadinessClient,
    *,
    output: Path,
    git_head: str,
    producing_command: str,
    policy: ShowcaseDecisionSet | None = None,
) -> ShowcaseReadinessReport:
    output.unlink(missing_ok=True)
    authority = policy or await require_complete_active_showcase(client)
    payload = _report_payload(
        authority, git_head=git_head, producing_command=producing_command
    )
    report = ShowcaseReadinessReport.model_validate(
        {**payload, "report_identity": hashlib.sha256(_canonical(payload)).hexdigest()}
    )
    _write_atomic(output, report)
    return report


async def verify_showcase_readiness(
    client: ShowcaseReadinessClient,
    *,
    output: Path,
    git_head: str,
    producing_command: str,
) -> ShowcaseReadinessReport:
    """Verify the active local graph without mutating any QLever graph."""
    return await _read_and_report(
        client,
        output=output,
        git_head=git_head,
        producing_command=producing_command,
    )


async def activate_showcase_readiness(
    client: ShowcaseReadinessClient,
    *,
    output: Path,
    git_head: str,
    producing_command: str,
) -> ShowcaseReadinessReport:
    """Activate only the local showcase graph, validate readback, and report."""
    output.unlink(missing_ok=True)
    policy = load_packaged_showcase_decision_set()
    activated = await activate_showcase_decision_graph(
        client, run_id=_activation_token(policy, git_head)
    )
    return await _read_and_report(
        client,
        output=output,
        git_head=git_head,
        producing_command=producing_command,
        policy=activated,
    )
