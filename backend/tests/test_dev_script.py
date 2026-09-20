"""Behaviour of ``scripts/dev.sh``, the process manager behind ``pdm run start-*``,
``stop-*`` and ``restart-*``.

Every test that runs the script runs a copy of it in its own directory: the script
sources the ``.env`` of the directory above it, and the repository's must never decide
what a test signals.

The processes the tests spawn get their own session, so a signal aimed at a process
group reaches only that process and its children, never the test runner.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import signal
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

# What the `pdm` stub runs in the round-trip test: a launcher that stays alive as the
# parent of the process that binds the port, the way `pdm run uvicorn` and `npm run dev`
# do. dev.sh passes the port last.
_FAKE_SERVER = """
import subprocess, sys, time

child = subprocess.Popen(
    [sys.executable, "-c", LISTENER, sys.argv[-1]], stdout=subprocess.PIPE, text=True
)
assert child.stdout is not None
assert child.stdout.readline().strip() == "listening"
time.sleep(60)
"""

_CLIENT = """
import socket, sys, time
client = socket.create_connection(("127.0.0.1", int(sys.argv[1])))
print("connected", flush=True)
time.sleep(60)
"""

# A server that takes its time over a graceful shutdown: uvicorn draining an in-flight
# request, vite finishing a write. `stop` has to outlast it, not walk away from it.
_STUBBORN_LISTENER = """
import signal, socket, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
server = socket.socket()
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("127.0.0.1", int(sys.argv[1])))
server.listen()
print("listening", flush=True)
time.sleep(60)
"""

# The shape of both servers dev.sh starts: `pdm run uvicorn` and `npm run dev` stay
# alive as the parent of the process that serves, and neither forwards a signal.
_LEADER_OF = """
import subprocess, sys, time
child = subprocess.Popen(
    [sys.executable, "-c", sys.argv[1], sys.argv[2]], stdout=subprocess.PIPE, text=True
)
assert child.stdout.readline().strip() == "listening"
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


