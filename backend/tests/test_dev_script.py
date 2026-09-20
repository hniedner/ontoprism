"""Behaviour of ``scripts/dev/servers.py``, the process manager behind
``pdm run start-*``, ``stop-*`` and ``restart-*``.

Every test that runs the script runs a copy of it in its own directory: the script
reads the ``.env`` of the directory above it and keeps its records there, and the
repository's must never decide what a test signals.

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

import psutil
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = ".dev-logs"

_LISTENER = """
import socket, sys, time
server = socket.socket()
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("127.0.0.1", int(sys.argv[1])))
server.listen()
print("listening", flush=True)
time.sleep(60)
"""

# vite binds `localhost`, which resolves to ::1 on this machine. Every other double
# here binds IPv4, which is how an IPv4-only readiness probe passed the whole suite
# while the real frontend never registered as ready.
_LISTENER_V6 = """
import socket, sys, time
server = socket.socket(socket.AF_INET6)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("::1", int(sys.argv[1])))
server.listen()
print("listening", flush=True)
time.sleep(60)
"""

# What the `pdm` stub runs in the round-trip test: a launcher that stays alive as the
# parent of the process that binds the port, the way `pdm run uvicorn` and `npm run dev`
# do. It finds the port by name in the argv the script passes.
_FAKE_SERVER = """
import subprocess, sys, time

# `--port N`, because the frontend command puts `--strictPort` last: a stub reading
# argv[-1] would take whatever came last and never exercise the real argument.
port = sys.argv[sys.argv.index("--port") + 1]
child = subprocess.Popen(
    [sys.executable, "-c", LISTENER, port], stdout=subprocess.PIPE, text=True
)
assert child.stdout is not None
assert child.stdout.readline().strip() == "listening"
time.sleep(60)
"""

# Something that answers exactly one connection and then goes: a server in the middle
# of restarting, a health probe's peer. `port_answers` opens a socket, so it counts this
# as an answer -- and by the time `lsof` looks, there is nothing on the port at all.
_ONE_SHOT_LISTENER = """
import socket, sys
server = socket.socket()
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("127.0.0.1", int(sys.argv[1])))
server.listen()
print("listening", flush=True)
server.accept()
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

# The shape of both servers the script starts: `pdm run uvicorn` and `npm run dev` stay
# alive as the parent of the process that serves. Both do forward SIGTERM
# (measured), so the double earns its place by being a two-process tree: the group
# is the handle that reaches the server whatever the launcher does.
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


# `pdm`/`npm` dying while the server they started keeps running: the launcher exits as
# soon as the child is up, so the recorded pid is gone and its group is not.
_LEADER_THAT_EXITS = """
import subprocess, sys
child = subprocess.Popen(
    [sys.executable, "-c", sys.argv[1], sys.argv[2]], stdout=subprocess.PIPE, text=True
)
assert child.stdout is not None
assert child.stdout.readline().strip() == "listening"
print(child.pid, flush=True)
"""


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


