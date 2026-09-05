"""Collection-driven, file-level partitions for the fixed Python CI lanes."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import tempfile
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

ALGORITHM_VERSION = "sha256-mod-v1"
RECEIPT_SCHEMA_VERSION = 1
SHARD_COUNT = 2
_ENV_PREFIX = "ONTOPRISM_TEST_PARTITION_"
_QLEVER_FIXTURE_FRAGMENT = "qlever"
Lane = Literal["backend", "integration"]
Phase = Literal["collect", "execute"]
ShardId = Literal["0", "1", "qlever", "postgres"]


def _usage_error(message: str) -> Exception:
    pytest_module = importlib.import_module("pytest")
    return pytest_module.UsageError(message)


class _Document(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class CollectionRecord(_Document):
    """The collection facts needed to select one file-level partition."""

    nodeid: str
    path: str
    markers: frozenset[str]
    fixtures: frozenset[str]


class LaneSelector(_Document):
    """One lane's pytest expression and equivalent marker-set rule."""

    lane: Lane
    marker_expression: str
    required_markers: frozenset[str]
    excluded_markers: frozenset[str]


LANE_SELECTORS = (
    LaneSelector(
        lane="backend",
        marker_expression="not integration",
        required_markers=frozenset(),
        excluded_markers=frozenset({"integration"}),
    ),
    LaneSelector(
        lane="integration",
        marker_expression=(
            "integration and not full_store and not full_build and not slow"
        ),
        required_markers=frozenset({"integration"}),
        excluded_markers=frozenset({"full_store", "full_build", "slow"}),
    ),
)


class PartitionSpec(_Document):
    """One correlated fixed partition and its emitted evidence names."""

    lane: Lane
    shard_id: ShardId
    shard_index: Annotated[int, Field(ge=0)]
    shard_count: Annotated[int, Field(gt=0)]
    layer: str
    coverage_stem: str
    artifact_name: str
    needs_robot: bool
    needs_jena: bool

    @model_validator(mode="after")
    def validate_correlation(self) -> Self:
        if self.lane == "backend":
            if self.shard_id not in {"0", "1"}:
                raise ValueError("partition lane/shard correlation is invalid")
            expected_index = int(self.shard_id)
            expected_stem = f"unit-{self.shard_id}"
            expected_artifact = f"coverage-backend-{expected_index + 1}"
        else:
            if self.shard_id not in {"qlever", "postgres"}:
                raise ValueError("partition lane/shard correlation is invalid")
            expected_index = 0 if self.shard_id == "qlever" else 1
            expected_stem = f"integration-{self.shard_id}"
            expected_artifact = f"coverage-integration-{self.shard_id}"
        if (
            self.shard_count != SHARD_COUNT
            or self.shard_index != expected_index
            or self.layer != f"python-{expected_stem}"
            or self.coverage_stem != expected_stem
            or self.artifact_name != expected_artifact
            or self.needs_robot != (self.shard_id == "postgres")
            or self.needs_jena != (self.shard_id == "qlever")
        ):
            raise ValueError("partition lane/shard correlation is invalid")
        return self

    @property
    def coverage_name(self) -> str:
        return f".coverage.{self.coverage_stem}"

    @property
    def identity_name(self) -> str:
        return f"coverage-{self.coverage_stem}.identity.json"

    @property
    def receipt_name(self) -> str:
        return f"partition-{self.lane}-{self.shard_id}.json"

    @property
    def coverage_xml_name(self) -> str | None:
        return f"coverage-{self.coverage_stem}.xml" if self.lane == "backend" else None

    @property
    def artifact_file_names(self) -> frozenset[str]:
        return frozenset(
            name
            for name in (
                self.coverage_name,
                self.identity_name,
                self.receipt_name,
                self.coverage_xml_name,
            )
            if name is not None
        )


PARTITION_SPECS = (
    PartitionSpec(
        lane="backend",
        shard_id="0",
        shard_index=0,
        shard_count=2,
        layer="python-unit-0",
        coverage_stem="unit-0",
        artifact_name="coverage-backend-1",
        needs_robot=False,
        needs_jena=False,
    ),
    PartitionSpec(
        lane="backend",
        shard_id="1",
        shard_index=1,
        shard_count=2,
        layer="python-unit-1",
        coverage_stem="unit-1",
        artifact_name="coverage-backend-2",
        needs_robot=False,
        needs_jena=False,
    ),
    PartitionSpec(
        lane="integration",
        shard_id="qlever",
        shard_index=0,
        shard_count=2,
        layer="python-integration-qlever",
        coverage_stem="integration-qlever",
        artifact_name="coverage-integration-qlever",
        needs_robot=False,
        needs_jena=True,
    ),
    PartitionSpec(
        lane="integration",
        shard_id="postgres",
        shard_index=1,
        shard_count=2,
        layer="python-integration-postgres",
        coverage_stem="integration-postgres",
        artifact_name="coverage-integration-postgres",
        needs_robot=True,
        needs_jena=False,
    ),
)


