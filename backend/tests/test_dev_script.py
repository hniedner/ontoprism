"""Behaviour of ``scripts/dev.sh``, the process manager behind ``pdm run start-*``,
``stop-*`` and ``restart-*``.

Every test runs a copy of the script in its own directory: the script sources the
``.env`` of the directory above it, and the repository's must never decide what a
test signals.
"""

from __future__ import annotations

import os
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
connection, _ = server.accept() if sys.argv[2] == "accept" else (None, None)
time.sleep(60)
"""

_CLIENT = """
import socket, sys, time
client = socket.create_connection(("127.0.0.1", int(sys.argv[1])))
print("connected", flush=True)
time.sleep(60)
"""


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _spawn(script: str, port: int, mode: str = "accept") -> subprocess.Popen[str]:
    process = subprocess.Popen(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-c", script, str(port), mode],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() in {"listening", "connected"}
    return process


def _script_copy(tmp_path: Path, dotenv: str = "") -> Path:
    (tmp_path / "scripts").mkdir()
    shutil.copy(REPO_ROOT / "scripts/dev.sh", tmp_path / "scripts/dev.sh")
    if dotenv:
        (tmp_path / ".env").write_text(dotenv)
    return tmp_path


def _stop_backend(
    root: Path, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", "scripts/dev.sh", "stop", "backend"],
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
def test_stopping_a_port_spares_its_clients(tmp_path: Path) -> None:
    """``lsof -i :PORT`` also lists every client of the port (a browser tab on the
    dev server, a proxy); only the listener is ours to stop."""
    port = _free_port()
    listener = _spawn(_LISTENER, port)
    client = _spawn(_CLIENT, port)
    try:
        result = _stop_backend(
            _script_copy(tmp_path), {**os.environ, "BACKEND_PORT": str(port)}
        )

        assert result.returncode == 0, result.stderr
        assert listener.wait(timeout=10) != 0
        time.sleep(0.5)
        assert client.poll() is None
    finally:
        _reap(listener, client)


@pytest.mark.unit
def test_a_port_given_in_the_environment_wins_over_dotenv(tmp_path: Path) -> None:
    """``.env`` used to overwrite the caller's port, so a command aimed at one port
    signalled whatever listened on the other."""
    asked = in_dotenv = _free_port()
    while in_dotenv == asked:
        in_dotenv = _free_port()
    target = _spawn(_LISTENER, asked, "idle")
    bystander = _spawn(_LISTENER, in_dotenv, "idle")
    try:
        result = _stop_backend(
            _script_copy(tmp_path, f"BACKEND_PORT={in_dotenv}\n"),
            {**os.environ, "BACKEND_PORT": str(asked)},
        )

        assert result.returncode == 0, result.stderr
        assert target.wait(timeout=10) != 0
        time.sleep(0.5)
        assert bystander.poll() is None
    finally:
        _reap(target, bystander)


@pytest.mark.unit
def test_without_lsof_the_script_refuses_instead_of_reporting_nothing_running(
    tmp_path: Path,
) -> None:
    """With lsof missing every lookup came back empty and ``stop`` printed "was not
    running" beside a live server."""
    empty_path = tmp_path / "bin"
    empty_path.mkdir()

    result = _stop_backend(_script_copy(tmp_path), {"PATH": str(empty_path)})

    assert result.returncode != 0
    assert "lsof" in result.stderr
    assert "not running" not in result.stdout


@pytest.mark.unit
def test_a_port_that_is_not_a_number_is_refused(tmp_path: Path) -> None:
    """lsof exits 1 for a malformed port exactly as it does for "no listener", so
    ``stop`` used to print "was not running" whatever was running."""
    result = _stop_backend(
        _script_copy(tmp_path), {**os.environ, "BACKEND_PORT": "80l1"}
    )

    assert result.returncode != 0
    assert "BACKEND_PORT" in result.stderr
    assert "80l1" in result.stderr
    assert "not running" not in result.stdout
