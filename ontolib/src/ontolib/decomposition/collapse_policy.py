"""Source-qualified exceptions to same-axis projection collapse."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
from contextlib import suppress
from importlib.resources import files
from typing import TYPE_CHECKING, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from ontolib.decomposition.models import RoleRestriction, SourceDefinitionOccurrence


_SHA256 = r"^[0-9a-f]{64}$"
_CODE = r"^C[0-9]+$"
_ROLE = r"^R[0-9]+$"
_RESOURCE = "data/r101-collapse-veto-policy.json"
AUTHORIZED_REGISTRY_IDENTITY = (
    "358b42f8279c067fbd0543572073cd5f6887eea0dc74d148483328c02ceb6975"
)


class CollapsePolicyError(ValueError):
    """The collapse policy is stale, ambiguous, or inapplicable to this source."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class CollapseVeto(_StrictModel):
    """One exact source tuple whose broader endpoint must survive collapse."""

    source_identity: str = Field(pattern=_SHA256)
    concept_code: str = Field(pattern=_CODE)
    role_code: str = Field(pattern=_ROLE)
    anchoring_genus: str = Field(pattern=_CODE)
    normalized_axis: str = Field(pattern=r"^op:[A-Za-z][A-Za-z0-9]*$")
    broader_code: str = Field(pattern=_CODE)
    narrower_code: str = Field(pattern=_CODE)
    occurrence_id: str = Field(pattern=_SHA256)
    atomic_decision_identity: str = Field(pattern=_SHA256)

    @property
    def runtime_key(self) -> tuple[str, str, str, str, str, str, str]:
        return (
            self.source_identity,
            self.concept_code,
            self.role_code,
            self.anchoring_genus,
            self.normalized_axis,
            self.broader_code,
            self.narrower_code,
        )


def _canonical(payload: object) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


class CollapseVetoPolicy(_StrictModel):
    """A complete deterministic runtime collapse-veto policy."""

    schema_version: int = Field(ge=1, le=1)
    registry_identity: str = Field(pattern=_SHA256)
    entries: tuple[CollapseVeto, ...]
    policy_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _validate_identity_and_keys(self) -> Self:
        keys = tuple(entry.runtime_key for entry in self.entries)
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("collapse veto keys must be canonical and unique")
        expected = hashlib.sha256(
            _canonical(self.model_dump(mode="json", exclude={"policy_identity"}))
        ).hexdigest()
        if self.policy_identity != expected:
            raise ValueError("collapse policy identity differs")
        return self

    @classmethod
    def create(
        cls,
        *,
        registry_identity: str,
        entries: tuple[CollapseVeto, ...],
    ) -> CollapseVetoPolicy:
        ordered = tuple(sorted(entries, key=lambda entry: entry.runtime_key))
        payload = {
            "schema_version": 1,
            "registry_identity": registry_identity,
            "entries": tuple(entry.model_dump(mode="json") for entry in ordered),
        }
        return cls.model_validate(
            {
                **payload,
                "policy_identity": hashlib.sha256(_canonical(payload)).hexdigest(),
            }
        )

    def protected_fillers(
        self,
        restrictions: Iterable[RoleRestriction],
        *,
        source_identity: str | None,
        concept_code: str | None,
        route_axis: Callable[[RoleRestriction], str],
    ) -> set[tuple[str, str]]:
        """Return exact ``(axis, broader)`` pairs protected before anchor is lost."""
        return _protected_fillers(
            self.entries,
            restrictions,
            source_identity=source_identity,
            concept_code=concept_code,
            route_axis=route_axis,
        )

    def qualify_live_occurrences(
        self,
        occurrences: Iterable[SourceDefinitionOccurrence],
        *,
        source_identity: str,
    ) -> None:
        """Require every policy key to occur exactly once in the certified source."""
        if not self.entries:
            return
        if source_identity not in {entry.source_identity for entry in self.entries}:
            raise CollapsePolicyError(
                "collapse policy source identity does not match run"
            )
        rows = tuple(occurrences)
        if len({row.occurrence_id for row in rows}) != len(rows):
            raise CollapsePolicyError("duplicate live source occurrence")
        for entry in self.entries:
            _qualify_entry(entry, rows)


def _entry_applies(
    entry: CollapseVeto, source_identity: str | None, concept_code: str
) -> bool:
    return (
        entry.source_identity == source_identity and entry.concept_code == concept_code
    )


def _protected_fillers(
    entries: tuple[CollapseVeto, ...],
    restrictions: Iterable[RoleRestriction],
    *,
    source_identity: str | None,
    concept_code: str | None,
    route_axis: Callable[[RoleRestriction], str],
) -> set[tuple[str, str]]:
    context = _policy_context(entries, source_identity, concept_code)
    if context is None:
        return set()
    rows = tuple(restrictions)
    routed = {(route_axis(row), row.filler_code) for row in rows}
    candidates = (
        _protected_entry(entry, rows, routed, context[0], context[1], route_axis)
        for entry in entries
    )
    return {candidate for candidate in candidates if candidate is not None}


def _policy_context(
    entries: tuple[CollapseVeto, ...],
    source_identity: str | None,
    concept_code: str | None,
) -> tuple[str, str] | None:
    if not entries:
        return None
    if source_identity not in {entry.source_identity for entry in entries}:
        raise CollapsePolicyError("collapse policy source identity does not match run")
    if concept_code is None:
        raise CollapsePolicyError("collapse policy matching requires concept code")
    return source_identity, concept_code


