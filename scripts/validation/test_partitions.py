"""Collection-driven, file-level partitions for the fixed Python CI lanes."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import tempfile
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

ALGORITHM_VERSION = "sha256-mod-v1"
RECEIPT_SCHEMA_VERSION = 1
SHARD_COUNT = 2
_ENV_PREFIX = "ONTOPRISM_TEST_PARTITION_"
_QLEVER_FIXTURE_FRAGMENT = "qlever"
Lane = Literal["backend", "integration"]


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
        explicit_jena_files = {
            record.path for record in records if "requires_jena" in record.markers
        }
        jena_files = tuple(sorted(set(qlever_files) | explicit_jena_files))
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
    """Minimal proof that one shard selected its exact share of one inventory."""

    schema_version: int
    lane: Lane
    shard_id: str
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
    def validate_internal_digests(self) -> Self:  # noqa: C901
        if self.schema_version != RECEIPT_SCHEMA_VERSION:
            raise ValueError("unsupported partition receipt schema")
        if self.algorithm_version != ALGORITHM_VERSION:
            raise ValueError("unsupported partition algorithm")
        if self.shard_count != SHARD_COUNT:
            raise ValueError("partition receipt must describe exactly two shards")
        if self.shard_index >= self.shard_count:
            raise ValueError("partition receipt shard index is out of range")
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
    shard_id: str,
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


def validate_partition_receipts(  # noqa: C901
    receipts: Sequence[PartitionReceipt],
    *,
    lane: Lane,
    expected_shards: Sequence[str],
) -> None:
    """Require exact shard identities and an exact disjoint inventory union."""
    expected = tuple(expected_shards)
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
    if lane == "backend":
        return "integration" not in record.markers
    return "integration" in record.markers and not record.markers.intersection(
        {"full_store", "full_build", "slow"}
    )


def _selection(
    records: Sequence[CollectionRecord], lane: Lane, shard: str, root: Path
) -> tuple[tuple[CollectionRecord, ...], IntegrationClassification | None, int]:
    eligible = tuple(record for record in records if _eligible(record, lane))
    if not eligible:
        raise _usage_error(f"{lane} full inventory is empty")
    if lane == "backend":
        if shard not in {"0", "1"}:
            raise _usage_error("backend shard must be 0 or 1")
        index = int(shard)
        selected_ids = set(
            assign_backend_modules([r.nodeid for r in eligible], index, SHARD_COUNT)
        )
        return tuple(r for r in eligible if r.nodeid in selected_ids), None, index
    if shard not in {"qlever", "postgres"}:
        raise _usage_error("integration shard must be qlever or postgres")
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
    return selected, classification, 0 if shard == "qlever" else 1


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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate-receipts")
    validate.add_argument("--backend", type=Path, nargs=2, required=True)
    validate.add_argument("--integration", type=Path, nargs=2, required=True)
    artifacts = subparsers.add_parser("validate-artifacts")
    artifacts.add_argument("--root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "validate-receipts":
        backend = tuple(load_receipt(path) for path in args.backend)
        integration = tuple(load_receipt(path) for path in args.integration)
        validate_partition_receipts(backend, lane="backend", expected_shards=("0", "1"))
        validate_partition_receipts(
            integration,
            lane="integration",
            expected_shards=("qlever", "postgres"),
        )
        return 0
    if args.command == "validate-artifacts":
        expected = {
            "coverage-backend-1": {
                ".coverage.unit-0",
                "coverage-unit-0.identity.json",
                "coverage-unit-0.xml",
                "partition-backend-0.json",
            },
            "coverage-backend-2": {
                ".coverage.unit-1",
                "coverage-unit-1.identity.json",
                "coverage-unit-1.xml",
                "partition-backend-1.json",
            },
            "coverage-integration-qlever": {
                ".coverage.integration-qlever",
                "coverage-integration-qlever.identity.json",
                "partition-integration-qlever.json",
            },
            "coverage-integration-postgres": {
                ".coverage.integration-postgres",
                "coverage-integration-postgres.identity.json",
                "partition-integration-postgres.json",
            },
        }
        root = args.root.resolve()
        for directory, expected_names in expected.items():
            path = root / directory
            actual = (
                {entry.name for entry in path.iterdir()} if path.is_dir() else set()
            )
            if actual != expected_names or not all(
                (path / name).is_file() for name in expected_names
            ):
                raise ValueError(
                    f"coverage artifact {directory} files mismatch: "
                    f"expected {sorted(expected_names)}, got {sorted(actual)}"
                )
        return 0
    raise AssertionError(f"unhandled command {args.command}")


def pytest_collection_modifyitems(config: Any, items: list[Any]) -> None:
    """Apply the same fixed file selection in controllers and xdist workers."""
    lane_value = os.environ.get(f"{_ENV_PREFIX}LANE")
    if lane_value is None:
        return
    if os.environ.get(f"{_ENV_PREFIX}FIXED_ROOTS") == "1" and not {
        "ontolib/tests",
        "backend/tests",
    } <= set(config.args):
        # Tests in a partition can launch focused nested pytest contracts. Those
        # subprocesses inherit the environment but are not the fixed CI collection.
        return
    if lane_value not in {"backend", "integration"}:
        raise _usage_error("invalid test partition lane")
    lane = cast("Lane", lane_value)
    shard = os.environ.get(f"{_ENV_PREFIX}SHARD", "")
    count = os.environ.get(f"{_ENV_PREFIX}COUNT")
    if count != "2":
        raise _usage_error("test partition count must be 2")
    receipt_value = os.environ.get(f"{_ENV_PREFIX}RECEIPT")
    if not receipt_value:
        raise _usage_error("test partition receipt path is required")
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
        shard_count=2,
        full_inventory=[record.nodeid for record in eligible],
        selected_nodeids=[record.nodeid for record in selected],
        classification=classification,
    )
    receipt_path = Path(receipt_value)
    phase = os.environ.get(f"{_ENV_PREFIX}PHASE")
    if phase == "collect" or not receipt_path.exists():
        _write_receipt(receipt_path, receipt)
    else:
        expected = load_receipt(receipt_path)
        if expected != receipt:
            raise _usage_error("execution collection differs from partition receipt")


if __name__ == "__main__":
    raise SystemExit(main())
