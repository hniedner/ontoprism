"""Strict qualification of the full-corpus runs compared for the R101 repair."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ontolib.decomposition.corpus_baseline import CorpusBaseline
from ontolib.decomposition.r101_conservation import r101_ledger_query_identity

_SHA256 = r"^[0-9a-f]{64}$"
_STANDARD_FINGERPRINT_SCHEMA_VERSION = 4
_CANARY_CONCEPTS = ("C187445", "C187447", "C53558")
_CONSTITUENT_TRIPLE = re.compile(
    r"^<http://ncicb\.nci\.nih\.gov/xml/owl/EVS/Thesaurus\.owl#(?P<concept>C[0-9]+)> "
    r"<https://w3id\.org/ontoprism/vocab#hasConstituent>\s*"
    r"\[<https://w3id\.org/ontoprism/vocab#axis> "
    r"<https://w3id\.org/ontoprism/vocab#(?P<axis>[A-Za-z][A-Za-z0-9]*)> ; "
    r"<https://w3id\.org/ontoprism/vocab#filler> "
    r"<http://ncicb\.nci\.nih\.gov/xml/owl/EVS/Thesaurus\.owl#"
    r"(?P<filler>C[0-9]+)> ; .+\] \.\n$"
)


class R101ComparatorValidationError(ValueError):
    """The supplied runs cannot support a controlled full-corpus comparison."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def _identity(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


class ComparatorFingerprint(_StrictModel):
    """Immutable persisted fields needed to compare one historical or current run."""

    schema_version: Literal[4, 5]
    source_identity: str = Field(pattern=_SHA256)
    collapse_policy_identity: str = Field(pattern=_SHA256)
    routing_implementation_identity: str | None = Field(default=None, pattern=_SHA256)
    branch: Literal["neoplasm", "disease"]
    scope_root: str = Field(pattern=r"^C[0-9]+$")
    scope_version: str = Field(min_length=1)
    semantic_types: tuple[str, ...]
    worklist: tuple[str, ...]
    total_limit: int | None = Field(default=None, gt=0)
    sample_manifest_identity: str | None = Field(default=None, pattern=_SHA256)
    algorithm_version: str = Field(min_length=1)
    config_version: str = Field(min_length=1)
    walker_max_depth: int = Field(gt=0)
    output_mode: Literal["none", "file"]
    load_mode: Literal["none", "named-graph"]
    emitted_at: AwareDatetime

    @model_validator(mode="after")
    def _canonical_collections_and_scope(self) -> Self:
        if self.semantic_types != tuple(sorted(set(self.semantic_types))):
            raise ValueError("semantic types are not canonical")
        if not self.worklist or len(self.worklist) != len(set(self.worklist)):
            raise ValueError("worklist must be nonempty and unique")
        expected_root = "C3262" if self.branch == "neoplasm" else "C2991"
        if self.scope_root != expected_root:
            raise ValueError("scope root does not match branch")
        return self

    @property
    def identity(self) -> str:
        return _identity(self.model_dump(mode="json", exclude_unset=True))


class ComparatorRun(_StrictModel):
    """Completed published run fields read without changing historical rows."""

    run_id: str = Field(min_length=1)
    ncit_version: str = Field(min_length=1)
    fingerprint: ComparatorFingerprint
    fingerprint_identity: str = Field(pattern=_SHA256)
    representation_identity: str = Field(pattern=_SHA256)
    publication_artifact_path: str = Field(min_length=1)

    @model_validator(mode="after")
    def _fingerprint_identity_matches(self) -> Self:
        if self.fingerprint_identity != self.fingerprint.identity:
            raise ValueError("fingerprint identity does not match fingerprint content")
        return self


class ComparatorControl(_StrictModel):
    source_identity: str = Field(pattern=_SHA256)
    ontology_release: str = Field(min_length=1)
    schema_version: Literal[4]
    branch: Literal["neoplasm"]
    scope_root: Literal["C3262"]
    scope_version: str = Field(min_length=1)
    semantic_types: tuple[str, ...]
    worklist: tuple[str, ...]
    worklist_identity: str = Field(pattern=_SHA256)
    worklist_count: int = Field(gt=0)
    total_limit: None
    sample_manifest_identity: None
    collapse_policy_identity: str = Field(pattern=_SHA256)
    config_version: str = Field(min_length=1)
    walker_max_depth: int = Field(gt=0)
    output_mode: Literal["file"]
    load_mode: Literal["none", "named-graph"]