def _protected_entry(
    entry: CollapseVeto,
    rows: tuple[RoleRestriction, ...],
    routed: set[tuple[str, str]],
    source_identity: str,
    concept_code: str,
    route_axis: Callable[[RoleRestriction], str],
) -> tuple[str, str] | None:
    if not _entry_applies(entry, source_identity, concept_code):
        return None
    matches = [row for row in rows if _matches_broader(entry, row, route_axis)]
    if len(matches) > 1:
        raise CollapsePolicyError("duplicate live collapse-veto tuple")
    if not matches or (entry.normalized_axis, entry.narrower_code) not in routed:
        return None
    return entry.normalized_axis, entry.broader_code


def _matches_broader(
    entry: CollapseVeto,
    row: RoleRestriction,
    route_axis: Callable[[RoleRestriction], str],
) -> bool:
    return (
        row.role_code,
        row.anchoring_genus,
        route_axis(row),
        row.filler_code,
    ) == (
        entry.role_code,
        entry.anchoring_genus,
        entry.normalized_axis,
        entry.broader_code,
    )


def _qualify_entry(
    entry: CollapseVeto, rows: tuple[SourceDefinitionOccurrence, ...]
) -> None:
    matches = [row for row in rows if _matches_live_entry(entry, row)]
    if len(matches) != 1:
        raise CollapsePolicyError(
            "collapse policy live source key is missing or ambiguous"
        )
    if matches[0].occurrence_id != entry.occurrence_id:
        raise CollapsePolicyError(
            "collapse policy source occurrence provenance drifted"
        )


def _matches_live_entry(entry: CollapseVeto, row: SourceDefinitionOccurrence) -> bool:
    return (
        row.root_code,
        row.role_code,
        row.anchor_code,
        row.filler_code,
    ) == (
        entry.concept_code,
        entry.role_code,
        entry.anchoring_genus,
        entry.broader_code,
    )


NO_COLLAPSE_VETO_POLICY = CollapseVetoPolicy.create(
    registry_identity="0" * 64,
    entries=(),
)


def load_packaged_collapse_veto_policy() -> CollapseVetoPolicy:
    """Load the wheel-packaged runtime policy through ``importlib.resources``."""
    resource = files("ontolib.decomposition").joinpath(_RESOURCE)
    try:
        payload = resource.read_text(encoding="ascii")
    except OSError as error:
        raise CollapsePolicyError("packaged collapse policy is unavailable") from error
    try:
        return CollapseVetoPolicy.model_validate_json(payload)
    except ValueError as error:
        raise CollapsePolicyError("packaged collapse policy is invalid") from error


def _atomic_write(path: Path, content: bytes) -> None:
    destination = path
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staging = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, destination)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(staging)
        raise


def write_canonical_registry_gzip(path: Path, payload: object) -> None:
    """Atomically write deterministic canonical JSON gzip with no filename or time."""
    content = (
        json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True).encode("ascii")
        + b"\n"
    )
    _atomic_write(path, gzip.compress(content, compresslevel=9, mtime=0))


def write_canonical_policy_json(path: Path, policy: CollapseVetoPolicy) -> None:
    """Atomically write the minimal canonical runtime policy."""
    content = (
        json.dumps(
            policy.model_dump(mode="json"), sort_keys=True, indent=2, ensure_ascii=True
        ).encode("ascii")
        + b"\n"
    )
    _atomic_write(path, content)


def _stage_write(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staging = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(staging)
        raise
    return staging


def _registry_gzip_content(payload: object) -> bytes:
    content = (
        json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True).encode("ascii")
        + b"\n"
    )
    return gzip.compress(content, compresslevel=9, mtime=0)


def _policy_json_content(policy: CollapseVetoPolicy) -> bytes:
    return (
        json.dumps(
            policy.model_dump(mode="json"), sort_keys=True, indent=2, ensure_ascii=True
        ).encode("ascii")
        + b"\n"
    )


def write_collapse_policy_artifacts(
    registry_path: Path,
    policy_path: Path,
    registry_payload: object,
    policy: CollapseVetoPolicy,
) -> None:
    """Publish both validated files with rollback on a reported replace failure.

    Both payloads are fully staged before publication. If either ``os.replace`` reports
    failure, every file replaced by this call is restored byte-for-byte (or removed when
    it did not previously exist). This is pair-consistent error handling, not a claim of
    crash-atomicity across two filesystem replacements.
    """
    destinations = (registry_path, policy_path)
    originals = {
        path: path.read_bytes() if path.exists() else None for path in destinations
    }
    staged: list[str] = []
    try:
        staged.append(
            _stage_write(registry_path, _registry_gzip_content(registry_payload))
        )
        staged.append(_stage_write(policy_path, _policy_json_content(policy)))
        _publish_staged_pair(staged, destinations, originals)
    finally:
        for staging in staged:
            with suppress(FileNotFoundError):
                os.unlink(staging)


def _publish_staged_pair(
    staged: list[str],
    destinations: tuple[Path, Path],
    originals: dict[Path, bytes | None],
) -> None:
    published: list[Path] = []
    try:
        for staging, destination in zip(staged, destinations, strict=True):
            os.replace(staging, destination)
            published.append(destination)
    except BaseException:
        _restore_published(published, originals)
        raise


def _restore_published(
    published: list[Path], originals: dict[Path, bytes | None]
) -> None:
    for destination in reversed(published):
        original = originals[destination]
        if original is None:
            with suppress(FileNotFoundError):
                destination.unlink()
        else:
            _atomic_write(destination, original)
