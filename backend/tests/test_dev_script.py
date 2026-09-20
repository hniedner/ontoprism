"""Behaviour of ``scripts/dev.sh``, the process manager behind ``pdm run start-*``,
``stop-*`` and ``restart-*``.

Every test runs a copy of the script in its own directory: the script sources the
``.env`` of the directory above it, and the repository's must never decide what a
test signals.

The processes the tests spawn get their own session, so a signal aimed at a process
group reaches only the spawned process and not the test runner.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_LISTENER = """
import socket, sys, time
server = socket.socket()
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("127.0.0.1", int(sys.argv[1])))
server.listen()
print("listening", flush=True)
time.sleep(60)
"""

# The shape of both servers dev.sh starts: `pdm run uvicorn` and `npm run dev` stay
# alive as the parent of the process that actually serves.
_LEADER_WITH_CHILD = """
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
print(child.pid, flush=True)
time.sleep(60)
"""


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _spawn_listener(port: int) -> subprocess.Popen[str]:
    process = subprocess.Popen(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-c", _LISTENER, str(port)],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "listening"
    return process


def _spawn_leader_with_child() -> tuple[subprocess.Popen[str], int]:
    process = subprocess.Popen(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-c", _LEADER_WITH_CHILD],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert process.stdout is not None
    return process, int(process.stdout.readline())


def _start_time(pid: int) -> str:
    """The value dev.sh records beside a pid, normalised the way the script does."""
    ps = shutil.which("ps")
    assert ps is not None
    listing = subprocess.run(  # noqa: S603 - resolved executable, no shell
        [ps, "-o", "lstart=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=True,
    )
    return " ".join(listing.stdout.split())


def _write_pidfile(root: Path, target: str, pid: int, started: str) -> None:
    logs = root / ".dev-logs"
    logs.mkdir(exist_ok=True)
    (logs / f"{target}.pid").write_text(f"{pid}\n{started}\n")


def _is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_until_gone(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _is_running(pid):
            return True
        time.sleep(0.1)
    return False


def _script_copy(tmp_path: Path, dotenv: str = "") -> Path:
    (tmp_path / "scripts").mkdir()
    shutil.copy(REPO_ROOT / "scripts/dev.sh", tmp_path / "scripts/dev.sh")
    if dotenv:
        (tmp_path / ".env").write_text(dotenv)
    return tmp_path


_TARGETS = [("backend", "BACKEND_PORT"), ("frontend", "FRONTEND_PORT")]


def _stop(
    root: Path, environment: dict[str, str], target: str = "backend"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed shell and a copy of the repo script
        ["/bin/bash", "scripts/dev.sh", "stop", target],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _start(
    root: Path, environment: dict[str, str], target: str = "backend"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed shell and a copy of the repo script
        ["/bin/bash", "scripts/dev.sh", "start", target],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _reap(*processes: subprocess.Popen[str]) -> None:
    for process in processes:
        process.kill()
        process.wait()


@pytest.mark.unit
def test_stop_spares_a_listener_it_did_not_start(tmp_path: Path) -> None:
    """Another project's server on :8011 is not ours to kill: on 2026-09-18 a sweep
    over a port range took down this project's gvproxy the same way."""
    port = _free_port()
    stranger = _spawn_listener(port)
    try:
        result = _stop(
            _script_copy(tmp_path), {**os.environ, "BACKEND_PORT": str(port)}
        )

        assert result.returncode == 0, result.stderr
        assert not _wait_until_gone(stranger.pid, timeout=2)
        assert str(stranger.pid) in result.stdout
    finally:
        _reap(stranger)


@pytest.mark.unit
def test_stop_spares_a_pid_that_was_reused_after_our_process_exited(
    tmp_path: Path,
) -> None:
    """A pidfile outlives the process it names, and pids wrap; the recorded start
    time is what tells our process from a stranger that inherited its number."""
    port = _free_port()
    survivor = _spawn_listener(port)
    root = _script_copy(tmp_path)
    _write_pidfile(root, "backend", survivor.pid, "Thu Jan  1 00:00:00 1970")
    try:
        result = _stop(root, {**os.environ, "BACKEND_PORT": str(port)})

        assert result.returncode == 0, result.stderr
        assert "stale pidfile" in result.stdout
        assert not _wait_until_gone(survivor.pid, timeout=2)
    finally:
        _reap(survivor)


@pytest.mark.unit
def test_stop_signals_the_process_the_pidfile_records(tmp_path: Path) -> None:
    """The pidfile, not the port, selects what is signalled: the recorded process
    is stopped even though nothing listens on the port stop was aimed at."""
    ours = _spawn_listener(_free_port())
    root = _script_copy(tmp_path)
    _write_pidfile(root, "backend", ours.pid, _start_time(ours.pid))
    try:
        result = _stop(root, {**os.environ, "BACKEND_PORT": str(_free_port())})

        assert result.returncode == 0, result.stderr
        assert ours.wait(timeout=10) != 0
        assert not (root / ".dev-logs/backend.pid").exists()
    finally:
        _reap(ours)


