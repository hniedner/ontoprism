"""Assertion support and human history, independent of re-derived engine runs."""

from typing import Annotated, Literal, Self, cast

from pydantic import AwareDatetime, Field, model_validator

from ontolib.common.boundary_models import StrictFrozenBoundaryModel as Model
from ontolib.decomposition.models import (
    AXIS_CODE_PATTERN,
    FILLER_CODE_PATTERN,
    AxisSource,
)
from ontolib.decomposition.source_support import P334Evidence, StatedFillerLocator
from ontolib.decomposition.source_support import PolicyChoice as PolicyChoiceKind

Text = Annotated[str, Field(pattern=r"\S")]
Code = Annotated[str, Field(pattern=r"^C[0-9]+$")]
Role = Annotated[str, Field(pattern=r"^R[0-9]+$")]
Lifecycle = Literal["proposed", "locally-approved", "submitted", "accepted-in-ncit"]


class SiblingMember(Model):
    role_code: Role | None
    filler_code: Code


class SupportContext(StatedFillerLocator):
    siblings: tuple[SiblingMember, ...] = ()


class AssertionPair(Model):
    axis: str = Field(pattern=AXIS_CODE_PATTERN)
    filler_code: str = Field(pattern=FILLER_CODE_PATTERN)


class NormalizedGroup(Model):
    label: Text
    members: tuple[AssertionPair, ...] = Field(min_length=1)


class PolicyChoice(Model):
    kind: PolicyChoiceKind
    policy: Text
    rationale: Text
    state: Literal["open", "corroborated"]


class AssertionSupport(Model):
    axis_source: AxisSource
    contexts: tuple[SupportContext, ...] = ()
    p334: P334Evidence | None = None
    normalized_group: NormalizedGroup | None = None
    policy_choices: tuple[PolicyChoice, ...] = ()

    @model_validator(mode="after")
    def valid_sources(self) -> Self:
        expected = {"role": "restriction", "parent": "genus"}.get(self.axis_source)
        if any(context.kind != expected for context in self.contexts):
            raise ValueError("source contexts disagree with axis source")
        if (self.axis_source == "p334") != (self.p334 is not None):
            raise ValueError("P334 source requires P334 evidence exclusively")
        return self


class EvidenceItem(Model):
    tier: Literal[0, 1, 2, 3]
    kind: Literal["curated-source", "reasoner", "literature"]
    source: Text
    source_release: Text
    locator: Text
    statement: Text
    context_indexes: tuple[Annotated[int, Field(ge=0)], ...] = ()


class ReviewReason(Model):
    kind: Literal["no-evidence", "contradictory", "ambiguous"]
    rationale: Text


class SupportSnapshot(Model):
    source_release: Text
    scope_root: Code
    support: AssertionSupport
    evidence: tuple[EvidenceItem, ...] = ()

    @model_validator(mode="after")
    def valid_references(self) -> Self:
        if any(
            i >= len(self.support.contexts)
            for e in self.evidence
            for i in e.context_indexes
        ):
            raise ValueError("evidence context index is outside support snapshot")
        return self


class HumanDecision(SupportSnapshot):
    actor: Text
    decided_at: AwareDatetime
    rationale: Text
    lifecycle: Lifecycle


class Applicability(Model):
    decision_index: Annotated[int, Field(ge=0)] | None = None
    state: Literal["no-decision", "applicable", "needs-review", "inapplicable"] = (
        "no-decision"
    )
    reason: Text | None = None

    @model_validator(mode="after")
    def valid_state(self) -> Self:
        if (self.state == "no-decision") != (self.decision_index is None):
            raise ValueError("decision reference disagrees with applicability")
        if self.state != "applicable" and self.reason is None:
            raise ValueError("non-applicable assessment requires a reason")
        return self


class LedgerAssessment(SupportSnapshot, AssertionPair):
    concept_code: Code
    status: Literal["provisional", "evidence-supported", "needs-SME"]
    needs_sme_reasons: tuple[ReviewReason, ...] = ()

    @model_validator(mode="after")
    def valid_assessment(self) -> Self:
        if (self.status == "needs-SME") != bool(self.needs_sme_reasons):
            raise ValueError("needs-SME requires reasons exclusively")
        if self.status == "evidence-supported" and (
            not self.evidence
            or any(p.state == "open" for p in self.support.policy_choices)
        ):
            raise ValueError(
                "evidence-supported requires evidence and no open policy choice"
            )
        return self

    @model_validator(mode="after")
    def valid_p334(self) -> Self:
        p334 = self.support.p334
        if p334 and (
            self.axis != "op:HistologyAnchor"
            or p334.path[0] != self.concept_code
            or p334.path[-1] != self.filler_code
        ):
            raise ValueError("P334 path endpoints disagree with assertion")
        return self


class LedgerEntry(LedgerAssessment):
    decisions: tuple[HumanDecision, ...] = ()
    applicability: Applicability
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def valid_history(self) -> Self:
        index = self.applicability.decision_index
        if index is not None and index >= len(self.decisions):
            raise ValueError("decision index is outside history")
        if self.decisions and index is None:
            raise ValueError("history requires an applicability assessment")
        return self

    @model_validator(mode="after")
    def matching_decision(self) -> Self:
        if self.applicability.state == "applicable":
            # Applicability validates that applicable decisions have an index.
            index = cast("int", self.applicability.decision_index)
            decision = self.decisions[index]
            if any(
                getattr(decision, key) != getattr(self, key)
                for key in SupportSnapshot.model_fields
            ):
                raise ValueError("applicable decision must match its support snapshot")
        return self
