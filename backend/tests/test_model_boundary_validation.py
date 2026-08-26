from __future__ import annotations

from pathlib import Path

import pytest
from scripts.validation.check_model_boundaries import validate_model_boundaries

pytestmark = pytest.mark.unit


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_validator_accepts_explicit_domain_to_wire_adapter(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "domain.py",
        "from dataclasses import dataclass\n"
        "@dataclass(frozen=True, slots=True)\n"
        "class Verdict:\n    code: str\n",
    )
    _write(
        tmp_path,
        "wire.py",
        "from pydantic import BaseModel, ConfigDict\n"
        "from domain import Verdict\n"
        "class VerdictDocument(BaseModel):\n"
        "    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)\n"
        "    code: str\n"
        "def to_document(value: Verdict) -> VerdictDocument:\n"
        "    return VerdictDocument(code=value.code)\n",
    )

    assert validate_model_boundaries(tmp_path) == []


def test_validator_rejects_nested_and_aliased_cross_model_fields(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "models.py",
        "from dataclasses import dataclass\n"
        "from typing import Annotated, TypeAlias\n"
        "from pydantic import BaseModel, Field\n"
        "class Document(BaseModel):\n    value: str\n"
        "Docs: TypeAlias = dict[str, tuple[Document, ...]]\n"
        "@dataclass(frozen=True, slots=True)\n"
        "class Domain:\n    documents: Docs\n"
        "@dataclass(frozen=True, slots=True)\n"
        "class Fact:\n    code: str\n"
        "FactUnion = Annotated[Fact, Field(discriminator=None)]\n"
        "class Envelope(BaseModel):\n    fact: FactUnion\n",
    )

    findings = validate_model_boundaries(tmp_path)

    assert any("Domain.documents" in finding for finding in findings)
    assert any("Envelope.fact" in finding for finding in findings)


def test_validator_rejects_callable_alias_and_inherited_crossing(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "models.py",
        "from collections.abc import Callable\n"
        "from dataclasses import dataclass\n"
        "from pydantic import BaseModel\n"
        "class Document(BaseModel):\n    value: str\n"
        "Step = Callable[[Document], None]\n"
        "@dataclass(frozen=True, slots=True)\n"
        "class Base:\n    step: Step\n"
        "@dataclass(frozen=True, slots=True)\n"
        "class Child(Base):\n    code: str\n",
    )

    findings = validate_model_boundaries(tmp_path)

    assert any("Base.step" in finding for finding in findings)
    assert any("Child.step" in finding for finding in findings)


def test_validator_rejects_mutable_and_pydantic_dataclasses(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "models.py",
        "from dataclasses import dataclass\n"
        "from pydantic.dataclasses import dataclass as pydantic_dataclass\n"
        "@dataclass\nclass Mutable:\n    value: int\n"
        "@pydantic_dataclass(frozen=True)\n"
        "class Hybrid:\n    value: int\n",
    )

    findings = validate_model_boundaries(tmp_path)

    assert any("Mutable" in finding and "frozen" in finding for finding in findings)
    assert any(
        "Hybrid" in finding and "Pydantic dataclass" in finding for finding in findings
    )


def test_validator_rejects_direct_dataclass_boundary_serialization(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "models.py",
        "import dataclasses\n"
        "from dataclasses import dataclass\n"
        "@dataclass(frozen=True, slots=True)\n"
        "class Domain:\n    value: int\n"
        "def serialize(value: Domain) -> dict[str, object]:\n"
        "    return dataclasses.asdict(value)\n",
    )

    findings = validate_model_boundaries(tmp_path)

    assert any("Domain" in finding and "asdict" in finding for finding in findings)


def test_validator_reports_unresolved_project_annotations(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "models.py",
        "from dataclasses import dataclass\n"
        "@dataclass(frozen=True, slots=True)\n"
        "class Domain:\n    missing: ProjectType\n",
    )

    findings = validate_model_boundaries(tmp_path)

    assert any(
        "ProjectType" in finding and "unresolved" in finding for finding in findings
    )


def test_validator_reports_dotted_and_unparsable_forward_annotations(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "models.py",
        "from dataclasses import dataclass\n"
        "@dataclass(frozen=True, slots=True)\n"
        "class Domain:\n"
        "    dotted: 'project_models.MissingType'\n"
        "    malformed: 'MissingType['\n",
    )

    findings = validate_model_boundaries(tmp_path)

    assert any(
        "Domain.dotted" in finding and "project_models.MissingType" in finding
        for finding in findings
    )
    assert any(
        "Domain.malformed" in finding and "MissingType[" in finding
        for finding in findings
    )


def test_validator_rejects_non_strict_pydantic_boundary(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "models.py",
        "from pydantic import BaseModel\nclass Document(BaseModel):\n    value: int\n",
    )

    findings = validate_model_boundaries(tmp_path)

    assert any("Document" in finding and "strict" in finding for finding in findings)


def test_validator_accepts_inherited_strict_pydantic_config(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "models.py",
        "from pydantic import BaseModel, ConfigDict\n"
        "class StrictDocument(BaseModel):\n"
        "    model_config = ConfigDict(strict=True, extra='forbid')\n"
        "class ChildDocument(StrictDocument):\n    value: int\n",
    )

    assert validate_model_boundaries(tmp_path) == []


def test_validator_rejects_named_serialized_dataclass_document(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "models.py",
        "from dataclasses import dataclass\n"
        "@dataclass(frozen=True, slots=True)\n"
        "class ExampleManifest:\n    schema_version: int\n",
    )

    findings = validate_model_boundaries(tmp_path)

    assert any(
        "ExampleManifest" in finding and "boundary document" in finding
        for finding in findings
    )


def test_repository_has_no_model_boundary_violations() -> None:
    root = Path(__file__).resolve().parents[2]

    assert validate_model_boundaries(root) == []
