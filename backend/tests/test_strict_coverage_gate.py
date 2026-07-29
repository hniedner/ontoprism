from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from scripts.validation.strict_coverage_gate import (
    AggregateCoverage,
    CoverageGateError,
    coverage_py_aggregate,
    enforce_strict_coverage,
    frontend_scope_coverage,
    main,
    python_scope_coverage,
)

pytestmark = pytest.mark.unit


def _aggregate(
    *,
    covered_lines: int = 91,
    total_lines: int = 100,
    covered_branches: int = 91,
    total_branches: int = 100,
) -> AggregateCoverage:
    return AggregateCoverage(
        scope="scope",
        covered_lines=covered_lines,
        total_lines=total_lines,
        covered_branches=covered_branches,
        total_branches=total_branches,
    )


def test_strict_gate_rejects_exactly_ninety_percent_lines() -> None:
    aggregate = _aggregate(covered_lines=90)

    with pytest.raises(CoverageGateError, match=r"lines 90\.00%.*must be > 90%"):
        enforce_strict_coverage((aggregate,))


def test_strict_gate_rejects_exactly_ninety_percent_branches() -> None:
    aggregate = _aggregate(covered_branches=90)

    with pytest.raises(CoverageGateError, match=r"branches 90\.00%.*must be > 90%"):
        enforce_strict_coverage((aggregate,))


def test_strict_gate_reports_independent_passing_metrics() -> None:
    report = enforce_strict_coverage((_aggregate(),))

    assert "scope: lines 91.00% (91/100)" in report
    assert "branches 91.00% (91/100)" in report
    assert "PASS (> 90%)" in report


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("total_lines", 0, "no measurable lines"),
        ("total_branches", 0, "no measurable branches"),
        ("covered_lines", -1, "negative covered lines"),
        ("covered_branches", -1, "negative covered branches"),
        ("covered_lines", 101, "covered lines exceeds total"),
        ("covered_branches", 101, "covered branches exceeds total"),
    ],
)
def test_strict_gate_rejects_invalid_or_unmeasured_totals(
    field: str,
    value: int,
    message: str,
) -> None:
    values = {
        "scope": "scope",
        "covered_lines": 91,
        "total_lines": 100,
        "covered_branches": 91,
        "total_branches": 100,
    }
    values[field] = value

    with pytest.raises(CoverageGateError, match=message):
        enforce_strict_coverage((AggregateCoverage(**values),))


def test_strict_gate_rejects_missing_scope_evidence() -> None:
    with pytest.raises(CoverageGateError, match="no aggregate coverage scopes"):
        enforce_strict_coverage(())


def test_frontend_scope_reads_independent_line_and_branch_totals(
    tmp_path: Path,
) -> None:
    summary = tmp_path / "coverage-summary.json"
    summary.write_text(
        json.dumps(
            {
                "total": {
                    "lines": {"covered": 793, "total": 798, "pct": 99.37},
                    "branches": {"covered": 429, "total": 460, "pct": 93.26},
                    "functions": {"covered": 343, "total": 345, "pct": 99.42},
                    "statements": {"covered": 1376, "total": 1382, "pct": 99.56},
                }
            }
        ),
        encoding="utf-8",
    )

    aggregate = frontend_scope_coverage(summary)

    assert aggregate == AggregateCoverage(
        scope="frontend/src/lib",
        covered_lines=793,
        total_lines=798,
        covered_branches=429,
        total_branches=460,
    )


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"total": {}},
        {
            "total": {
                "lines": {"covered": 91, "total": 100},
                "branches": {"covered": "91", "total": 100},
            }
        },
    ],
)
def test_frontend_scope_rejects_malformed_summary(
    tmp_path: Path,
    payload: object,
) -> None:
    summary = tmp_path / "coverage-summary.json"
    summary.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CoverageGateError, match="coverage summary"):
        frontend_scope_coverage(summary)


def test_frontend_scope_rejects_invalid_json(tmp_path: Path) -> None:
    summary = tmp_path / "coverage-summary.json"
    summary.write_text("{", encoding="utf-8")

    with pytest.raises(CoverageGateError, match="invalid JSON"):
        frontend_scope_coverage(summary)