def partition_spec(lane: Lane, shard_id: str) -> PartitionSpec:
    try:
        return next(
            spec
            for spec in PARTITION_SPECS
            if spec.lane == lane and spec.shard_id == shard_id
        )
    except StopIteration as exc:
        raise ValueError(f"invalid fixed partition {lane}/{shard_id}") from exc


def lane_selector(lane: Lane) -> LaneSelector:
    return next(selector for selector in LANE_SELECTORS if selector.lane == lane)


class PartitionEnvironment(_Document):
    """Validated selector environment shared by controller and xdist workers."""

    lane: Lane
    shard_id: ShardId
    shard_count: int
    receipt: Path
    phase: Phase
    fixed_roots: bool

    @model_validator(mode="after")
    def validate_partition(self) -> Self:
        spec = partition_spec(self.lane, self.shard_id)
        if self.shard_count != spec.shard_count:
            raise ValueError("test partition count must be 2")
        return self

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> Self:
        def required(name: str) -> str:
            value = environ.get(f"{_ENV_PREFIX}{name}")
            if not value:
                raise ValueError(f"test partition {name} is required")
            return value

        lane_value = required("LANE")
        if lane_value not in {"backend", "integration"}:
            raise ValueError("invalid test partition lane")
        phase = required("PHASE")
        if phase not in {"collect", "execute"}:
            raise ValueError("test partition phase must be collect or execute")
        fixed_roots = required("FIXED_ROOTS")
        if fixed_roots not in {"0", "1"}:
            raise ValueError("test partition FIXED_ROOTS must be exactly 0 or 1")
        count = required("COUNT")
        if count != str(SHARD_COUNT):
            raise ValueError("test partition count must be 2")
        return cls(
            lane=cast("Lane", lane_value),
            shard_id=cast("ShardId", required("SHARD")),
            shard_count=SHARD_COUNT,
            receipt=Path(required("RECEIPT")),
            phase=cast("Phase", phase),
            fixed_roots=fixed_roots == "1",
        )


class IntegrationClassification(_Document):
    """Resource and tool capabilities derived from collection plus the manifest."""

    manifest_sha256: str
    qlever_files: tuple[str, ...]
    postgres_files: tuple[str, ...]
    robot_files: tuple[str, ...]
    jena_files: tuple[str, ...]
    evidence_sha256: str

    @classmethod
    def from_collection(
        cls,
        records: Sequence[CollectionRecord],
        manifest_path: Path,
    ) -> Self:
        if not records:
            raise ValueError("integration inventory is empty")
        manifest_bytes = manifest_path.read_bytes()
        raw = tomllib.loads(manifest_bytes.decode("utf-8"))
        entries = raw.get("mutator")
        if not isinstance(entries, list):
            raise ValueError("integration mutator manifest has no declarations")
        manifest_qlever_files: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("integration mutator declaration must be a table")
            path = entry.get("path")
            fixtures = entry.get("fixtures")
            if (
                not isinstance(path, str)
                or not isinstance(fixtures, list)
                or not all(isinstance(fixture, str) for fixture in fixtures)
            ):
                raise ValueError("integration mutator declaration is malformed")
            if any(_QLEVER_FIXTURE_FRAGMENT in fixture for fixture in fixtures):
                manifest_qlever_files.add(path)

        inventory = {record.path for record in records}
        collected_qlever_files = {
            record.path
            for record in records
            if any(_QLEVER_FIXTURE_FRAGMENT in fixture for fixture in record.fixtures)
        }
        qlever_files = tuple(
            sorted(inventory & (manifest_qlever_files | collected_qlever_files))
        )
        postgres_files = tuple(sorted(inventory - set(qlever_files)))
        robot_files = tuple(
            sorted(
                {
                    record.path
                    for record in records
                    if "requires_robot" in record.markers
                }
            )
        )
        jena_files = qlever_files
        if not robot_files:
            raise ValueError("ROBOT file assignment must be nonempty")
        if not set(robot_files) <= set(postgres_files):
            raise ValueError("ROBOT files must be contained by the postgres shard")
        evidence = {
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "inventory": sorted(inventory),
            "manifest_qlever_files": sorted(manifest_qlever_files & inventory),
            "collected_qlever_files": sorted(collected_qlever_files),
            "robot_files": list(robot_files),
            "jena_files": list(jena_files),
        }
        evidence_sha256 = _digest_json(evidence)
        return cls(
            manifest_sha256=evidence["manifest_sha256"],
            qlever_files=qlever_files,
            postgres_files=postgres_files,
            robot_files=robot_files,
            jena_files=jena_files,
            evidence_sha256=evidence_sha256,
        )


