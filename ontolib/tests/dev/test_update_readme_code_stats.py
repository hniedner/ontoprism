from __future__ import annotations

import shutil
import subprocess
import sys
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest
from scripts.dev.update_readme_code_stats import (
    END_MARKER,
    START_MARKER,
    LanguageStats,
    _count_lines,
    _tracked_files,
    build_table,
    main,
    parse_args,
    update_readme,
)

pytestmark = pytest.mark.unit

_GIT = shutil.which("git") or "git"


class TestCountLines:
    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.txt"
        f.write_text("")
        assert _count_lines(f) == 0

    def test_single_line(self, tmp_path: Path) -> None:
        f = tmp_path / "single.txt"
        f.write_text("hello\n")
        assert _count_lines(f) == 1

    def test_multiple_lines(self, tmp_path: Path) -> None:
        f = tmp_path / "multi.txt"
        f.write_text("a\nb\nc\n")
        assert _count_lines(f) == 3


class TestBuildTable:
    def _mock_tracked(self, files: list[tuple[str, str]], tmp_path: Path) -> list[Path]:
        paths: list[Path] = []
        for idx, (content, suffix) in enumerate(files):
            p = tmp_path / f"f{idx}{suffix}"
            p.write_text(content)
            paths.append(p)
        return paths

    def test_single_python_file(self, tmp_path: Path) -> None:
        paths = self._mock_tracked([("x = 1\n", ".py")], tmp_path)
        with patch(
            "scripts.dev.update_readme_code_stats._tracked_files", return_value=paths
        ):
            table = build_table()
        assert "## Codebase Line Count" in table
        assert "Python" in table
        assert "| 1 |" in table
        assert "**Total**" in table

    def test_multiple_languages_sorted_by_line_count(self, tmp_path: Path) -> None:
        paths = self._mock_tracked(
            [
                ("a\nb\nc\n", ".py"),
                ("x\n", ".ts"),
                ("y\nz\n", ".js"),
            ],
            tmp_path,
        )
        with patch(
            "scripts.dev.update_readme_code_stats._tracked_files", return_value=paths
        ):
            table = build_table()
        lines = table.splitlines()
        py_idx = next(i for i, line in enumerate(lines) if line.startswith("| Python"))
        ts_idx = next(
            i for i, line in enumerate(lines) if line.startswith("| TypeScript")
        )
        js_idx = next(
            i for i, line in enumerate(lines) if line.startswith("| JavaScript")
        )
        assert py_idx < js_idx < ts_idx

    def test_unknown_extension_skipped(self, tmp_path: Path) -> None:
        paths = self._mock_tracked([("data\n", ".py"), ("binary\n", ".bin")], tmp_path)
        with patch(
            "scripts.dev.update_readme_code_stats._tracked_files", return_value=paths
        ):
            table = build_table()
        assert "Python" in table
        assert ".bin" not in table

    def test_no_tracked_files(self) -> None:
        with patch(
            "scripts.dev.update_readme_code_stats._tracked_files", return_value=[]
        ):
            table = build_table()
        assert "Python" not in table
        assert "**Total** | **0** | **0**" in table


class TestUpdateReadme:
    def _readme_with_markers(self, tmp_path: Path, body: str = "") -> Path:
        p = tmp_path / "README.md"
        p.write_text(f"prefix\n{START_MARKER}\n{body}\n{END_MARKER}\nsuffix\n")
        return p

    def test_replaces_content_between_markers(self, tmp_path: Path) -> None:
        p = self._readme_with_markers(tmp_path, "old content")
        with patch(
            "scripts.dev.update_readme_code_stats._tracked_files", return_value=[]
        ):
            changed = update_readme(p)
        content = p.read_text()
        assert changed is True
        assert "old content" not in content
        assert START_MARKER in content
        assert END_MARKER in content
        assert "suffix" in content
        assert "prefix" in content

    def test_returns_false_when_no_change(self, tmp_path: Path) -> None:
        p = self._readme_with_markers(tmp_path)
        with patch(
            "scripts.dev.update_readme_code_stats._tracked_files", return_value=[]
        ):
            update_readme(p)
        with patch(
            "scripts.dev.update_readme_code_stats._tracked_files", return_value=[]
        ):
            changed = update_readme(p)
        assert changed is False

    def test_raises_on_missing_markers(self, tmp_path: Path) -> None:
        p = tmp_path / "README.md"
        p.write_text("no markers here\n")
        with pytest.raises(ValueError, match="Missing markers"):
            update_readme(p)

    def test_raises_on_missing_end_marker(self, tmp_path: Path) -> None:
        p = tmp_path / "README.md"
        p.write_text(f"{START_MARKER}\ncontent\nno end marker\n")
        with pytest.raises(ValueError, match="Missing markers"):
            update_readme(p)

    def test_raises_on_reversed_markers(self, tmp_path: Path) -> None:
        p = tmp_path / "README.md"
        p.write_text(f"{END_MARKER}\n{START_MARKER}\n")
        with pytest.raises(ValueError, match="Missing markers"):
            update_readme(p)