def test_coverage_loaders_reject_missing_artifacts(tmp_path: Path) -> None:
    with pytest.raises(CoverageGateError, match="coverage data file does not exist"):
        python_scope_coverage(
            tmp_path / ".coverage",
            root=tmp_path,
            scope="src",
        )
    with pytest.raises(CoverageGateError, match="coverage summary does not exist"):
        frontend_scope_coverage(tmp_path / "coverage-summary.json")


def test_python_scope_uses_real_coverage_py_line_and_branch_totals(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    subject = source_dir / "subject.py"
    subject.write_text(
        """
def choose(flag: bool) -> int:
    if flag:
        return 1
    return 0


choose(True)
""".lstrip(),
        encoding="utf-8",
    )
    data_file = tmp_path / ".coverage"
    subprocess.run(  # noqa: S603 — trusted interpreter and generated local source
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            "--branch",
            f"--data-file={data_file}",
            str(subject),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    aggregate = python_scope_coverage(
        data_file,
        root=tmp_path,
        scope="src",
    )

    assert aggregate == AggregateCoverage(
        scope="src",
        covered_lines=4,
        total_lines=5,
        covered_branches=1,
        total_branches=2,
    )
    with pytest.raises(CoverageGateError, match="no Python source files"):
        python_scope_coverage(
            data_file,
            root=tmp_path,
            scope="missing-src",
        )


def test_coverage_py_totals_reject_malformed_external_result_shape() -> None:
    with pytest.raises(CoverageGateError, match="missing totals"):
        coverage_py_aggregate({}, scope="scope")
    with pytest.raises(CoverageGateError, match="missing integer"):
        coverage_py_aggregate(
            {
                "totals": {
                    "covered_lines": True,
                    "num_statements": 100,
                    "covered_branches": 91,
                    "num_branches": 100,
                }
            },
            scope="scope",
        )


def test_frontend_cli_reports_success_and_missing_artifact(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    summary = tmp_path / "coverage-summary.json"
    summary.write_text(
        json.dumps(
            {
                "total": {
                    "lines": {"covered": 91, "total": 100},
                    "branches": {"covered": 91, "total": 100},
                }
            }
        ),
        encoding="utf-8",
    )

    assert main(["frontend", "--coverage-summary", str(summary)]) == 0
    assert "frontend/src/lib: lines 91.00%" in capsys.readouterr().out

    assert (
        main(
            [
                "frontend",
                "--coverage-summary",
                str(tmp_path / "missing-summary.json"),
            ]
        )
        == 1
    )
    assert "strict coverage gate failed" in capsys.readouterr().err


def test_python_cli_gates_both_documented_scopes_with_real_coverage_py(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sources: list[Path] = []
    for scope in ("ontolib/src", "backend/src"):
        source = tmp_path / scope / "subject.py"
        source.parent.mkdir(parents=True)
        source.write_text(
            """
def choose(flag: bool) -> int:
    if flag:
        return 1
    return 0


choose(True)
choose(False)
""".lstrip(),
            encoding="utf-8",
        )
        sources.append(source)
    driver = tmp_path / "driver.py"
    driver.write_text(
        "import runpy\n"
        + "\n".join(f"runpy.run_path({str(path)!r})" for path in sources)
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        "[tool.coverage.run]\nbranch = true\n",
        encoding="utf-8",
    )
    data_file = tmp_path / ".coverage"
    subprocess.run(  # noqa: S603 — trusted interpreter and generated local source
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            "--branch",
            f"--data-file={data_file}",
            str(driver),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    assert (
        main(
            [
                "python",
                "--coverage-data",
                str(data_file),
                "--root",
                str(tmp_path),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "ontolib/src: lines 100.00%" in output
    assert "backend/src: lines 100.00%" in output


def test_repository_wires_strict_gate_into_local_and_ci_entrypoints() -> None:
    root = Path(__file__).resolve().parents[2]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = pyproject["tool"]["pdm"]["scripts"]
    test_ci = scripts["test-ci"]["shell"]
    assert "strict_coverage_gate.py python" in test_ci
    assert "coverage report --include=" not in test_ci

    package = json.loads((root / "frontend/package.json").read_text(encoding="utf-8"))
    assert "strict_coverage_gate.py frontend" in package["scripts"]["test:coverage"]

    workflow = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "strict_coverage_gate.py python" in workflow
    assert "--against-current" in workflow
    assert "COVERAGE_CONFIG_SET: python-combined" in workflow