class PartitionReceipt(_Document):
    """Proof that one shard selected a self-consistent subset of one inventory."""

    schema_version: int
    lane: Lane
    shard_id: ShardId
    shard_index: Annotated[int, Field(ge=0)]
    shard_count: Annotated[int, Field(gt=0)]
    algorithm_version: str
    full_inventory: tuple[str, ...]
    full_inventory_sha256: str
    selected_nodeids: tuple[str, ...]
    selected_sha256: str
    selected_count: Annotated[int, Field(gt=0)]
    integration_classification: IntegrationClassification | None = None

    @model_validator(mode="after")
    def validate_internal_digests(self) -> Self:  # noqa: C901, PLR0912
        if self.schema_version != RECEIPT_SCHEMA_VERSION:
            raise ValueError("unsupported partition receipt schema")
        if self.algorithm_version != ALGORITHM_VERSION:
            raise ValueError("unsupported partition algorithm")
        if self.shard_count != SHARD_COUNT:
            raise ValueError("partition receipt must describe exactly two shards")
        if self.shard_index >= self.shard_count:
            raise ValueError("partition receipt shard index is out of range")
        try:
            spec = partition_spec(self.lane, self.shard_id)
        except ValueError as exc:
            raise ValueError(
                "partition receipt lane/shard correlation is invalid"
            ) from exc
        if self.shard_index != spec.shard_index:
            raise ValueError(
                "partition receipt lane/shard/index correlation is invalid"
            )
        if self.full_inventory != tuple(sorted(set(self.full_inventory))):
            raise ValueError("full inventory must be sorted and unique")
        if self.selected_nodeids != tuple(sorted(set(self.selected_nodeids))):
            raise ValueError("selected nodeids must be sorted and unique")
        if self.selected_count != len(self.selected_nodeids):
            raise ValueError("selected count does not match selected nodeids")
        if self.full_inventory_sha256 != _digest_strings(self.full_inventory):
            raise ValueError("full inventory digest does not match inventory")
        if self.selected_sha256 != _digest_strings(self.selected_nodeids):
            raise ValueError("selected digest does not match selected nodeids")
        if not set(self.selected_nodeids) <= set(self.full_inventory):
            raise ValueError("selected nodeids are outside the full inventory")
        if (self.lane == "integration") != (
            self.integration_classification is not None
        ):
            raise ValueError("integration classification presence disagrees with lane")
        return self