@pytest.mark.unit
def test_stop_takes_down_the_server_behind_the_recorded_process(
    tmp_path: Path,
) -> None:
    """`pdm run uvicorn` and `npm run dev` serve from a child, so signalling the
    recorded pid alone would leave the actual server holding the port."""
    leader, child = _spawn_leader_with_child()
    root = _script_copy(tmp_path)
    _write_pidfile(root, "backend", leader.pid, _start_time(leader.pid))
    try:
        result = _stop(root, {**os.environ, "BACKEND_PORT": str(_free_port())})

        assert result.returncode == 0, result.stderr
        assert leader.wait(timeout=10) != 0
        assert _wait_until_gone(child)
    finally:
        _reap(leader)
        if _is_running(child):
            os.kill(child, 9)


@pytest.mark.unit
@pytest.mark.parametrize(("target", "variable"), _TARGETS)
def test_start_leaves_a_server_it_already_started_alone(
    target: str, variable: str, tmp_path: Path
) -> None:
    """The pidfile, not the port, says whether our server is up: a dev server that
    has not bound its port yet must not be started a second time."""
    ours = _spawn_listener(_free_port())
    root = _script_copy(tmp_path)
    _write_pidfile(root, target, ours.pid, _start_time(ours.pid))
    try:
        result = _start(root, {**os.environ, variable: str(_free_port())}, target)

        assert result.returncode == 0, result.stdout + result.stderr
        assert f"already running (pid {ours.pid})" in result.stdout
        assert not _wait_until_gone(ours.pid, timeout=2)
    finally:
        _reap(ours)


@pytest.mark.unit
@pytest.mark.parametrize(("target", "variable"), _TARGETS)
def test_start_refuses_a_port_held_by_a_process_it_did_not_start(
    target: str, variable: str, tmp_path: Path
) -> None:
    """Reporting a foreign holder as "already running" hides that the server never
    came up, and invites the next `stop` to kill a stranger."""
    port = _free_port()
    stranger = _spawn_listener(port)
    try:
        result = _start(
            _script_copy(tmp_path), {**os.environ, variable: str(port)}, target
        )

        assert result.returncode != 0
        assert str(stranger.pid) in result.stdout + result.stderr
        assert not _wait_until_gone(stranger.pid, timeout=2)
    finally:
        _reap(stranger)


@pytest.mark.unit
def test_no_script_selects_a_process_by_port_or_name() -> None:
    """The global guard against `lsof -ti :PORT | xargs kill`, `pkill` and friends is
    a Claude Code hook, and a hook cannot see inside a script this repository ships."""
    by_port_or_name = re.compile(
        r"lsof[^\n]*\bkill\b|xargs\s+kill|\bpkill\b|\bkillall\b|fuser\s+-k"
    )

    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}"
        for path in sorted((REPO_ROOT / "scripts").rglob("*"))
        if path.suffix in {".sh", ".py", ".mjs"}
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if by_port_or_name.search(line)
    ]

    assert offenders == []


@pytest.mark.unit
@pytest.mark.parametrize(("target", "variable"), _TARGETS)
def test_a_port_given_in_the_environment_wins_over_dotenv(
    target: str, variable: str, tmp_path: Path
) -> None:
    """``.env`` used to overwrite the caller's port, so a command aimed at one port
    acted on whatever held the other."""
    asked = in_dotenv = _free_port()
    while in_dotenv == asked:
        in_dotenv = _free_port()
    aimed_at = _spawn_listener(asked)
    bystander = _spawn_listener(in_dotenv)
    try:
        result = _stop(
            _script_copy(tmp_path, f"{variable}={in_dotenv}\n"),
            {**os.environ, variable: str(asked)},
            target,
        )

        assert result.returncode == 0, result.stderr
        assert str(asked) in result.stdout
        assert str(in_dotenv) not in result.stdout
        assert str(aimed_at.pid) in result.stdout
        assert str(bystander.pid) not in result.stdout
    finally:
        _reap(aimed_at, bystander)


@pytest.mark.unit
def test_without_lsof_the_script_refuses_instead_of_reporting_nothing_running(
    tmp_path: Path,
) -> None:
    """lsof is how the script sees a port held by a process it did not start. With it
    missing every lookup comes back empty, and a stranger on the port goes
    unmentioned."""
    without_lsof = tmp_path / "bin"
    without_lsof.mkdir()
    # The external commands dev.sh itself uses, so only lsof is missing and the script
    # would otherwise reach "was not running".
    for tool in ("dirname", "mkdir", "ps", "sleep"):
        found = shutil.which(tool)
        assert found is not None
        (without_lsof / tool).symlink_to(found)

    result = _stop(_script_copy(tmp_path), {"PATH": str(without_lsof)})

    assert result.returncode != 0
    assert "lsof" in result.stderr
    assert "not running" not in result.stdout


@pytest.mark.unit
@pytest.mark.parametrize(("target", "variable"), _TARGETS)
def test_a_port_that_is_not_a_number_is_refused(
    target: str, variable: str, tmp_path: Path
) -> None:
    """lsof exits 1 for a malformed port exactly as it does for "no listener", so
    ``stop`` used to print "was not running" whatever was running."""
    result = _stop(_script_copy(tmp_path), {**os.environ, variable: "80l1"}, target)

    assert result.returncode != 0
    assert variable in result.stderr
    assert "80l1" in result.stderr
    assert "not running" not in result.stdout
