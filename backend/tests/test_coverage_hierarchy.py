from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from scripts.validation.coverage_hierarchy import (
    ArtifactIdentity,
    Metric,
    build_frontend_report,
    build_python_report,
    identity_from_mapping,
    load_coverage_config,
    load_manifest,
    main,
    validate_manifest,
    verify_identities,
    verify_identities_against_current,
)
from scripts.validation.coverage_hierarchy import (
    _python_raw_report as python_raw_report,
)

pytestmark = pytest.mark.unit


def _write_pyproject(
    root: Path, *, exclude_also: str = "", partial_also: str = ""
) -> Path:
    pyproject = root / "pyproject.toml"
    pyproject.write_text(
        "[tool.coverage.report]\n"
        f"exclude_also = [{exclude_also}]\n"
        f"partial_also = [{partial_also}]\n",
        encoding="utf-8",
    )
    return pyproject


def _write_manifest(
    root: Path,
    *,
    default_group: str = "app",
    required_path: str = "",
    assignment: str = "",
    exemption: str = "",
) -> Path:
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        pyproject.write_text(
            "[tool.coverage.report]\nexclude_also = []\npartial_also = []\n",
            encoding="utf-8",
        )
    manifest = root / "coverage-surfaces.toml"
    required = f'"{required_path}"' if required_path else ""
    manifest.write_text(
        f"""
schema_version = 1
report_only = true
limitations = ["Fixture limitation."]
required_production_paths = [{required}]

[[group]]
name = "app"
classification = "production"
language = "python"
measurement = "coverage.py"
tree = "python"
kind = "application"
executable = true

[[group]]
name = "dev"
classification = "dev"
language = "python"
measurement = "none"
tree = "dev"
kind = "tooling"
executable = true

[[inventory]]
root = "src"
extensions = [".py"]
default_group = "{default_group}"

{assignment}
{exemption}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return manifest


def _identity(commit: str = "a" * 40) -> ArtifactIdentity:
    return ArtifactIdentity(
        schema_version=1,
        commit=commit,
        config_sha256="b" * 64,
        manifest_sha256="c" * 64,
        source_sha256="d" * 64,
        tool="coverage.py",
        tool_version="7.15.2",
        layer="python-combined",
    )


def _summary(*, lines: tuple[int, int], branches: tuple[int, int]) -> dict[str, int]:
    return {
        "covered_lines": lines[0],
        "num_statements": lines[1],
        "covered_branches": branches[0],
        "num_branches": branches[1],
    }


def test_weak_function_is_reported_beneath_strong_module(tmp_path: Path) -> None:
    source = tmp_path / "src" / "strong.py"
    source.parent.mkdir()
    source.write_text("def weak(flag: bool) -> int:\n    return 1 if flag else 0\n")
    manifest = load_manifest(_write_manifest(tmp_path), tmp_path)
    raw = {
        "meta": {"format": 3, "version": "7.15.2", "branch_coverage": True},
        "files": {
            "src/strong.py": {
                "summary": _summary(lines=(19, 20), branches=(19, 20)),
                "functions": {
                    "weak": {
                        "summary": _summary(lines=(1, 2), branches=(1, 2)),
                        "start_line": 1,
                    },
                    "": {
                        "summary": _summary(lines=(18, 18), branches=(18, 18)),
                        "start_line": 1,
                    },
                },
                "classes": {},
            }
        },
    }

    report = build_python_report(manifest, raw, _identity(), tmp_path)

    deficits = {scope.scope_id for scope in report.deficits}
    assert "function:src/strong.py:weak@1" in deficits
    assert "module:src/strong.py" not in deficits


def test_branchless_metric_is_na_and_unmeasured_metric_is_zero() -> None:
    assert Metric(covered=0, total=0).display == "N/A (no branches)"
    assert Metric(covered=0, total=None).display == "0.00% (unmeasured)"
    assert Metric(covered=0, total=None).percent == 0.0
    assert (
        Metric(covered=0, total=0, kind="lines").display == "N/A (no executable lines)"
    )


def test_metric_deficit_uses_strictly_greater_than_ninety_percent_floor() -> None:
    assert Metric(covered=90, total=100).is_deficit is True
    assert Metric(covered=91, total=100).is_deficit is False


@pytest.mark.parametrize(
    ("values", "location", "message"),
    [
        (
            {"covered": -1, "total": 1},
            ("covered",),
            "Input should be greater than or equal to 0",
        ),
        (
            {"covered": 0, "total": -1},
            ("total",),
            "Input should be greater than or equal to 0",
        ),
        ({"covered": 2, "total": 1}, (), "Value error, covered must not exceed total"),
    ],
)
def test_metric_rejects_impossible_counts(
    values: dict[str, int], location: tuple[str, ...], message: str
) -> None:
    with pytest.raises(ValidationError) as error:
        Metric.model_validate(values)

    assert error.value.errors(include_url=False)[0]["loc"] == location
    assert error.value.errors(include_url=False)[0]["msg"] == message


def test_artifact_identity_serialization_tracks_model_fields_and_roundtrips() -> None:
    class ExtendedArtifactIdentity(ArtifactIdentity):
        collector: str

    identity = ExtendedArtifactIdentity(**_identity().model_dump(), collector="ci")

    assert identity.as_dict() == identity.model_dump(mode="json")
    assert identity_from_mapping(_identity().as_dict()) == _identity()


def test_artifact_identity_model_rejects_unsupported_tool_typo() -> None:
    values = {**_identity().as_dict(), "tool": "coverge.py"}

    with pytest.raises(ValidationError) as error:
        ArtifactIdentity.model_validate(values)

    assert error.value.errors(include_url=False)[0]["loc"] == ("tool",)
    assert error.value.errors(include_url=False)[0]["msg"] == (
        "Input should be 'coverage.py' or 'vitest'"
    )


def test_identity_cli_rejects_unsupported_tool_typo(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as error:
        main(
            [
                "identity",
                "--layer",
                "python-unit",
                "--tool",
                "coverge.py",
                "--tool-version",
                "7.15.2",
                "--output",
                str(tmp_path / "identity.json"),
            ]
        )

    assert error.value.code == 2


def test_unmeasured_executable_module_is_reported_as_zero(tmp_path: Path) -> None:
    source = tmp_path / "src" / "missing.py"
    source.parent.mkdir()
    source.write_text("def missing() -> int:\n    return 1\n")
    manifest = load_manifest(_write_manifest(tmp_path), tmp_path)
    raw = {
        "meta": {"format": 3, "version": "7.15.2", "branch_coverage": True},
        "files": {},
    }

    report = build_python_report(manifest, raw, _identity(), tmp_path)

    module = next(s for s in report.scopes if s.scope_id == "module:src/missing.py")
    assert module.lines.display == "0.00% (unmeasured)"
    assert module.branches.display == "0.00% (unmeasured)"
    assert module in report.deficits


def test_manifest_rejects_omitted_required_production_cli(tmp_path: Path) -> None:
    source = tmp_path / "src" / "data_build.py"
    source.parent.mkdir()
    source.write_text("def main() -> None:\n    pass\n")
    manifest_path = _write_manifest(
        tmp_path,
        default_group="dev",
        required_path="src/data_build.py",
    )
    manifest = load_manifest(manifest_path, tmp_path)

    errors = validate_manifest(manifest, tmp_path)

    assert any("required production path" in error for error in errors)


def test_manifest_rejects_broad_and_unowned_exemptions(tmp_path: Path) -> None:
    source = tmp_path / "src" / "module.py"
    test_file = tmp_path / "tests" / "test_module.py"
    source.parent.mkdir()
    test_file.parent.mkdir()
    source.write_text("def value() -> int:  # pragma: no cover\n    return 1\n")
    test_file.write_text("def test_value() -> None:\n    assert 1 == 1\n")
    exemption = """
[[exemption]]
path = "src/**"
line = 1
kind = "pragma-no-cover"
owner = ""
rationale = ""
behavioral_test = ""
review_issue = 0
review_after = "not-a-date"
"""
    manifest = load_manifest(_write_manifest(tmp_path, exemption=exemption), tmp_path)

    errors = validate_manifest(manifest, tmp_path)

    assert any("must be an exact path" in error for error in errors)
    assert any("owner" in error for error in errors)
    assert any("behavioral_test" in error for error in errors)
    assert any("review_issue" in error for error in errors)
    assert any("unowned pragma" in error for error in errors)


def test_manifest_accepts_owned_pragma_with_behavioral_test(tmp_path: Path) -> None:
    source = tmp_path / "src" / "module.py"
    test_file = tmp_path / "tests" / "test_module.py"
    source.parent.mkdir()
    test_file.parent.mkdir()
    source.write_text("def value() -> int:  # pragma: no cover\n    return 1\n")
    test_file.write_text("def test_value() -> None:\n    assert value() == 1\n")
    exemption = """
[[exemption]]
path = "src/module.py"
line = 1
kind = "pragma-no-cover"
owner = "test-owner"
rationale = "The excluded guard is structurally unreachable in normal execution."
behavioral_test = "tests/test_module.py"
review_issue = 170
review_after = "2099-01-01"
"""
    manifest = load_manifest(_write_manifest(tmp_path, exemption=exemption), tmp_path)

    assert validate_manifest(manifest, tmp_path) == []


def test_repository_coverage_config_exclusions_are_owned() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    manifest = load_manifest(repo_root / "coverage-surfaces.toml", repo_root)
    config = load_coverage_config(repo_root / "pyproject.toml")

    owned = {
        exemption.path
        for exemption in manifest.exemptions
        if exemption.kind == "coverage-exclude-regex"
    }
    assert set(config.exclude_also) == owned


def test_identity_mismatch_refuses_cross_commit_merge() -> None:
    unit = _identity("a" * 40)
    integration = ArtifactIdentity(
        **{
            **unit.as_dict(),
            "commit": "d" * 40,
            "layer": "python-integration",
        }
    )

    with pytest.raises(ValueError, match="commit identity mismatch"):
        verify_identities((unit, integration))


def test_identity_mismatch_refuses_configuration_and_source_drift() -> None:
    baseline = _identity()
    replacements = {
        "config_sha256": "e" * 40,
        "manifest_sha256": "e" * 40,
        "source_sha256": "e" * 40,
        "tool": "vitest",
        "tool_version": "e" * 40,
    }
    for field, replacement in replacements.items():
        changed = ArtifactIdentity(
            **{
                **baseline.as_dict(),
                field: replacement,
                "layer": "python-integration",
            }
        )
        with pytest.raises(ValueError, match=f"{field} identity mismatch"):
            verify_identities((baseline, changed))


def test_identity_verification_rejects_dirty_collection() -> None:
    dirty = ArtifactIdentity(**{**_identity().as_dict(), "worktree_dirty": True})

    with pytest.raises(ValueError, match="dirty worktree"):
        verify_identities((dirty,))


def test_identity_verification_rejects_artifacts_stale_against_checkout() -> None:
    artifact = _identity("a" * 40)
    current = ArtifactIdentity(
        **{
            **artifact.as_dict(),
            "commit": "e" * 40,
            "layer": "current-checkout",
        }
    )

    with pytest.raises(ValueError, match=r"stale.*current checkout"):
        verify_identities_against_current((artifact,), current)


def test_current_identity_allows_downloaded_artifacts_outside_source_inventory() -> (
    None
):
    artifact = _identity()
    current_with_auxiliary_outputs = ArtifactIdentity(
        **{
            **artifact.as_dict(),
            "layer": "current-checkout",
            "worktree_dirty": True,
        }
    )

    assert (
        verify_identities_against_current((artifact,), current_with_auxiliary_outputs)
        is None
    )


def test_coverage_py_contract_exposes_native_functions_classes_and_branches(
    tmp_path: Path,
) -> None:
    # Run the real Coverage.py CLI in a subprocess (not a nested in-process session
    # under pytest-cov's sys.monitoring tool) so the contract is measured cleanly and
    # cannot perturb the outer suite's coverage instrumentation.
    source = tmp_path / "subject.py"
    data_file = tmp_path / ".coverage"
    output = tmp_path / "coverage.json"
    source.write_text(
        """
class Choice:
    def choose(self, flag):
        if flag:
            return 1
        return 0


def branchless():
    return 1


Choice().choose(True)
branchless()
""".lstrip(),
        encoding="utf-8",
    )
    subprocess.run(  # noqa: S603 — sys.executable is the trusted test interpreter
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            "--branch",
            f"--data-file={data_file}",
            str(source),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(  # noqa: S603 — sys.executable is the trusted test interpreter
        [
            sys.executable,
            "-m",
            "coverage",
            "json",
            f"--data-file={data_file}",
            "-o",
            str(output),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    raw = json.loads(output.read_text(encoding="utf-8"))
    assert raw["meta"]["format"] == 3
    assert raw["meta"]["branch_coverage"] is True
    file_data = next(iter(raw["files"].values()))
    assert file_data["functions"]["Choice.choose"]["summary"]["num_branches"] == 2
    assert file_data["functions"]["branchless"]["summary"]["num_branches"] == 0
    assert file_data["classes"]["Choice"]["summary"]["num_statements"] > 0


def test_repository_manifest_is_complete_and_owned() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    manifest = load_manifest(repo_root / "coverage-surfaces.toml", repo_root)

    assert validate_manifest(manifest, repo_root) == []
    assert {
        "frontend/src/lib/components/AlignmentLinks.svelte.test.ts",
        "frontend/src/routes/repositories/uberon/[curie]/UberonConceptSummary.svelte.test.ts",
        "frontend/e2e/repositories.spec.ts",
        "frontend/e2e/ssr-bff.spec.ts",
    }.issubset(manifest.required_test_paths)


def test_python_report_keeps_unmeasured_release_workflow_visible() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    manifest = load_manifest(repo_root / "coverage-surfaces.toml", repo_root)
    raw = {
        "meta": {"format": 3, "version": "7.15.2", "branch_coverage": True},
        "files": {},
    }

    report = build_python_report(manifest, raw, _identity(), repo_root)

    workflow = next(
        scope
        for scope in report.scopes
        if scope.scope_id == "module:.github/workflows/release.yml"
    )
    assert workflow.lines.display == "0.00% (unmeasured)"
    assert workflow in report.deficits


def _routing_manifest(root: Path, *, group_block: str) -> Path:
    """Write a manifest whose sole inventory routes into ``group_block``."""
    _write_pyproject(root)
    manifest = root / "coverage-surfaces.toml"
    manifest.write_text(
        f"""
schema_version = 1
report_only = true
limitations = ["Fixture limitation."]
required_production_paths = []

{group_block}

[[inventory]]
root = "src"
extensions = [".svelte"]
default_group = "under-test"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return manifest


def test_workflow_bucket_routes_on_language_not_measurement(tmp_path: Path) -> None:
    # A production workflow-language surface that declares a *measured* tool must
    # still be emitted (not dropped because measurement != "none").
    source = tmp_path / "src" / "deploy.svelte"
    source.parent.mkdir()
    source.write_text("<p>deploy</p>\n")
    manifest = load_manifest(
        _routing_manifest(
            tmp_path,
            group_block="""
[[group]]
name = "under-test"
classification = "production"
language = "workflow"
measurement = "coverage.py"
tree = "workflows"
kind = "production-workflow"
executable = true
""".strip(),
        ),
        tmp_path,
    )
    raw = {
        "meta": {"format": 3, "version": "7.15.2", "branch_coverage": True},
        "files": {},
    }

    report = build_python_report(manifest, raw, _identity(), tmp_path)

    scope = next(s for s in report.scopes if s.scope_id == "module:src/deploy.svelte")
    assert scope.lines.display == "0.00% (unmeasured)"


def test_frontend_none_surface_does_not_leak_into_python_report(tmp_path: Path) -> None:
    # A frontend production surface with measurement="none" must NOT be pulled into
    # the python report's workflow bucket (routing is by language, not measurement).
    source = tmp_path / "src" / "only_browser.svelte"
    source.parent.mkdir()
    source.write_text("<p>browser</p>\n")
    manifest = load_manifest(
        _routing_manifest(
            tmp_path,
            group_block="""
[[group]]
name = "under-test"
classification = "production"
language = "frontend"
measurement = "none"
tree = "frontend"
kind = "application"
executable = true
""".strip(),
        ),
        tmp_path,
    )
    raw = {
        "meta": {"format": 3, "version": "7.15.2", "branch_coverage": True},
        "files": {},
    }

    report = build_python_report(manifest, raw, _identity(), tmp_path)

    assert all(s.scope_id != "module:src/only_browser.svelte" for s in report.scopes)


def test_frontend_report_uses_native_line_and_branch_counts(tmp_path: Path) -> None:
    source = tmp_path / "src" / "component.svelte"
    source.parent.mkdir()
    source.write_text("<p>Hello</p>\n")
    manifest_path = _write_manifest(tmp_path)
    text = manifest_path.read_text().replace(
        'language = "python"', 'language = "frontend"', 1
    )
    text = text.replace('measurement = "coverage.py"', 'measurement = "vitest"', 1)
    text = text.replace('extensions = [".py"]', 'extensions = [".svelte"]')
    manifest_path.write_text(text)
    manifest = load_manifest(manifest_path, tmp_path)
    raw = {
        str(source): {
            "statementMap": {
                "0": {"start": {"line": 1, "column": 0}, "end": {"line": 1}},
                "1": {"start": {"line": 2, "column": 0}, "end": {"line": 2}},
            },
            "s": {"0": 1, "1": 0},
            "f": {"0": 1},
            "fnMap": {
                "0": {
                    "name": "weak",
                    "loc": {
                        "start": {"line": 1, "column": 0},
                        "end": {"line": 2, "column": 10},
                    },
                }
            },
            "branchMap": {
                "0": {
                    "loc": {
                        "start": {"line": 1, "column": 0},
                        "end": {"line": 2, "column": 10},
                    },
                    "locations": [
                        {
                            "start": {"line": 1, "column": 0},
                            "end": {"line": 1, "column": 5},
                        },
                        {
                            "start": {"line": 2, "column": 0},
                            "end": {"line": 2, "column": 5},
                        },
                    ],
                }
            },
            "b": {"0": [1, 0]},
        }
    }

    report = build_frontend_report(manifest, raw, _identity(), tmp_path)

    module = next(
        scope
        for scope in report.scopes
        if scope.scope_id == "module:src/component.svelte"
    )
    assert module.lines.display == "50.00% (1/2)"
    assert module.branches.display == "50.00% (1/2)"
    function = next(
        scope
        for scope in report.scopes
        if scope.scope_id == "function:src/component.svelte:weak@1"
    )
    assert function.lines.display == "50.00% (1/2)"
    assert function.branches.display == "50.00% (1/2)"


def test_frontend_report_raises_on_unrecognized_istanbul_location(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src" / "component.svelte"
    source.parent.mkdir()
    source.write_text("<p>Hello</p>\n")
    manifest_path = _write_manifest(tmp_path)
    text = manifest_path.read_text().replace(
        'language = "python"', 'language = "frontend"', 1
    )
    text = text.replace('measurement = "coverage.py"', 'measurement = "vitest"', 1)
    text = text.replace('extensions = [".py"]', 'extensions = [".svelte"]')
    manifest_path.write_text(text)
    manifest = load_manifest(manifest_path, tmp_path)
    raw = {
        str(source): {
            "statementMap": {"0": {"start": {"line": 1, "column": 0}, "end": {}}},
            "s": {"0": 1},
            "f": {"0": 1},
            "fnMap": {
                "0": {
                    "name": "weak",
                    "loc": {
                        "start": {"line": 1, "column": 0},
                        "end": {"line": 2, "column": 10},
                    },
                }
            },
            # A branch entry with neither a parseable start, a loc, nor locations is a
            # broken coverage-format assumption and must fail loudly, not be dropped.
            "branchMap": {"0": {}},
            "b": {"0": [1, 0]},
        }
    }

    with pytest.raises(ValueError, match="unrecognized Istanbul location shape"):
        build_frontend_report(manifest, raw, _identity(), tmp_path)


def test_load_manifest_rejects_unknown_enums(tmp_path: Path) -> None:
    _write_pyproject(tmp_path)
    base = _write_manifest(tmp_path).read_text()

    bad_classification = base.replace(
        'classification = "production"', 'classification = "prod"', 1
    )
    (tmp_path / "coverage-surfaces.toml").write_text(bad_classification)
    with pytest.raises(ValueError, match="unknown classification"):
        load_manifest(tmp_path / "coverage-surfaces.toml", tmp_path)

    bad_measurement = base.replace(
        'measurement = "coverage.py"', 'measurement = "coverage"', 1
    )
    (tmp_path / "coverage-surfaces.toml").write_text(bad_measurement)
    with pytest.raises(ValueError, match="unknown measurement"):
        load_manifest(tmp_path / "coverage-surfaces.toml", tmp_path)

    bad_language = base.replace('language = "python"', 'language = "pyton"', 1)
    (tmp_path / "coverage-surfaces.toml").write_text(bad_language)
    with pytest.raises(ValueError, match="unknown language"):
        load_manifest(tmp_path / "coverage-surfaces.toml", tmp_path)


def test_load_manifest_rejects_production_non_executable_group(tmp_path: Path) -> None:
    # A production group flagged non-executable is gated out of every report emitter,
    # so a required path routed to it would be silently omitted; reject it at parse.
    _write_pyproject(tmp_path)
    base = _write_manifest(tmp_path).read_text()
    contradictory = base.replace(
        'classification = "production"\nlanguage = "python"\n'
        'measurement = "coverage.py"\ntree = "python"\nkind = "application"\n'
        "executable = true",
        'classification = "production"\nlanguage = "python"\n'
        'measurement = "coverage.py"\ntree = "python"\nkind = "application"\n'
        "executable = false",
        1,
    )
    assert contradictory != base
    (tmp_path / "coverage-surfaces.toml").write_text(contradictory)
    with pytest.raises(ValueError, match="executable flag disagrees"):
        load_manifest(tmp_path / "coverage-surfaces.toml", tmp_path)


def test_load_manifest_rejects_unknown_exemption_kind(tmp_path: Path) -> None:
    source = tmp_path / "src" / "module.py"
    source.parent.mkdir()
    source.write_text("x = 1\n")
    exemption = """
[[exemption]]
path = "src/module.py"
line = 1
kind = "pragma_no_cover"
owner = "o"
rationale = "r"
behavioral_test = "src/module.py"
review_issue = 1
review_after = "2099-01-01"
"""
    manifest_path = _write_manifest(tmp_path, exemption=exemption)
    with pytest.raises(ValueError, match="unknown kind"):
        load_manifest(manifest_path, tmp_path)


def test_manifest_rejects_expired_exemption(tmp_path: Path) -> None:
    source = tmp_path / "src" / "module.py"
    test_file = tmp_path / "tests" / "test_module.py"
    source.parent.mkdir()
    test_file.parent.mkdir()
    source.write_text("def value() -> int:  # pragma: no cover\n    return 1\n")
    test_file.write_text("def test_value() -> None:\n    assert value() == 1\n")
    exemption = """
[[exemption]]
path = "src/module.py"
line = 1
kind = "pragma-no-cover"
owner = "o"
rationale = "structurally unreachable"
behavioral_test = "tests/test_module.py"
review_issue = 1
review_after = "2000-01-01"
"""
    manifest = load_manifest(_write_manifest(tmp_path, exemption=exemption), tmp_path)

    assert any(
        "review_after has expired" in error
        for error in validate_manifest(manifest, tmp_path)
    )


def test_manifest_rejects_misreferenced_measurement_exclusion(tmp_path: Path) -> None:
    source = tmp_path / "src" / "shell.svelte"
    test_file = tmp_path / "tests" / "e2e.spec.ts"
    config = tmp_path / "src" / "config.ts"
    source.parent.mkdir()
    test_file.parent.mkdir()
    source.write_text("<canvas></canvas>\n")
    test_file.write_text("test('renders', () => {});\n")
    config.write_text("export const coverage = {};\n")  # does NOT name the shell
    exemption = """
[[exemption]]
path = "src/shell.svelte"
line = 1
kind = "measurement-exclusion"
owner = "o"
rationale = "cannot mount in jsdom"
behavioral_test = "tests/e2e.spec.ts"
review_issue = 1
review_after = "2099-01-01"
configured_in = "src/config.ts"
"""
    manifest = load_manifest(_write_manifest(tmp_path, exemption=exemption), tmp_path)

    assert any(
        "is not named by" in error for error in validate_manifest(manifest, tmp_path)
    )


def test_manifest_rejects_config_regex_not_in_pyproject(tmp_path: Path) -> None:
    _write_pyproject(tmp_path, exclude_also='"if TYPE_CHECKING:"')
    exemption = """
[[exemption]]
path = "raise NotImplementedError"
line = 1
kind = "coverage-exclude-regex"
owner = "o"
rationale = "abstract sentinel"
behavioral_test = "coverage-surfaces.toml"
review_issue = 1
review_after = "2099-01-01"
configured_in = "pyproject.toml"
"""
    manifest = load_manifest(_write_manifest(tmp_path, exemption=exemption), tmp_path)

    errors = validate_manifest(manifest, tmp_path)
    assert any("does not match configured coverage regex" in error for error in errors)


def test_manifest_rejects_unowned_coverage_config_regex(tmp_path: Path) -> None:
    _write_pyproject(
        tmp_path,
        exclude_also='"if TYPE_CHECKING:"',
        partial_also='"pragma: no branch"',
    )
    manifest = load_manifest(_write_manifest(tmp_path), tmp_path)

    errors = validate_manifest(manifest, tmp_path)
    assert any("unowned coverage-exclude-regex: if TYPE_CHECKING:" in e for e in errors)
    assert any("unowned coverage-partial-regex: pragma: no branch" in e for e in errors)


def test_python_raw_report_rejects_empty_coverage_data(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    manifest = load_manifest(repo_root / "coverage-surfaces.toml", repo_root)

    with pytest.raises(ValueError, match="no measured files"):
        python_raw_report(
            manifest,
            tmp_path / "absent.coverage",
            tmp_path / "raw.json",
            repo_root,
        )