def _spawn_leader_that_exits(port: int) -> tuple[int, int]:
    """Returns (leader pid, child pid) once the leader has exited."""
    process = subprocess.Popen(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-c", _LEADER_THAT_EXITS, _LISTENER, str(port)],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert process.stdout is not None
    child = int(process.stdout.readline())
    assert process.wait(timeout=10) == 0
    return process.pid, child


def _path_with_stubs(tmp_path: Path, **stubs: str) -> dict[str, str]:
    """An environment whose PATH shadows each named tool with a one-line script."""
    directory = tmp_path / "stubs"
    directory.mkdir()
    for name, body in stubs.items():
        stub = directory / name
        stub.write_text(f"#!/bin/sh\n{body}\n")
        stub.chmod(0o755)
    return {**os.environ, "PATH": f"{directory}{os.pathsep}{os.environ['PATH']}"}


def _stubs_that_launch_a_fake_server(
    tmp_path: Path, listener: str = _LISTENER
) -> dict[str, str]:
    """PATH stubs that make `start backend` launch :data:`_FAKE_SERVER` instead of
    uvicorn, so a test can drive a real ``start`` and ``stop`` round trip."""
    server = tmp_path / "fake_server.py"
    server.write_text(f"LISTENER = {listener!r}\n{_FAKE_SERVER}")
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


def _start_time(pid: int) -> float:
    """The value the script records beside a pid."""
    return psutil.Process(pid).create_time()


def _write_pidfile(root: Path, target: str, pid: int, started: float) -> None:
    logs = root / ".dev-logs"
    logs.mkdir(exist_ok=True)
    (logs / f"{target}.pid").write_text(f"{pid} {started}\n")


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
    (tmp_path / "scripts/dev").mkdir(parents=True)
    shutil.copy(
        REPO_ROOT / "scripts/dev/servers.py", tmp_path / "scripts/dev/servers.py"
    )
    if dotenv:
        (tmp_path / ".env").write_text(dotenv)
    return tmp_path


_TARGETS = [("backend", "BACKEND_PORT"), ("frontend", "FRONTEND_PORT")]


def _stop(
    root: Path, environment: dict[str, str], target: str = "backend"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and a copy of the repo script
        [sys.executable, "scripts/dev/servers.py", "stop", target],
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
    return subprocess.run(  # noqa: S603 - fixed interpreter and a copy of the repo script
        [sys.executable, "scripts/dev/servers.py", "start", target],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _restart(
    root: Path, environment: dict[str, str], target: str = "backend"
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and a copy of the repo script
        [sys.executable, "scripts/dev/servers.py", "restart", target],
        cwd=root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
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
    """The backend half of the round trip; the frontend's is below. `start` has to
    leave the launched process leading its own group — without that, `stop`'s group
    signal reaches nothing and the server survives every other test in this file."""
    port = _free_port()
    root = _script_copy(tmp_path)
    environment = _stubs_that_launch_a_fake_server(tmp_path)
    environment["BACKEND_PORT"] = str(port)
    leader = 0
    try:
        started = _start(root, environment)

        assert started.returncode == 0, started.stdout + started.stderr
        assert _is_listening(port)
        recorded = (root / ".dev-logs/backend.pid").read_text().split()
        leader = int(recorded[0])
        assert float(recorded[1]) == _start_time(leader)
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
def test_start_does_not_launch_a_second_server_when_it_cannot_read_the_record(
    tmp_path: Path,
) -> None:
    """An unanswered "is ours still running?" must not read as "no", or `start` puts
    a second server on the port."""
    ours = _spawn_listener(_free_port())
    root = _script_copy(tmp_path)
    _write_pidfile(root, "backend", ours.pid, _start_time(ours.pid))
    (root / ".dev-logs/backend.pid").chmod(0o000)
    environment = _path_with_stubs(tmp_path, docker="echo ontoprism-postgres")
    environment["BACKEND_PORT"] = str(_free_port())
    try:
        result = _start(root, environment)

        assert result.returncode != 0
        assert "cannot read" in result.stdout
        assert "http://localhost" not in result.stdout
    finally:
        (root / ".dev-logs/backend.pid").chmod(0o600)
        _reap(ours)


@pytest.mark.unit
def test_stopping_all_stops_the_second_target_and_still_reports_the_failure(
    tmp_path: Path,
) -> None:
    """Every target is attempted even when an earlier one fails, and the failure is
    still reported. Giving up after the frontend leaves the backend running with no
    sign that anything was skipped."""
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
    _write_pidfile(root, "backend", survivor.pid, 1.0)
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
        assert "has no record of starting" not in result.stdout
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
@pytest.mark.parametrize("field", [" {pid}", "abc", ""])
def test_a_record_that_cannot_be_parsed_is_not_a_verdict(
    field: str, tmp_path: Path
) -> None:
    """A record that cannot be parsed is not a verdict: it must not read as "nothing
    of ours is running", because that deletes the only record of a live server. The
    pid also reaches `os.killpg`, so the field is bounded before it gets there, the
    way the ports are. `write_record` truncates before it writes, so a half-written
    record is a state that happens."""
    bystander = _spawn_listener(_free_port())
    root = _script_copy(tmp_path)
    (root / ".dev-logs").mkdir(exist_ok=True)
    (root / ".dev-logs/backend.pid").write_text(field.format(pid=bystander.pid))
    try:
        result = _stop(root, {**os.environ, "BACKEND_PORT": str(_free_port())})

        assert result.returncode != 0
        assert "was not running" not in result.stdout
        assert (root / ".dev-logs/backend.pid").exists()
        assert not _wait_until_gone(bystander.pid, timeout=1)
    finally:
        _reap(bystander)


@pytest.mark.unit
@pytest.mark.parametrize("pid_field", ["0", "1"])
def test_a_recorded_pid_that_would_signal_a_foreign_group_is_refused(
    pid_field: str, tmp_path: Path
) -> None:
    """`os.killpg` with 0 addresses the script's own process group and with 1 a group
    that is not ours, so neither may reach a signal."""
    root = _script_copy(tmp_path)
    (root / ".dev-logs").mkdir(exist_ok=True)
    (root / ".dev-logs/backend.pid").write_text(f"{pid_field} 1.0\n")

    result = _stop(root, {**os.environ, "BACKEND_PORT": str(_free_port())})

    assert result.returncode != 0
    assert "nothing signalled" in result.stdout
    assert (root / ".dev-logs/backend.pid").exists()


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
    # `stop`'s own verdict stands; who else holds the port is advisory, and a lookup
    # that cannot answer must not turn a successful stop into a failure.
    assert stopped.returncode == 0, stopped.stdout + stopped.stderr
    assert "could not check who holds" in stopped.stdout


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
    # Two honest answers to the same state, and which one wins is a race on how fast
    # the stub launcher exits: `_await_listening` says "pid N is gone" when the process
    # is already unreachable at its first look and "exited while waiting" when it dies
    # inside the loop. Pinning one of them would make this test flaky, not stricter.
    assert "exited while waiting for" in result.stdout or "is gone" in result.stdout
    assert f"{LOG_DIR}/backend.log" in result.stdout + result.stderr
    assert not (root / ".dev-logs/backend.pid").exists()


@pytest.mark.unit
def test_stop_keeps_a_record_whose_pid_is_gone_while_its_group_still_runs(
    tmp_path: Path,
) -> None:
    """The launcher exited before `stop` ran, but the server it started is still on
    the port. Calling that a stale pidfile deletes the only record that the process is
    ours and leaves it to be found by port. It is not signalled either: once the group
    empties the pid can be reused, and a reused pid leading a group looks the same."""
    port = _free_port()
    leader, child = _spawn_leader_that_exits(port)
    root = _script_copy(tmp_path)
    _write_pidfile(root, "backend", leader, 1.0)
    try:
        result = _stop(root, {**os.environ, "BACKEND_PORT": str(port)})

        assert result.returncode != 0
        assert str(child) in result.stdout
        assert "stale pidfile" not in result.stdout
        assert (root / ".dev-logs/backend.pid").exists()
        assert not _wait_until_gone(child, timeout=1)
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(child, signal.SIGKILL)


@pytest.mark.unit
def test_a_record_that_cannot_be_read_is_not_a_verdict(tmp_path: Path) -> None:
    """An unreadable pidfile used to decode as "not ours": green "stale pidfile", the
    record deleted, and the live server it named left to be blamed on a stranger."""
    ours = _spawn_listener(_free_port())
    root = _script_copy(tmp_path)
    _write_pidfile(root, "backend", ours.pid, _start_time(ours.pid))
    (root / ".dev-logs/backend.pid").chmod(0o000)
    try:
        result = _stop(root, {**os.environ, "BACKEND_PORT": str(_free_port())})

        assert result.returncode != 0
        assert "cannot read" in result.stdout
        assert "stale pidfile" not in result.stdout
        assert (root / ".dev-logs/backend.pid").exists()
        assert not _wait_until_gone(ours.pid, timeout=1)
    finally:
        (root / ".dev-logs/backend.pid").chmod(0o600)
        _reap(ours)


@pytest.mark.unit
def test_a_server_on_the_ipv6_loopback_counts_as_listening(tmp_path: Path) -> None:
    """vite binds `localhost`, which resolves to ::1 here, so probing only 127.0.0.1
    reported the real frontend as never coming up and left it running unannounced."""
    port = _free_port()
    root = _script_copy(tmp_path)
    environment = _stubs_that_launch_a_fake_server(tmp_path, _LISTENER_V6)
    environment["BACKEND_PORT"] = str(port)
    # Below the harness timeout, so a regression fails on the assertion below rather
    # than racing `_start`'s own 30s bound.
    environment["ONTOPRISM_DEV_READY_SECONDS"] = "5"
    leader = 0
    try:
        result = _start(root, environment)

        assert result.returncode == 0, result.stdout + result.stderr
        assert f"http://localhost:{port}" in result.stdout
        assert "not listening yet" not in result.stdout
        leader = int((root / ".dev-logs/backend.pid").read_text().split()[0])
    finally:
        if leader:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(leader, signal.SIGKILL)


@pytest.mark.unit
def test_start_does_not_hand_out_a_url_for_a_server_that_dies_while_starting(
    tmp_path: Path,
) -> None:
    """A fixed pause after launch only catches an instant crash; a server failing a
    second later still got a green URL, and the next `stop` called that a stale
    pidfile."""
    root = _script_copy(tmp_path)
    environment = _path_with_stubs(
        tmp_path,
        docker="echo ontoprism-postgres",
        pdm="sleep 1.2; exit 1",
    )
    environment["BACKEND_PORT"] = str(_free_port())

    result = _start(root, environment)

    assert result.returncode != 0
    assert "http://localhost" not in result.stdout
    assert "exited while waiting" in result.stdout
    assert not (root / ".dev-logs/backend.pid").exists()


@pytest.mark.unit
def test_data_services_that_fail_to_start_are_reported(tmp_path: Path) -> None:
    """`docker compose up -d >/dev/null 2>&1 || true` hid the reason the stack was
    down, leaving the user to debug the app instead of the services."""
    port, frontend_port = _free_port(), _free_port()
    stranger = _spawn_listener(port)
    frontend_stranger = _spawn_listener(frontend_port)
    root = _script_copy(tmp_path)
    environment = _path_with_stubs(
        tmp_path,
        docker='[ "$1" = "compose" ] && { echo "boom" >&2; exit 1; }; echo none',
    )
    # Both ports, because `start all` names both targets: an unset one falls back to
    # the real dev port, and the test would then ask about the developer's own server.
    environment["BACKEND_PORT"] = str(port)
    environment["FRONTEND_PORT"] = str(frontend_port)
    try:
        result = _start(root, environment, "all")

        assert "docker compose did not start the data services" in result.stdout
        assert (root / ".dev-logs/compose.log").read_text().strip() == "boom"
    finally:
        _reap(stranger, frontend_stranger)


@pytest.mark.unit
def test_start_refuses_to_overwrite_a_record_whose_group_still_runs(
    tmp_path: Path,
) -> None:
    """`stop` refuses to touch this state; `start` overwriting the record would leave
    those processes with nothing that knows they are ours, so no later `stop` could
    ever reach them."""
    port = _free_port()
    leader, child = _spawn_leader_that_exits(port)
    root = _script_copy(tmp_path)
    _write_pidfile(root, "backend", leader, 1.0)
    environment = _path_with_stubs(tmp_path, docker="echo ontoprism-postgres")
    environment["BACKEND_PORT"] = str(_free_port())
    try:
        result = _start(root, environment)

        assert result.returncode != 0
        assert str(child) in result.stdout
        assert "http://localhost" not in result.stdout
        assert (root / ".dev-logs/backend.pid").read_text().split()[0] == str(leader)
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(child, signal.SIGKILL)


@pytest.mark.unit
def test_stopping_all_attempts_every_target_when_one_cannot_be_answered(
    tmp_path: Path,
) -> None:
    """An unanswerable question about one target must not skip the others: giving up
    after the frontend leaves the backend running and unmentioned."""
    backend_port = _free_port()
    ours = _spawn_listener(backend_port)
    root = _script_copy(tmp_path)
    _write_pidfile(root, "backend", ours.pid, _start_time(ours.pid))
    (root / ".dev-logs/frontend.pid").write_text("not a record at all\n")
    try:
        result = _stop(
            root,
            {
                **os.environ,
                "FRONTEND_PORT": str(_free_port()),
                "BACKEND_PORT": str(backend_port),
            },
            "all",
        )

        assert result.returncode != 0
        assert "frontend" in result.stdout
        assert ours.wait(timeout=10) != 0
        assert not (root / ".dev-logs/backend.pid").exists()
    finally:
        _reap(ours)


@pytest.mark.unit
def test_a_port_lsof_cannot_see_is_not_reported_as_free(tmp_path: Path) -> None:
    """Unprivileged lsof reports another user's socket exactly as it reports a free
    port: exit 1, both streams empty. Reading that as "free" makes `start` launch a
    second server onto an occupied port."""
    port = _free_port()
    stranger = _spawn_listener(port)
    root = _script_copy(tmp_path)
    environment = _path_with_stubs(
        tmp_path, lsof="exit 1", docker="echo ontoprism-postgres"
    )
    environment["BACKEND_PORT"] = str(port)
    try:
        result = _start(root, environment)

        assert result.returncode != 0
        assert "lsof cannot see it" in result.stdout
        assert "http://localhost" not in result.stdout
        assert not _wait_until_gone(stranger.pid, timeout=1)
    finally:
        _reap(stranger)


@pytest.mark.unit
@pytest.mark.parametrize("port", ["0", "99999"])
def test_a_port_outside_the_usable_range_is_refused(port: str, tmp_path: Path) -> None:
    """lsof answers an out-of-range port exactly as it answers a free one, so a port
    that is silently wrong would read as "nothing there". `--port 0` is worse than a
    no-op: uvicorn binds a random port under a record whose port field means
    nothing."""
    result = _stop(_script_copy(tmp_path), {**os.environ, "BACKEND_PORT": port})

    assert result.returncode != 0
    assert "between 1 and 65535" in result.stdout
    assert "not running" not in result.stdout


@pytest.mark.unit
def test_start_reports_a_server_that_never_listens(tmp_path: Path) -> None:
    """An unanswered "is it serving?" is not a success. The record is kept so `stop`
    can still reach the process."""
    root = _script_copy(tmp_path)
    environment = _path_with_stubs(
        tmp_path, docker="echo ontoprism-postgres", pdm="sleep 60"
    )
    environment["BACKEND_PORT"] = str(_free_port())
    environment["ONTOPRISM_DEV_READY_SECONDS"] = "1"
    leader = 0
    try:
        result = _start(root, environment)

        assert result.returncode != 0
        assert "did not listen" in result.stdout
        assert "http://localhost" not in result.stdout
        # The record is kept, and names the process that is actually running, so a
        # later `stop` can still reach it.
        leader = int((root / ".dev-logs/backend.pid").read_text().split()[0])
        assert _is_running(leader)
        stopped = _stop(root, environment)
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        assert _wait_until_gone(leader)
    finally:
        if leader:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(leader, signal.SIGKILL)


@pytest.mark.unit
def test_a_server_that_goes_down_on_term_is_not_killed(tmp_path: Path) -> None:
    """The graceful window exists so uvicorn can drain a request and vite can finish
    a write. Without it `stop` is just TERM-then-KILL, and nothing would notice."""
    ours = _spawn_listener(_free_port())
    root = _script_copy(tmp_path)
    _write_pidfile(root, "backend", ours.pid, _start_time(ours.pid))
    try:
        result = _stop(root, {**os.environ, "BACKEND_PORT": str(_free_port())})

        assert result.returncode == 0, result.stdout + result.stderr
        # -SIGTERM, not -SIGKILL: it was asked to go, not forced.
        assert ours.wait(timeout=10) == -signal.SIGTERM
    finally:
        _reap(ours)


@pytest.mark.unit
def test_an_unreaped_process_does_not_keep_its_group_alive(tmp_path: Path) -> None:
    """On Linux an exited-but-unreaped process is still in its group and `getpgid`
    still answers for it, so `group_members`' zombie filter is what stops it keeping
    the group alive. On macOS `getpgid` raises for it instead, and what this pins is
    `start_time_of`'s zombie check — both must survive, and only CI exercises the
    first."""
    port = _free_port()
    leader, child = _spawn_leader_of(_LISTENER, port)
    root = _script_copy(tmp_path)
    _write_pidfile(root, "backend", leader.pid, _start_time(leader.pid))
    try:
        result = _stop(root, {**os.environ, "BACKEND_PORT": str(port)})

        # The test process is the leader's parent and never reaps it, so the group
        # holds an unreaped member for the whole of `stop`.
        assert result.returncode == 0, result.stdout + result.stderr
        assert "did not stop" not in result.stdout
        assert _wait_until_gone(child)
    finally:
        _reap_group(leader)


@pytest.mark.unit
def test_a_port_from_dotenv_is_used_when_the_environment_is_silent(
    tmp_path: Path,
) -> None:
    """`.env` is where an operator overrides a port, and `port_of` reads it only
    when the environment is silent. `.env.example` ships no ports, so this path is
    the override rather than the default."""
    port = _free_port()
    stranger = _spawn_listener(port)
    root = _script_copy(tmp_path, f"BACKEND_PORT={port}\n")
    environment = {
        key: value for key, value in os.environ.items() if key != "BACKEND_PORT"
    }
    try:
        result = _stop(root, environment)

        assert result.returncode == 0, result.stdout + result.stderr
        assert str(stranger.pid) in result.stdout
    finally:
        _reap(stranger)


@pytest.mark.unit
def test_start_refuses_when_the_data_services_are_not_up(tmp_path: Path) -> None:
    """The backend cannot serve without them, and the message is the only place the
    user is told which command starts them."""
    root = _script_copy(tmp_path)
    environment = _path_with_stubs(tmp_path, docker="echo some-other-container")
    environment["BACKEND_PORT"] = str(_free_port())

    result = _start(root, environment)

    assert result.returncode != 0
    assert "pdm run up" in result.stdout
    assert "http://localhost" not in result.stdout
    assert not (root / ".dev-logs/backend.pid").exists()


@pytest.mark.unit
def test_a_container_runtime_that_does_not_answer_is_not_read_as_services_up(
    tmp_path: Path,
) -> None:
    """A runtime that cannot answer must not read as "the services are there" — that
    launches the backend against a database that is not running."""
    root = _script_copy(tmp_path)
    environment = _path_with_stubs(
        tmp_path, docker='echo "Cannot connect to the Docker daemon" >&2; exit 1'
    )
    environment["BACKEND_PORT"] = str(_free_port())

    result = _start(root, environment)

    assert result.returncode != 0
    assert "did not answer" in result.stdout
    assert "http://localhost" not in result.stdout
    assert not (root / ".dev-logs/backend.pid").exists()


@pytest.mark.unit
def test_an_lsof_that_warns_while_finding_nothing_is_not_read_as_a_free_port(
    tmp_path: Path,
) -> None:
    """`-w` suppresses lsof's own warnings, so stderr beside exit 1 is a lookup that
    went wrong, not an empty answer."""
    root = _script_copy(tmp_path)
    environment = _path_with_stubs(
        tmp_path, lsof='echo "lsof: WARNING: cannot stat /Volumes/x" >&2; exit 1'
    )
    environment["BACKEND_PORT"] = str(_free_port())

    result = _start(root, environment)

    assert result.returncode != 0
    # The script's own words, not "lsof": `tmp_path` is named after the test, so that
    # substring arrives free in any message quoting a path underneath it.
    assert "the lsof lookup for :" in result.stdout
    assert "cannot stat" in result.stdout
    assert "http://localhost" not in result.stdout
    # The refusal has to come before the launch: `_launch` opens the log file before it
    # spawns, so an untouched log dir is what says nothing was started. The pidfile's
    # absence alone would not: a launch that failed afterwards leaves none either.
    assert not (root / ".dev-logs/backend.pid").exists()
    assert not (root / ".dev-logs/backend.log").exists()


@pytest.mark.unit
def test_restart_does_not_start_again_when_a_target_could_not_be_stopped(
    tmp_path: Path,
) -> None:
    """Restarting over a server that would not stop is how a port ends up with two
    of them. `pdm run restart-*` ships, so the refusal has to hold."""
    port = _free_port()
    leader, child = _spawn_leader_of(_LISTENER, port)
    root = _script_copy(tmp_path)
    # A recorded pid that leads no group: `stop` cannot reach it and says so.
    _write_pidfile(root, "backend", child, _start_time(child))
    environment = _path_with_stubs(tmp_path, docker="echo ontoprism-postgres")
    environment["BACKEND_PORT"] = str(port)
    try:
        result = _restart(root, environment)

        assert result.returncode != 0
        assert "not restarted" in result.stdout
        assert "http://localhost" not in result.stdout
    finally:
        _reap_group(leader)


@pytest.mark.unit
def test_the_frontend_is_started_and_stopped_through_the_script(
    tmp_path: Path,
) -> None:
    """The frontend has its own launch command and writes its own record, so the
    round trip has to be driven under that target too, not only the backend.
    (`node_modules` is created here so the `npm install` branch is skipped; that
    branch has no test.)"""
    port = _free_port()
    root = _script_copy(tmp_path)
    (root / "frontend/node_modules").mkdir(parents=True)
    server = tmp_path / "fake_server.py"
    server.write_text(f"LISTENER = {_LISTENER!r}\n{_FAKE_SERVER}")
    environment = _path_with_stubs(
        tmp_path, npm=f'exec "{sys.executable}" "{server}" "$@"'
    )
    environment["FRONTEND_PORT"] = str(port)
    leader = 0
    try:
        started = _start(root, environment, "frontend")

        assert started.returncode == 0, started.stdout + started.stderr
        assert _is_listening(port)
        leader = int((root / ".dev-logs/frontend.pid").read_text().split()[0])

        stopped = _stop(root, environment, "frontend")

        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        assert not _is_listening(port)
    finally:
        if leader and _is_running(leader):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(leader, signal.SIGKILL)


@pytest.mark.unit
@pytest.mark.parametrize("wait", ["abc", "0", "1e400", "-5"])
def test_a_readiness_wait_that_is_not_a_wait_is_refused(
    wait: str, tmp_path: Path
) -> None:
    """Every one of these is refused before anything is launched: a non-number,
    zero, a negative, and a value past the bound. A wrong readiness wait must not be
    discovered after the server is up and recorded."""
    root = _script_copy(tmp_path)
    environment = _path_with_stubs(tmp_path, docker="echo ontoprism-postgres")
    environment["BACKEND_PORT"] = str(_free_port())
    environment["ONTOPRISM_DEV_READY_SECONDS"] = wait

    result = _start(root, environment)

    assert result.returncode != 0
    assert "ONTOPRISM_DEV_READY_SECONDS" in result.stdout
    assert "http://localhost" not in result.stdout
    # Nothing was launched, so there is nothing to find later.
    assert not (root / ".dev-logs/backend.pid").exists()
    assert "was launched as pid" not in result.stdout


@pytest.mark.unit
def test_a_record_that_cannot_be_written_names_the_running_process(
    tmp_path: Path,
) -> None:
    """The server is already up by the time the record is written, so a write that
    fails must name its pid: without a record nothing else can reach it."""
    port = _free_port()
    root = _script_copy(tmp_path)
    environment = _stubs_that_launch_a_fake_server(tmp_path)
    environment["BACKEND_PORT"] = str(port)
    logs = root / ".dev-logs"
    logs.mkdir(exist_ok=True)
    # Readable but not writable, the shape a `sudo` run leaves behind.
    (logs / "backend.pid").write_text("999999 1.0\n")
    (logs / "backend.pid").chmod(0o444)
    # Bound before the try: a failing assertion below must not turn the cleanup into
    # an UnboundLocalError that masks it and leaks the group — which is the state this
    # test is in whenever it is run red.
    named: re.Match[str] | None = None
    try:
        result = _start(root, environment)

        assert result.returncode != 0
        assert "could not be written" in result.stdout
        # The pid below comes from `write_record`'s own message; the wrapper in
        # `start` is what appends the port, so this assertion is what pins the
        # wrapper firing on this path.
        assert f"check :{port}" in result.stdout
        assert "http://localhost" not in result.stdout
        named = re.search(r"is running as pid (\d+)", result.stdout)
        assert named is not None
        # Naming the pid is only worth anything if it is reachable, so check that
        # rather than leaving it to the cleanup, which suppresses exactly the error a
        # fabricated pid would raise.
        assert _is_running(int(named.group(1)))
    finally:
        (logs / "backend.pid").chmod(0o600)
        if named is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(int(named.group(1)), signal.SIGKILL)


@pytest.mark.unit
def test_a_record_that_cannot_be_removed_does_not_skip_the_other_target(
    tmp_path: Path,
) -> None:
    """An unremovable record has to fail as a `DevError`, so `_attempt` keeps it to
    its own target: `stop all` still attempts and names both, exits non-zero, and
    does not fall out as a traceback with nothing on stdout."""
    root = _script_copy(tmp_path)
    logs = root / ".dev-logs"
    logs.mkdir(exist_ok=True)
    for name in ("backend", "frontend"):
        (logs / f"{name}.pid").write_text("999999 1.0\n")
    # The shape a `sudo` run leaves behind: the records exist and cannot be removed.
    logs.chmod(0o555)
    try:
        result = _stop(
            root,
            {
                **os.environ,
                "BACKEND_PORT": str(_free_port()),
                "FRONTEND_PORT": str(_free_port()),
            },
            "all",
        )

        assert result.returncode != 0
        # Both targets attempted and named, and no traceback.
        assert "backend" in result.stdout
        assert "frontend" in result.stdout
        assert "Traceback" not in result.stderr
    finally:
        logs.chmod(0o755)


@pytest.mark.unit
def test_start_does_not_claim_a_port_a_stranger_answers(tmp_path: Path) -> None:
    """`port_answers` only opens a socket, so a stranger on the port satisfies it.
    The launch path refuses a port it does not own; the already-running path has to
    reach the same answer, or `start` hands out a green URL for another app — which
    is one `BACKEND_PORT=8001` away, the sibling app's port."""
    port = _free_port()
    stranger = _spawn_listener(port)
    ours = _spawn_listener(_free_port())
    root = _script_copy(tmp_path)
    _write_pidfile(root, "backend", ours.pid, _start_time(ours.pid))
    environment = _path_with_stubs(tmp_path, docker="echo ontoprism-postgres")
    environment["BACKEND_PORT"] = str(port)
    environment["ONTOPRISM_DEV_READY_SECONDS"] = "5"
    try:
        result = _start(root, environment)

        assert result.returncode != 0
        assert "http://localhost" not in result.stdout
        assert str(stranger.pid) in result.stdout
        # This branch is the only refusal that leaves a running server the user did
        # not have before, so it owes them the same handle the timeout branch gives.
        assert "backend.pid is kept" in result.stdout
        assert not _wait_until_gone(stranger.pid, timeout=1)
    finally:
        _reap(stranger, ours)


@pytest.mark.unit
def test_start_does_not_rewrite_a_record_it_would_not_change(tmp_path: Path) -> None:
    """An already-running server's record is already on disk and correct. Rewriting
    it is a no-op that can still fail — a read-only pidfile is the shape a `sudo` run
    leaves behind — and failing it would report a failure for a server that is up and
    serving."""
    port = _free_port()
    ours = _spawn_listener(port)
    root = _script_copy(tmp_path)
    _write_pidfile(root, "backend", ours.pid, _start_time(ours.pid))
    (root / ".dev-logs/backend.pid").chmod(0o444)
    environment = _path_with_stubs(tmp_path, docker="echo ontoprism-postgres")
    environment["BACKEND_PORT"] = str(port)
    try:
        result = _start(root, environment)

        assert result.returncode == 0, result.stdout + result.stderr
        assert f"http://localhost:{port}" in result.stdout
        assert "could not be written" not in result.stdout
    finally:
        (root / ".dev-logs/backend.pid").chmod(0o600)
        _reap(ours)


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
    """The record, not the port, says whether the server is ours: a second `start`
    must recognise it rather than launch another onto the same port."""
    port = _free_port()
    ours = _spawn_listener(port)
    root = _script_copy(tmp_path)
    _write_pidfile(root, target, ours.pid, _start_time(ours.pid))
    try:
        result = _start(root, {**os.environ, variable: str(port)}, target)

        assert result.returncode == 0, result.stdout + result.stderr
        assert f"already running (pid {ours.pid})" in result.stdout
        assert not _wait_until_gone(ours.pid, timeout=2)
    finally:
        _reap(ours)


@pytest.mark.unit
def test_start_does_not_call_a_recorded_group_that_binds_nothing_running(
    tmp_path: Path,
) -> None:
    """ "Already running" is not "already serving". A recorded group can be alive and
    bind nothing — `uvicorn --reload` restarting a worker that crashes on import is
    the case `_await_listening` names — and reporting success there tells the user a
    broken stack is up. The second `start` must reach the first one's verdict."""
    port = _free_port()
    leader, child = _spawn_leader_of(_LISTENER, _free_port())
    root = _script_copy(tmp_path)
    _write_pidfile(root, "backend", leader.pid, _start_time(leader.pid))
    environment = _path_with_stubs(tmp_path, docker="echo ontoprism-postgres")
    environment["BACKEND_PORT"] = str(port)
    environment["ONTOPRISM_DEV_READY_SECONDS"] = "1"
    try:
        result = _start(root, environment)

        assert result.returncode != 0
        assert "did not listen" in result.stdout
        assert "http://localhost" not in result.stdout
        assert not _wait_until_gone(child, timeout=1)
    finally:
        _reap_group(leader)


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
    """Whether one line picks a process to signal out of a port or name lookup.

    `kill -9 $(lsof -ti:8011)` puts the kill first, so matching `lsof … kill` in that
    order alone let the usual shell spelling through: the two only have to share a
    line. The Python spellings matter too now that `scripts/` is mostly Python —
    `os.kill(p, ...)` beside `lsof` or `listeners_on` selects a victim the same way.
    """
    if re.search(r"\bpkill\b|\bkillall\b|fuser\s+-k", line):
        return True
    signals = re.search(r"\bkill\b|os\.kill|killpg|\bterminate\(|SIGKILL|SIGTERM", line)
    selects_by_port = re.search(r"\blsof|listeners_on|net_connections", line)
    return bool(signals and selects_by_port)


@pytest.mark.unit
def test_no_script_under_scripts_selects_a_process_by_port_or_name() -> None:
    """The guard against `lsof -ti :PORT | xargs kill`, `pkill` and friends at an
    agent's prompt is a local hook on the owner's machine, and a hook sees nothing a
    script does. This covers `scripts/` only, where this project's process management
    lives."""
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
def test_a_missing_lookup_tool_refuses_instead_of_reporting_nothing_running(
    tmp_path: Path,
) -> None:
    """lsof is how the script sees who holds a port, and `start` is where a blind
    lookup does damage: an empty answer reading as "the port is free" launches a
    second server on top of whatever is already there."""
    port = _free_port()
    stranger = _spawn_listener(port)
    empty = tmp_path / "bin"
    empty.mkdir()
    try:
        result = _start(
            _script_copy(tmp_path), {"PATH": str(empty), "BACKEND_PORT": str(port)}
        )

        assert result.returncode != 0
        assert "lsof" in result.stdout + result.stderr
        assert "http://localhost" not in result.stdout
        assert not _wait_until_gone(stranger.pid, timeout=1)
    finally:
        _reap(stranger)


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
    assert variable in result.stdout
    assert "80l1" in result.stdout
    assert "not running" not in result.stdout


@pytest.mark.unit
def test_start_does_not_claim_a_port_whose_answer_has_gone(tmp_path: Path) -> None:
    """`listeners_on` returns nothing only when its own re-probe agrees the port is
    silent, so an empty answer means whatever replied a moment ago is gone -- and the
    one thing that cannot be true then is that our pid is serving the port. Falling
    through to the green URL here is the same lie as claiming a stranger's server,
    reached by the other route."""
    port = _free_port()
    fleeting = subprocess.Popen(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-c", _ONE_SHOT_LISTENER, str(port)],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    assert fleeting.stdout is not None
    assert fleeting.stdout.readline().strip() == "listening"
    ours = _spawn_listener(_free_port())
    root = _script_copy(tmp_path)
    _write_pidfile(root, "backend", ours.pid, _start_time(ours.pid))
    # The stub outlives the one connection `port_answers` makes, so by the time it
    # answers, the port really is empty -- "nothing matched", not a broken lookup.
    environment = _path_with_stubs(
        tmp_path, docker="echo ontoprism-postgres", lsof="sleep 0.3; exit 1"
    )
    environment["BACKEND_PORT"] = str(port)
    environment["ONTOPRISM_DEV_READY_SECONDS"] = "2"
    try:
        result = _start(root, environment)

        assert result.returncode != 0
        assert "http://localhost" not in result.stdout
        assert f"did not listen on :{port}" in result.stdout
    finally:
        _reap(fleeting, ours)
