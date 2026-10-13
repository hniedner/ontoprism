"""Transactional natural-key ledger, never owned or deleted by engine runs."""

import json
from datetime import UTC, datetime

from sqlalchemy import DateTime, Text, column, select, table, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ontolib.decomposition.evidence_models import (
    Applicability,
    HumanDecision,
    LedgerAssessment,
    LedgerEntry,
    Lifecycle,
    SupportSnapshot,
)

_JSON = ("support", "evidence", "needs_sme_reasons", "decisions", "applicability")
_TABLE = table(
    "assertion_evidence",
    *(
        column(key, JSONB)
        if key in _JSON
        else column(key, DateTime(timezone=True))
        if key == "updated_at"
        else column(key, Text)
        for key in LedgerEntry.model_fields
    ),
)
_HISTORY = {"decisions", "applicability", "updated_at"}


def _entry(row) -> LedgerEntry:
    return LedgerEntry.model_validate_json(json.dumps(dict(row), default=str))


def _applicability(
    index: int, changed: bool, absent_reason: str | None
) -> Applicability:
    if absent_reason:
        return Applicability(
            decision_index=index, state="inapplicable", reason=absent_reason
        )
    if changed:
        return Applicability(
            decision_index=index,
            state="needs-review",
            reason="Historical release, scope, support or evidence changed",
        )
    return Applicability(decision_index=index, state="applicable")


def reassess_entry(
    payload: dict, previous: LedgerEntry, absent_reason: str | None
) -> dict:
    """Apply retained human history consistently to writes and read-only previews."""
    history = previous.decisions
    applicability = Applicability(reason=absent_reason or "No human decision recorded")
    if history:
        latest = history[-1].model_dump(mode="json")
        changed = any(
            latest[key] != payload[key] for key in SupportSnapshot.model_fields
        )
        applicability = _applicability(len(history) - 1, changed, absent_reason)
        if applicability.state == "needs-review":
            reasons = list(payload["needs_sme_reasons"])
            stale = {"kind": "ambiguous", "rationale": applicability.reason}
            if stale not in reasons:
                reasons.append(stale)
            payload.update(
                status="needs-SME",
                needs_sme_reasons=reasons,
            )
    return {
        **payload,
        "decisions": [d.model_dump(mode="json") for d in history],
        "applicability": applicability.model_dump(mode="json"),
        "updated_at": previous.updated_at,
    }


class EvidenceLedger:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def for_concept(self, code: str) -> list[LedgerEntry]:
        async with self.sessions() as session:
            result = await session.execute(
                select(_TABLE)
                .where(_TABLE.c.concept_code == code)
                .order_by(_TABLE.c.axis, _TABLE.c.filler_code)
            )
            return [_entry(row) for row in result.mappings()]

    async def assess(
        self, assessment: LedgerAssessment, *, absent_reason: str | None = None
    ) -> LedgerEntry:
        if absent_reason is not None and not absent_reason.strip():
            raise ValueError("absence requires a nonblank reason")
        return await self._write(assessment, absent_reason=absent_reason)

    async def decide(
        self,
        assessment: LedgerAssessment,
        *,
        actor: str,
        rationale: str,
        lifecycle: Lifecycle,
    ) -> LedgerEntry:
        snapshot = assessment.model_dump(
            include={"source_release", "scope_root", "support", "evidence"}
        )
        decision = HumanDecision(
            **snapshot,
            actor=actor,
            rationale=rationale,
            lifecycle=lifecycle,
            decided_at=datetime.now(UTC),
        )
        return await self._write(assessment, decision=decision)

    async def _write(
        self,
        assessment: LedgerAssessment,
        *,
        decision: HumanDecision | None = None,
        absent_reason: str | None = None,
    ) -> LedgerEntry:
        # Revalidate caller-created models, including unchecked model_copy updates.
        incoming = LedgerAssessment.model_validate_json(assessment.model_dump_json())
        payload = incoming.model_dump(mode="json")
        initial = {
            **payload,
            "decisions": [],
            "applicability": Applicability(
                reason="No human decision recorded"
            ).model_dump(),
            "updated_at": datetime.now(UTC),
        }
        async with self.sessions() as session, session.begin():
            await session.execute(
                insert(_TABLE).values(**initial).on_conflict_do_nothing()
            )
            key = [
                _TABLE.c[name] == payload[name]
                for name in ("concept_code", "axis", "filler_code")
            ]
            result = await session.execute(select(_TABLE).where(*key).with_for_update())
            previous = _entry(result.mappings().one())
            updated = reassess_entry(payload, previous, absent_reason)
            if decision:
                if previous.model_dump(exclude=_HISTORY) != incoming.model_dump():
                    raise ValueError(
                        "decision support is stale; reload the current assessment"
                    )
                updated["decisions"].append(decision.model_dump(mode="json"))
                updated.update(incoming.model_dump(mode="json"))
                updated["applicability"] = {
                    "decision_index": len(updated["decisions"]) - 1,
                    "state": "applicable",
                    "reason": None,
                }
            candidate = _entry(updated)
            if candidate == previous:
                return previous
            updated["updated_at"] = datetime.now(UTC)
            await session.execute(update(_TABLE).where(*key).values(**updated))
            return _entry(updated)
