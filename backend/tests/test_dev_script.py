"""Behaviour of ``scripts/dev.sh``, the process manager behind ``pdm run stop-*``."""

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
connection, _ = server.accept()
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


def _spawn(script: str, port: int) -> subprocess.Popen[str]:
    process = subprocess.Popen(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-c", script, str(port)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() in {"listening", "connected"}
    return process


@pytest.mark.unit
@pytest.mark.skipif(
    shutil.which("lsof") is None, reason="dev.sh finds processes with lsof"
)
def test_stopping_a_port_spares_its_clients() -> None:
    """``lsof -i :PORT`` also lists every client of the port (a browser tab on the
    dev server, a proxy); only the listener is ours to stop."""
    port = _free_port()
    listener = _spawn(_LISTENER, port)
    client = _spawn(_CLIENT, port)
    try:
        subprocess.run(
            ["/bin/bash", "scripts/dev.sh", "stop", "backend"],
            cwd=REPO_ROOT,
            env={**os.environ, "BACKEND_PORT": str(port)},
            check=True,
            capture_output=True,
            timeout=30,
        )

        assert listener.wait(timeout=10) != 0
        time.sleep(0.5)
        assert client.poll() is None
    finally:
        for process in (listener, client):
            process.kill()
            process.wait()