def _digest_json(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _digest_strings(values: Sequence[str]) -> str:
    return _digest_json(list(values))


def assign_backend_modules(
    nodeids: Sequence[str], shard_index: int, shard_count: int
) -> tuple[str, ...]:
    """Select whole test modules with SHA-256's first 8 bytes, big-endian."""
    if shard_count != SHARD_COUNT:
        raise ValueError("backend partition requires exactly two shards")
    if shard_index not in range(shard_count):
        raise ValueError("backend shard index is out of range")
    selected = tuple(
        sorted(
            {
                nodeid
                for nodeid in nodeids
                if int.from_bytes(
                    hashlib.sha256(nodeid.partition("::")[0].encode()).digest()[:8],
                    "big",
                )
                % shard_count
                == shard_index
            }
        )
    )
    if not selected:
        raise ValueError(f"empty backend shard {shard_index}/{shard_count}")
    return selected


def build_receipt(
    *,
    lane: Lane,
    shard_id: ShardId,
    shard_index: int,
    shard_count: int,
    full_inventory: Sequence[str],
    selected_nodeids: Sequence[str],
    classification: IntegrationClassification | None,
) -> PartitionReceipt:
    full = tuple(sorted(set(full_inventory)))
    selected = tuple(sorted(set(selected_nodeids)))
    return PartitionReceipt(
        schema_version=RECEIPT_SCHEMA_VERSION,
        lane=lane,
        shard_id=shard_id,
        shard_index=shard_index,
        shard_count=shard_count,
        algorithm_version=ALGORITHM_VERSION,
        full_inventory=full,
        full_inventory_sha256=_digest_strings(full),
        selected_nodeids=selected,
        selected_sha256=_digest_strings(selected),
        selected_count=len(selected),
        integration_classification=classification,
    )


def validate_partition_receipts(  # noqa: C901, PLR0912
    receipts: Sequence[PartitionReceipt],
    *,
    lane: Lane,
) -> None:
    """Require exact shard identities and an exact disjoint inventory union."""
    expected = tuple(spec.shard_id for spec in PARTITION_SPECS if spec.lane == lane)
    if len(receipts) != len(expected):
        raise ValueError(f"missing partition receipt for {lane}")
    if tuple(receipt.shard_id for receipt in receipts) != expected:
        raise ValueError(f"unexpected or duplicate {lane} shard IDs")
    if any(receipt.lane != lane for receipt in receipts):
        raise ValueError("partition receipt lane mismatch")
    if tuple(receipt.shard_index for receipt in receipts) != tuple(
        range(len(expected))
    ):
        raise ValueError("partition receipt indices are not exact")
    inventories = {receipt.full_inventory for receipt in receipts}
    digests = {receipt.full_inventory_sha256 for receipt in receipts}
    if len(inventories) != 1 or len(digests) != 1:
        raise ValueError("partition full inventories or digests differ")
    selected_sets = [set(receipt.selected_nodeids) for receipt in receipts]
    for index, selected in enumerate(selected_sets):
        if any(selected & other for other in selected_sets[index + 1 :]):
            raise ValueError("partition selections overlap")
    union = set().union(*selected_sets)
    if union != set(receipts[0].full_inventory):
        raise ValueError("partition selections do not exactly cover full inventory")
    if lane == "integration":
        classification = receipts[0].integration_classification
        if any(
            receipt.integration_classification != classification
            for receipt in receipts[1:]
        ):
            raise ValueError(
                "integration classification evidence differs between shards"
            )
        if classification is None:
            raise ValueError("integration classification evidence is missing")
        if not classification.robot_files:
            raise ValueError("ROBOT file assignment must be nonempty")
        if not set(classification.robot_files) <= set(classification.postgres_files):
            raise ValueError("ROBOT files must be contained by the postgres shard")
        if classification.jena_files != classification.qlever_files:
            raise ValueError("Jena files must exactly equal the qlever shard files")
        selected_files = [
            {nodeid.partition("::")[0] for nodeid in receipt.selected_nodeids}
            for receipt in receipts
        ]
        if selected_files != [
            set(classification.qlever_files),
            set(classification.postgres_files),
        ]:
            raise ValueError(
                "integration selections disagree with boundary classification"
            )


def _record(item: Any, root: Path) -> CollectionRecord:
    absolute = Path(str(item.path)).resolve()
    try:
        path = absolute.relative_to(root).as_posix()
    except ValueError:
        path = absolute.as_posix()
    suffix = item.nodeid.partition("::")[2]
    nodeid = f"{path}::{suffix}" if suffix else path
    return CollectionRecord(
        nodeid=nodeid,
        path=path,
        markers=frozenset(marker.name for marker in item.iter_markers()),
        fixtures=frozenset(getattr(item, "fixturenames", ())),
    )


def _eligible(record: CollectionRecord, lane: Lane) -> bool:
    selector = lane_selector(lane)
    return selector.required_markers <= record.markers and not (
        selector.excluded_markers & record.markers
    )


def _selection(
    records: Sequence[CollectionRecord], lane: Lane, shard: str, root: Path
) -> tuple[tuple[CollectionRecord, ...], IntegrationClassification | None, int]:
    eligible = tuple(record for record in records if _eligible(record, lane))
    if not eligible:
        raise _usage_error(f"{lane} full inventory is empty")
    try:
        spec = partition_spec(lane, shard)
    except ValueError as exc:
        raise _usage_error(str(exc)) from exc
    if lane == "backend":
        index = spec.shard_index
        selected_ids = set(
            assign_backend_modules([r.nodeid for r in eligible], index, SHARD_COUNT)
        )
        return tuple(r for r in eligible if r.nodeid in selected_ids), None, index
    classification = IntegrationClassification.from_collection(
        eligible, root / "test_support/integration_mutators.toml"
    )
    paths = (
        set(classification.qlever_files)
        if shard == "qlever"
        else set(classification.postgres_files)
    )
    selected = tuple(record for record in eligible if record.path in paths)
    if not selected:
        raise _usage_error(f"empty integration shard {shard}")
    return selected, classification, spec.shard_index


def _write_receipt(path: Path, receipt: PartitionReceipt) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        json.dump(receipt.model_dump(mode="json"), stream, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    temporary.replace(path)


def load_receipt(path: Path) -> PartitionReceipt:
    return PartitionReceipt.model_validate_json(path.read_text(encoding="utf-8"))


def validate_artifacts(
    root: Path,
    *,
    validate_receipts: bool = True,
    validate_identities: bool = True,
) -> None:
    """Require the four exact artifact directories and their exact regular files."""
    root = root.resolve()
    expected_directories = {spec.artifact_name for spec in PARTITION_SPECS}
    actual_directories = {
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and entry.name.startswith("coverage-")
    }
    unexpected = actual_directories - expected_directories
    if unexpected:
        raise ValueError(
            f"unexpected coverage artifact directories: {sorted(unexpected)}"
        )
    for spec in PARTITION_SPECS:
        path = root / spec.artifact_name
        actual = {entry.name for entry in path.iterdir()} if path.is_dir() else set()
        if actual != spec.artifact_file_names or not all(
            (path / name).is_file() for name in spec.artifact_file_names
        ):
            raise ValueError(
                f"coverage artifact {spec.artifact_name} files mismatch: "
                f"expected {sorted(spec.artifact_file_names)}, got {sorted(actual)}"
            )
    if validate_receipts:
        for lane in ("backend", "integration"):
            receipts = tuple(
                load_receipt(root / spec.artifact_name / spec.receipt_name)
                for spec in PARTITION_SPECS
                if spec.lane == lane
            )
            validate_partition_receipts(receipts, lane=lane)
    if validate_identities:
        for spec in PARTITION_SPECS:
            identity_path = root / spec.artifact_name / spec.identity_name
            raw_identity = json.loads(identity_path.read_text(encoding="utf-8"))
            if (
                not isinstance(raw_identity, dict)
                or raw_identity.get("layer") != spec.layer
            ):
                raise ValueError(
                    f"coverage identity layer mismatch for "
                    f"{spec.artifact_name}/{spec.identity_name}"
                )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    artifacts = subparsers.add_parser("validate-artifacts")
    artifacts.add_argument("--root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "validate-artifacts":
        validate_artifacts(args.root)
        return 0
    raise AssertionError(f"unhandled command {args.command}")


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    """Apply one receipt-bound fixed selection in controller and xdist workers."""
    lane_value = os.environ.get(f"{_ENV_PREFIX}LANE")
    if lane_value is None:
        return
    try:
        environment = PartitionEnvironment.from_environ(os.environ)
    except ValueError as exc:
        raise _usage_error(str(exc)) from exc
    has_fixed_roots = {
        "ontolib/tests",
        "backend/tests",
    } <= set(config.args)
    if environment.fixed_roots and not has_fixed_roots:
        if environment.phase == "collect":
            raise _usage_error("fixed partition collect requires repository test roots")
        # Partition tests may launch focused nested pytest processes. Execute-phase
        # children inherit the selector but do not represent the fixed collection.
        return
    lane = environment.lane
    shard = environment.shard_id
    root = Path(str(config.rootpath)).resolve()
    records = tuple(_record(item, root) for item in items)
    eligible = tuple(record for record in records if _eligible(record, lane))
    selected, classification, index = _selection(records, lane, shard, root)
    selected_ids = {record.nodeid for record in selected}
    items[:] = [
        item
        for item, record in zip(items, records, strict=True)
        if record.nodeid in selected_ids
    ]
    receipt = build_receipt(
        lane=lane,
        shard_id=shard,
        shard_index=index,
        shard_count=environment.shard_count,
        full_inventory=[record.nodeid for record in eligible],
        selected_nodeids=[record.nodeid for record in selected],
        classification=classification,
    )
    receipt_path = environment.receipt
    if environment.phase == "collect":
        _write_receipt(receipt_path, receipt)
        return
    if not receipt_path.is_file():
        raise _usage_error("execute phase requires an existing partition receipt")
    expected = load_receipt(receipt_path)
    if expected != receipt:
        raise _usage_error("execution collection differs from partition receipt")


if __name__ == "__main__":
    raise SystemExit(main())