class TestParseArgs:
    def test_defaults(self) -> None:
        with patch("sys.argv", ["prog"]):
            args = parse_args()
        assert args.check is False

    def test_check_flag(self) -> None:
        with patch("sys.argv", ["prog", "--check"]):
            args = parse_args()
        assert args.check is True

    def test_readme_path(self, tmp_path: Path) -> None:
        readme = tmp_path / "README.md"
        with patch("sys.argv", ["prog", "--readme", str(readme)]):
            args = parse_args()
        assert args.readme == readme


class TestTrackedFiles:
    def _init_repo(self, path: Path) -> None:
        subprocess.run(  # noqa: S603 — test fixture
            [_GIT, "init"],
            cwd=path,
            check=True,
            capture_output=True,
        )
        subprocess.run(  # noqa: S603 — test fixture
            [_GIT, "config", "user.email", "test@test.com"],
            cwd=path,
            check=True,
            capture_output=True,
        )
        subprocess.run(  # noqa: S603 — test fixture
            [_GIT, "config", "user.name", "Test"],
            cwd=path,
            check=True,
            capture_output=True,
        )

    def test_returns_git_tracked_files(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        tracked = repo / "tracked.py"
        tracked.write_text("x = 1\n")
        untracked = repo / "untracked.js"
        untracked.write_text("y = 2\n")
        subprocess.run(  # noqa: S603 — test fixture
            [_GIT, "add", "tracked.py"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(  # noqa: S603 — test fixture
            [_GIT, "commit", "-m", "init"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        with patch("scripts.dev.update_readme_code_stats.REPO_ROOT", repo):
            files = _tracked_files()
        paths = [str(f.relative_to(repo)) for f in files]
        assert "tracked.py" in paths
        assert "untracked.js" not in paths

    def test_handles_blank_line_in_git_output(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n")
        (tmp_path / "b.py").write_text("y = 2\n")
        fake_proc = subprocess.CompletedProcess(
            [], 0, stdout="a.py\n\nb.py\n", stderr=""
        )
        with (
            patch("subprocess.run", return_value=fake_proc),
            patch("scripts.dev.update_readme_code_stats.REPO_ROOT", tmp_path),
        ):
            files = _tracked_files()
        assert len(files) == 2

    def test_skips_deleted_tracked_files(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo2"
        repo.mkdir()
        self._init_repo(repo)
        f = repo / "gone.py"
        f.write_text("x = 1\n")
        subprocess.run(  # noqa: S603 — test fixture
            [_GIT, "add", "gone.py"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(  # noqa: S603 — test fixture
            [_GIT, "commit", "-m", "init"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        f.unlink()
        with patch("scripts.dev.update_readme_code_stats.REPO_ROOT", repo):
            files = _tracked_files()
        assert all(f_.is_file() for f_ in files)
        assert not any(f_.name == "gone.py" for f_ in files)


class TestMain:
    def test_returns_0_on_successful_update(self, tmp_path: Path) -> None:
        readme = tmp_path / "README.md"
        readme.write_text(f"prefix\n{START_MARKER}\nold\n{END_MARKER}\nsuffix\n")
        with (
            patch(
                "scripts.dev.update_readme_code_stats._tracked_files", return_value=[]
            ),
            patch("sys.argv", ["prog", "--readme", str(readme)]),
        ):
            rc = main()
        assert rc == 0

    def test_returns_1_with_check_and_changed(self, tmp_path: Path) -> None:
        readme = tmp_path / "README.md"
        readme.write_text(f"prefix\n{START_MARKER}\nold\n{END_MARKER}\nsuffix\n")
        with (
            patch(
                "scripts.dev.update_readme_code_stats._tracked_files", return_value=[]
            ),
            patch("sys.argv", ["prog", "--readme", str(readme), "--check"]),
        ):
            rc = main()
        assert rc == 1

    def test_returns_0_with_check_and_unchanged(self, tmp_path: Path) -> None:
        readme = tmp_path / "README.md"
        readme.write_text(f"prefix\n{START_MARKER}\n{END_MARKER}\nsuffix\n")
        with (
            patch(
                "scripts.dev.update_readme_code_stats._tracked_files", return_value=[]
            ),
            patch("sys.argv", ["prog", "--readme", str(readme)]),
        ):
            main()
        with (
            patch(
                "scripts.dev.update_readme_code_stats._tracked_files", return_value=[]
            ),
            patch("sys.argv", ["prog", "--readme", str(readme), "--check"]),
        ):
            rc = main()
        assert rc == 0

    def test_cli_entry_point(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "scripts.dev.update_readme_code_stats", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        assert "Update the autogenerated source line-count table" in result.stdout


class TestLanguageStats:
    def test_defaults(self) -> None:
        s = LanguageStats()
        assert s.files == 0
        assert s.lines == 0

    def test_custom_values(self) -> None:
        s = LanguageStats(files=3, lines=42)
        assert s.files == 3
        assert s.lines == 42