class ComparatorRunBinding(_StrictModel):
    run_id: str = Field(min_length=1)
    fingerprint_identity: str = Field(pattern=_SHA256)
    representation_identity: str = Field(pattern=_SHA256)
    publication_artifact_path: str = Field(min_length=1)
    artifact_path: str = Field(min_length=1)
    artifact_identity: str = Field(pattern=_SHA256)
    algorithm_version: Literal["decomposition-v4", "decomposition-v5"]
    routing_implementation_identity: str = Field(
        pattern=r"^(?:[0-9a-f]{64}|not-recorded)$"
    )

    @model_validator(mode="after")
    def _routing_identity_matches_algorithm(self) -> Self:
        if (
            self.algorithm_version == "decomposition-v5"
            and self.routing_implementation_identity == "not-recorded"
        ):
            raise ValueError("v5 routing implementation identity is not recorded")
        return self


class ArtifactConstituentTriple(_StrictModel):
    concept_code: str = Field(pattern=r"^C[0-9]+$")
    axis: str = Field(pattern=r"^op:[A-Za-z][A-Za-z0-9]*$")
    filler_code: str = Field(pattern=r"^C[0-9]+$")


class R101ComparatorQualification(_StrictModel):
    schema_version: Literal[1]
    control: ComparatorControl
    old: ComparatorRunBinding
    new: ComparatorRunBinding
    shared_canary_constituents: tuple[ArtifactConstituentTriple, ...]
    old_baseline_identity: str = Field(pattern=_SHA256)
    query_identity: str = Field(pattern=_SHA256)
    qualification_identity: str = Field(pattern=_SHA256)

    @model_validator(mode="after")
    def _identity_matches(self) -> Self:
        _validate_qualification(self)
        return self


def _validate_qualification(qualification: R101ComparatorQualification) -> None:
    canary_concepts = tuple(
        sorted({row.concept_code for row in qualification.shared_canary_constituents})
    )
    payload = qualification.model_dump(mode="json", exclude={"qualification_identity"})
    requirements = (
        (
            qualification.old.algorithm_version == "decomposition-v4",
            "old comparator algorithm must be decomposition-v4",
        ),
        (
            qualification.new.algorithm_version == "decomposition-v5",
            "new comparator algorithm must be decomposition-v5",
        ),
        (
            qualification.old.run_id != qualification.new.run_id,
            "comparator runs must be distinct",
        ),
        (
            canary_concepts == tuple(sorted(_CANARY_CONCEPTS)),
            "comparator canary inventory is incomplete",
        ),
        (
            qualification.qualification_identity == _identity(payload),
            "comparator qualification identity differs",
        ),
    )
    for valid, message in requirements:
        if not valid:
            raise ValueError(message)