def _spawn_client(port: int) -> subprocess.Popen[str]:
    process = subprocess.Popen(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-c", _CLIENT, str(port)],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "connected"
    return process


def _spawn_leader_of(child: str, port: int) -> tuple[subprocess.Popen[str], int]:
    """A process-group leader whose child holds ``port``; returns both pids."""
    process = subprocess.Popen(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-c", _LEADER_OF, child, str(port)],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert process.stdout is not None
    return process, int(process.stdout.readline())


def _path_with_stubs(tmp_path: Path, **stubs: str) -> dict[str, str]:
    """An environment whose PATH shadows each named tool with a one-line script."""
    directory = tmp_path / "stubs"
    directory.mkdir()
    for name, body in stubs.items():
        stub = directory / name
        stub.write_text(f"#!/bin/sh\n{body}\n")
        stub.chmod(0o755)
    return {**os.environ, "PATH": f"{directory}{os.pathsep}{os.environ['PATH']}"}


def _stubs_that_launch_a_fake_server(tmp_path: Path) -> dict[str, str]:
    """PATH stubs that make `start backend` launch :data:`_FAKE_SERVER` instead of
    uvicorn, so a test can drive a real ``start`` and ``stop`` round trip."""
    server = tmp_path / "fake_server.py"
    server.write_text(f"LISTENER = {_LISTENER!r}\n{_FAKE_SERVER}")
    return _path_with_stubs(
        tmp_path,
        docker="echo ontoprism-postgres",
        pdm=f'exec "{sys.executable}" "{server}" "$@"',
    )


def _is_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


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


def _reap_group(process: subprocess.Popen[str]) -> None:
    """Clean up a leader spawned by :func:`_spawn_leader_of` along with its child.

    macOS reports EPERM rather than ESRCH for a process group that is already gone.
    """
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)
    _reap(process)


@pytest.mark.unit
def test_a_started_server_is_stopped_again_through_the_script(tmp_path: Path) -> None:
    """The one test that drives both halves. `start` has to leave the launched process
    leading its own group — without that, `stop`'s group signal reaches nothing and the
    server survives every other test in this file."""
    port = _free_port()
    root = _script_copy(tmp_path)
    environment = _stubs_that_launch_a_fake_server(tmp_path)
    environment["BACKEND_PORT"] = str(port)
    leader = 0
    try:
        started = _start(root, environment)

        assert started.returncode == 0, started.stdout + started.stderr
        assert _is_listening(port)
        recorded = (root / ".dev-logs/backend.pid").read_text().splitlines()
        leader = int(recorded[0])
        assert recorded[1] == _start_time(leader)
        assert os.getpgid(leader) == leader

        stopped = _stop(root, environment)

        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        assert not _is_listening(port)
        assert not (root / ".dev-logs/backend.pid").exists()
    finally:
        if leader and _is_running(leader):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(leader, signal.SIGKILL)


@pytest.mark.unit
def test_start_does_not_launch_a_second_server_when_it_cannot_tell(
    tmp_path: Path,
) -> None:
    """The start-side twin of the broken-`ps` case: an unanswered "is ours still
    running?" must not read as "no", or `start` puts a second server on the port."""
    ours = _spawn_listener(_free_port())
    root = _script_copy(tmp_path)
    _write_pidfile(root, "backend", ours.pid, _start_time(ours.pid))
    environment = _path_with_stubs(
        tmp_path, ps='echo "ps: broken" >&2; exit 2', docker="echo ontoprism-postgres"
    )
    environment["BACKEND_PORT"] = str(_free_port())
    try:
        result = _start(root, environment)

        assert result.returncode != 0
        assert "cannot tell" in result.stdout + result.stderr
        assert "http://localhost" not in result.stdout
    finally:
        _reap(ours)


@pytest.mark.unit
def test_stopping_all_stops_the_second_target_and_still_reports_the_failure(
    tmp_path: Path,
) -> None:
    """`stop all` used to abort on the first failing target under `set -e`, leaving the
    other server running with no sign that anything was skipped."""
    frontend_port, backend_port = _free_port(), _free_port()
    unreachable_leader, unreachable = _spawn_leader_of(_LISTENER, frontend_port)
    reachable = _spawn_listener(backend_port)
    root = _script_copy(tmp_path)
    # A pid that leads no group: its stop fails, and the backend's must still run.
    _write_pidfile(root, "frontend", unreachable, _start_time(unreachable))
    _write_pidfile(root, "backend", reachable.pid, _start_time(reachable.pid))
    try:
        result = _stop(
            root,
            {
                **os.environ,
                "FRONTEND_PORT": str(frontend_port),
                "BACKEND_PORT": str(backend_port),
            },
            "all",
        )

        assert result.returncode != 0
        assert "frontend did not stop" in result.stdout
        assert reachable.wait(timeout=10) != 0
        assert not (root / ".dev-logs/backend.pid").exists()
        assert (root / ".dev-logs/frontend.pid").exists()
    finally:
        _reap_group(unreachable_leader)
        _reap(reachable)


@pytest.mark.unit
def test_stop_spares_a_listener_it_did_not_start(tmp_path: Path) -> None:
    """Another project's server on a dev port is not ours to kill: on 2026-09-18 a
    sweep over a port range took down this project's gvproxy the same way."""
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
    port = _free_port()
    leader, child = _spawn_leader_of(_LISTENER, port)
    root = _script_copy(tmp_path)
    _write_pidfile(root, "backend", leader.pid, _start_time(leader.pid))
    try:
        result = _stop(root, {**os.environ, "BACKEND_PORT": str(port)})

        assert result.returncode == 0, result.stderr
        assert leader.wait(timeout=10) != 0
        assert _wait_until_gone(child)
        assert not _is_listening(port)
    finally:
        _reap_group(leader)


@pytest.mark.unit
def test_stop_outlasts_a_server_that_outlives_its_launcher(tmp_path: Path) -> None:
    """The launcher dies on the first TERM while the server drains. Watching only the
    recorded pid, `stop` reported success here, deleted the pidfile, and left the real
    server holding the port with nothing left that knew it was ours."""
    port = _free_port()
    leader, child = _spawn_leader_of(_STUBBORN_LISTENER, port)
    root = _script_copy(tmp_path)
    _write_pidfile(root, "backend", leader.pid, _start_time(leader.pid))
    try:
        result = _stop(root, {**os.environ, "BACKEND_PORT": str(port)})

        assert result.returncode == 0, result.stdout + result.stderr
        assert _wait_until_gone(child)
        assert not _is_listening(port)
        assert "no record of starting" not in result.stdout
        assert not (root / ".dev-logs/backend.pid").exists()
    finally:
        _reap_group(leader)


@pytest.mark.unit
def test_stop_keeps_the_pidfile_when_it_cannot_reach_the_recorded_process(
    tmp_path: Path,
) -> None:
    """`kill -TERM -<pid>` reaches nothing when the recorded pid leads no process
    group. Reporting that as "stopped" would throw away the only record of a live
    server, so the reject branch has to fire and keep the pidfile."""
    port = _free_port()
    leader, child = _spawn_leader_of(_LISTENER, port)
    root = _script_copy(tmp_path)
    _write_pidfile(root, "backend", child, _start_time(child))
    try:
        result = _stop(root, {**os.environ, "BACKEND_PORT": str(port)})

        assert result.returncode != 0
        assert "did not stop" in result.stdout + result.stderr
        assert not _wait_until_gone(child, timeout=1)
        assert (root / ".dev-logs/backend.pid").exists()
    finally:
        _reap_group(leader)


@pytest.mark.unit
@pytest.mark.parametrize("field", [" {pid}", "abc", "{pid} 999"])
def test_a_pid_field_that_is_not_a_plain_pid_signals_nothing(
    field: str, tmp_path: Path
) -> None:
    """The pid field is pasted into `kill -<pid>`, so it is checked before it gets
    there, the way the ports are. (`0` and `1`, which would make that a broadcast, are
    rejected by the same check but cannot be a parameter here: the test would have to
    record the real start time of pid 1 to isolate the check, and a regression would
    then have the suite signal every process on the machine.)"""
    bystander = _spawn_listener(_free_port())
    root = _script_copy(tmp_path)
    _write_pidfile(root, "backend", bystander.pid, _start_time(bystander.pid))
    (root / ".dev-logs/backend.pid").write_text(
        f"{field.format(pid=bystander.pid)}\n{_start_time(bystander.pid)}\n"
    )
    try:
        result = _stop(root, {**os.environ, "BACKEND_PORT": str(_free_port())})

        assert result.returncode == 0, result.stdout + result.stderr
        assert "stale pidfile" in result.stdout
        assert not _wait_until_gone(bystander.pid, timeout=1)
    finally:
        _reap(bystander)


@pytest.mark.unit
def test_a_broken_port_lookup_is_not_read_as_a_free_port(tmp_path: Path) -> None:
    """lsof exits 1 for "nothing listening"; a higher status is a broken lookup. Taking
    it for a free port makes `start` launch a second server onto an occupied port and
    silences the warning `stop` owes the user."""
    environment = _path_with_stubs(
        tmp_path, lsof='echo "lsof: cannot open /dev/kmem" >&2; exit 2'
    )
    environment["BACKEND_PORT"] = str(_free_port())
    root = _script_copy(tmp_path)

    started = _start(root, environment)
    stopped = _stop(root, environment)

    assert started.returncode != 0
    assert "http://localhost" not in started.stdout
    assert stopped.returncode != 0
    assert "lsof" in stopped.stdout + stopped.stderr


@pytest.mark.unit
def test_a_broken_process_lookup_does_not_delete_a_live_pidfile(tmp_path: Path) -> None:
    """ps exits 1 for "no such process"; a higher status says the question was not
    answered. Reading that as "gone" printed a green "stale pidfile" over a running
    server and removed the only record of it."""
    ours = _spawn_listener(_free_port())
    root = _script_copy(tmp_path)
    _write_pidfile(root, "backend", ours.pid, _start_time(ours.pid))
    environment = _path_with_stubs(tmp_path, ps='echo "ps: broken" >&2; exit 2')
    environment["BACKEND_PORT"] = str(_free_port())
    try:
        result = _stop(root, environment)

        assert result.returncode != 0
        assert "stale pidfile" not in result.stdout
        assert (root / ".dev-logs/backend.pid").exists()
        assert not _wait_until_gone(ours.pid, timeout=1)
    finally:
        _reap(ours)


@pytest.mark.unit
def test_start_reports_a_server_that_exited_instead_of_a_url(tmp_path: Path) -> None:
    """A crash on startup used to print the green "→ http://localhost:…" line with a
    pid that was already gone, and the next `stop` called that a stale pidfile."""
    root = _script_copy(tmp_path)
    environment = _path_with_stubs(
        tmp_path,
        docker="echo ontoprism-postgres",
        pdm='echo "boom" >&2; exit 1',
    )
    environment["BACKEND_PORT"] = str(_free_port())

    result = _start(root, environment)

    assert result.returncode != 0
    assert "http://localhost" not in result.stdout
    assert "exited immediately" in result.stdout + result.stderr
    assert not (root / ".dev-logs/backend.pid").exists()


@pytest.mark.unit
def test_a_client_of_the_port_is_not_reported_as_holding_it(tmp_path: Path) -> None:
    """A bare `lsof -i :PORT` also lists every client of the port — a browser tab on the
    dev server, a proxy — and naming one as the holder would make `start` refuse a port
    that is free."""
    port = _free_port()
    listener = _spawn_listener(port)
    client = _spawn_client(port)
    try:
        result = _stop(
            _script_copy(tmp_path), {**os.environ, "BACKEND_PORT": str(port)}
        )

        assert result.returncode == 0, result.stderr
        assert str(listener.pid) in result.stdout
        assert str(client.pid) not in result.stdout
    finally:
        _reap(listener, client)


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


def _selects_a_process_by_port_or_name(line: str) -> bool:
    """`kill -9 $(lsof -ti:8011)` puts the kill first, so matching `lsof … kill` in that
    order alone let the usual spelling through: the two only have to share a line."""
    if re.search(r"\bpkill\b|\bkillall\b|fuser\s+-k", line):
        return True
    return bool(re.search(r"\bkill\b", line) and re.search(r"\blsof\b", line))


@pytest.mark.unit
def test_no_script_under_scripts_selects_a_process_by_port_or_name() -> None:
    """The guard against `lsof -ti :PORT | xargs kill`, `pkill` and friends at an
    agent's prompt is a Claude Code hook, and a hook sees nothing a script does. This
    covers `scripts/` only; the rest of the tree has no process management in it."""
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}"
        for path in sorted((REPO_ROOT / "scripts").rglob("*"))
        if path.is_file() and path.suffix in {"", ".sh", ".py", ".mjs"}
        for number, line in enumerate(
            path.read_text(errors="ignore").splitlines(), start=1
        )
        if _selects_a_process_by_port_or_name(line)
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
@pytest.mark.parametrize("missing", ["lsof", "ps"])
def test_a_missing_lookup_tool_refuses_instead_of_reporting_nothing_running(
    missing: str, tmp_path: Path
) -> None:
    """lsof is how the script sees who holds a port and ps how it sees whether the
    recorded process is still ours. With either missing, every lookup comes back empty
    and the script would report a free port and a stopped server."""
    only_the_rest = tmp_path / "bin"
    only_the_rest.mkdir()
    # The external commands the stop path needs before it reports anything, so the
    # missing tool is the only reason the script can fail.
    for tool in ("dirname", "lsof", "mkdir", "ps", "sleep"):
        if tool == missing:
            continue
        found = shutil.which(tool)
        assert found is not None
        (only_the_rest / tool).symlink_to(found)

    result = _stop(_script_copy(tmp_path), {"PATH": str(only_the_rest)})

    assert result.returncode != 0
    assert missing in result.stderr
    assert "not running" not in result.stdout


@pytest.mark.unit
@pytest.mark.parametrize(("target", "variable"), _TARGETS)
def test_a_port_that_is_not_a_number_is_refused(
    target: str, variable: str, tmp_path: Path
) -> None:
    """lsof exits 1 for a malformed port exactly as it does for "no listener", so a
    port that is silently wrong would leave a foreign holder unreported by ``stop`` and
    unnoticed by ``start``."""
    result = _stop(_script_copy(tmp_path), {**os.environ, variable: "80l1"}, target)

    assert result.returncode != 0
    assert variable in result.stderr
    assert "80l1" in result.stderr
    assert "not running" not in result.stdout