def _artifact_identity(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise R101ComparatorValidationError(
            "comparator artifact must be a regular file"
        )
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canary_constituents(path: Path) -> tuple[ArtifactConstituentTriple, ...]:
    rows: set[tuple[str, str, str]] = set()
    with path.open(encoding="utf-8", newline="") as stream:
        for line in stream:
            match = _CONSTITUENT_TRIPLE.fullmatch(line)
            if match is None or match.group("concept") not in _CANARY_CONCEPTS:
                continue
            rows.add(
                (
                    match.group("concept"),
                    f"op:{match.group('axis')}",
                    match.group("filler"),
                )
            )
    return tuple(
        ArtifactConstituentTriple(concept_code=concept, axis=axis, filler_code=filler)
        for concept, axis, filler in sorted(rows)
    )


def _shared_canary_constituents(
    old_artifact: Path, new_artifact: Path
) -> tuple[ArtifactConstituentTriple, ...]:
    old_rows = _canary_constituents(old_artifact)
    new_rows = _canary_constituents(new_artifact)
    missing = _missing_canary_concepts(old_rows, new_rows)
    if missing:
        raise R101ComparatorValidationError(
            "artifact constituent proof requires canaries C187445, C187447, C53558; "
            f"missing {', '.join(missing)}"
        )
    if old_rows != new_rows:
        raise R101ComparatorValidationError(
            "alleged non-R101 artifact additions differ between full artifacts"
        )
    return old_rows


def _missing_canary_concepts(
    old_rows: tuple[ArtifactConstituentTriple, ...],
    new_rows: tuple[ArtifactConstituentTriple, ...],
) -> tuple[str, ...]:
    old_concepts = {row.concept_code for row in old_rows}
    new_concepts = {row.concept_code for row in new_rows}
    return tuple(sorted(set(_CANARY_CONCEPTS).difference(old_concepts & new_concepts)))


def _require_equal(label: str, old: object, new: object) -> None:
    if old != new:
        raise R101ComparatorValidationError(f"comparator {label} differs")


def _qualify_control(old: ComparatorRun, new: ComparatorRun) -> ComparatorControl:
    old_fingerprint = old.fingerprint
    new_fingerprint = new.fingerprint
    controls = (
        (
            "source identity",
            old_fingerprint.source_identity,
            new_fingerprint.source_identity,
        ),
        ("release", old.ncit_version, new.ncit_version),
        ("branch", old_fingerprint.branch, new_fingerprint.branch),
        ("scope root", old_fingerprint.scope_root, new_fingerprint.scope_root),
        ("scope version", old_fingerprint.scope_version, new_fingerprint.scope_version),
        (
            "semantic types",
            old_fingerprint.semantic_types,
            new_fingerprint.semantic_types,
        ),
        ("worklist", old_fingerprint.worklist, new_fingerprint.worklist),
        ("total limit", old_fingerprint.total_limit, new_fingerprint.total_limit),
        (
            "sample manifest",
            old_fingerprint.sample_manifest_identity,
            new_fingerprint.sample_manifest_identity,
        ),
        (
            "schema version",
            old_fingerprint.schema_version,
            new_fingerprint.schema_version,
        ),
        (
            "collapse policy",
            old_fingerprint.collapse_policy_identity,
            new_fingerprint.collapse_policy_identity,
        ),
        (
            "configuration",
            old_fingerprint.config_version,
            new_fingerprint.config_version,
        ),
        (
            "walker depth",
            old_fingerprint.walker_max_depth,
            new_fingerprint.walker_max_depth,
        ),
        ("output mode", old_fingerprint.output_mode, new_fingerprint.output_mode),
        ("load mode", old_fingerprint.load_mode, new_fingerprint.load_mode),
    )
    for label, old_value, new_value in controls:
        _require_equal(label, old_value, new_value)
    if old_fingerprint.schema_version != _STANDARD_FINGERPRINT_SCHEMA_VERSION:
        raise R101ComparatorValidationError(
            "comparator requires standard schema version 4"
        )
    if old_fingerprint.branch != "neoplasm":
        raise R101ComparatorValidationError("comparator requires the neoplasm branch")
    if old_fingerprint.total_limit is not None:
        raise R101ComparatorValidationError("full comparator cannot use a total limit")
    if old_fingerprint.sample_manifest_identity is not None:
        raise R101ComparatorValidationError(
            "full comparator cannot use a sample manifest"
        )
    if old_fingerprint.output_mode != "file":
        raise R101ComparatorValidationError("full comparator requires file output mode")
    return ComparatorControl(
        source_identity=old_fingerprint.source_identity,
        ontology_release=old.ncit_version,
        schema_version=4,
        branch="neoplasm",
        scope_root="C3262",
        scope_version=old_fingerprint.scope_version,
        semantic_types=old_fingerprint.semantic_types,
        worklist=old_fingerprint.worklist,
        worklist_identity=_identity(old_fingerprint.worklist),
        worklist_count=len(old_fingerprint.worklist),
        total_limit=None,
        sample_manifest_identity=None,
        collapse_policy_identity=old_fingerprint.collapse_policy_identity,
        config_version=old_fingerprint.config_version,
        walker_max_depth=old_fingerprint.walker_max_depth,
        output_mode="file",
        load_mode=old_fingerprint.load_mode,
    )


def _require_baseline(old: ComparatorRun, baseline: CorpusBaseline) -> None:
    expected = (
        old.run_id,
        old.fingerprint.source_identity,
        old.ncit_version,
        old.fingerprint_identity,
        old.representation_identity,
        len(old.fingerprint.worklist),
    )
    observed = (
        baseline.run_id,
        baseline.source_identity,
        baseline.ontology_release,
        baseline.run_fingerprint_identity,
        baseline.representation_identity,
        baseline.worklist_count,
    )
    if (
        observed != expected
        or baseline.artifact_identity != old.representation_identity
    ):
        raise R101ComparatorValidationError("old baseline does not bind comparator run")


def _binding(
    run: ComparatorRun,
    artifact: Path,
    *,
    expected_algorithm: Literal["decomposition-v4", "decomposition-v5"],
) -> ComparatorRunBinding:
    if run.fingerprint.algorithm_version != expected_algorithm:
        raise R101ComparatorValidationError(
            f"comparator {expected_algorithm} algorithm version differs"
        )
    artifact_identity = _artifact_identity(artifact)
    if artifact_identity != run.representation_identity:
        raise R101ComparatorValidationError("comparator artifact identity differs")
    routing_identity = run.fingerprint.routing_implementation_identity
    if expected_algorithm == "decomposition-v5" and routing_identity is None:
        raise R101ComparatorValidationError(
            "v5 routing implementation identity is missing"
        )
    return ComparatorRunBinding(
        run_id=run.run_id,
        fingerprint_identity=run.fingerprint_identity,
        representation_identity=run.representation_identity,
        publication_artifact_path=run.publication_artifact_path,
        artifact_path=artifact.as_posix(),
        artifact_identity=artifact_identity,
        algorithm_version=expected_algorithm,
        routing_implementation_identity=routing_identity or "not-recorded",
    )


def qualify_r101_comparator(
    *,
    old_run: ComparatorRun,
    new_run: ComparatorRun,
    old_baseline: CorpusBaseline,
    old_artifact: Path,
    new_artifact: Path,
) -> R101ComparatorQualification:
    """Qualify a v4/full to v5/full pair while treating code identities as variables."""
    control = _qualify_control(old_run, new_run)
    _require_baseline(old_run, old_baseline)
    old_binding = _binding(old_run, old_artifact, expected_algorithm="decomposition-v4")
    new_binding = _binding(new_run, new_artifact, expected_algorithm="decomposition-v5")
    shared_canary_constituents = _shared_canary_constituents(old_artifact, new_artifact)
    if (
        old_binding.routing_implementation_identity
        == new_binding.routing_implementation_identity
    ):
        raise R101ComparatorValidationError(
            "routing implementation identities must be independent variables"
        )
    identity_payload: dict[str, object] = {
        "schema_version": 1,
        "control": control.model_dump(mode="json"),
        "old": old_binding.model_dump(mode="json"),
        "new": new_binding.model_dump(mode="json"),
        "shared_canary_constituents": [
            row.model_dump(mode="json") for row in shared_canary_constituents
        ],
        "old_baseline_identity": old_baseline.baseline_identity,
        "query_identity": r101_ledger_query_identity(),
    }
    return R101ComparatorQualification.model_validate(
        {
            "schema_version": 1,
            "control": control,
            "old": old_binding,
            "new": new_binding,
            "shared_canary_constituents": shared_canary_constituents,
            "old_baseline_identity": old_baseline.baseline_identity,
            "query_identity": r101_ledger_query_identity(),
            "qualification_identity": _identity(identity_payload),
        }
    )


def write_r101_comparator_qualification(
    path: Path, qualification: R101ComparatorQualification
) -> None:
    """Atomically write one canonical comparator qualification document."""
    payload = (
        json.dumps(
            qualification.model_dump(mode="json"),
            sort_keys=True,
            indent=2,
            ensure_ascii=True,
        )
        + "\n"
    ).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, staging = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(staging)
        raise


def load_r101_comparator_qualification(path: Path) -> R101ComparatorQualification:
    """Load and validate one exact comparator qualification document."""
    if path.is_symlink() or not path.is_file():
        raise R101ComparatorValidationError(
            "comparator qualification must be a regular file"
        )
    return R101ComparatorQualification.model_validate_json(path.read_bytes())
