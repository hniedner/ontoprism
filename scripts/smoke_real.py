"""Run a read-only browser smoke against the configured local repositories."""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
from http import HTTPStatus
from pathlib import Path
from urllib.request import urlopen

from fastapi import Request
from fastapi.responses import JSONResponse

from backend.main import create_app

ROOT = Path(__file__).resolve().parents[1]
app = create_app()


@app.middleware("http")
async def read_only(request: Request, call_next):  # type: ignore[no-untyped-def]
    """Reject unsafe verbs before any endpoint can reach a configured store."""
    remote_searches = {
        "/api/v1/pubmed/search",
        "/api/v1/clinicaltrials/search",
    }
    if request.method not in {"GET", "HEAD"} and not (
        request.method == "POST" and request.url.path in remote_searches
    ):
        return JSONResponse({"detail": "smoke backend is read-only"}, status_code=405)
    return await call_next(request)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for(url: str, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server exited before readiness: {url}")
        try:
            with urlopen(url, timeout=1) as response:  # noqa: S310 - loopback only
                if response.status == HTTPStatus.OK:
                    return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f"server did not become ready: {url}")


def start(
    command: list[str], *, cwd: Path, env: dict[str, str]
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 - fixed executable and arguments
        command,
        cwd=cwd,
        env=env,
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def stop(process: subprocess.Popen[bytes]) -> str:
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        output, _ = process.communicate(timeout=15)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        output, _ = process.communicate()
    return output.decode(errors="replace")


def main(
    connection_scope: Callable[[str], AbstractContextManager[None]] | None = None,
) -> int:
    backend_port, frontend_port = free_port(), free_port()
    env = os.environ.copy()
    # Frontend SSR is directed at this guarded app.
    env["ONTOPRISM_FASTAPI_ORIGIN"] = f"http://127.0.0.1:{backend_port}"
    env["HOST"] = "127.0.0.1"
    env["PORT"] = str(frontend_port)
    env["ORIGIN"] = f"http://127.0.0.1:{frontend_port}"
    npm, node = shutil.which("npm"), shutil.which("node")
    if npm is None or node is None:
        raise RuntimeError("npm and node are required for the browser smoke")
    backend = start(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "scripts.smoke_real:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(backend_port),
        ],
        cwd=ROOT,
        env=env,
    )
    frontend: subprocess.Popen[bytes] | None = None
    try:
        with ExitStack() as stack:
            if connection_scope is not None:
                stack.enter_context(
                    connection_scope(f"http://127.0.0.1:{backend_port}")
                )
                stack.enter_context(
                    connection_scope(f"http://127.0.0.1:{frontend_port}")
                )
            wait_for(f"http://127.0.0.1:{backend_port}/health", backend)
            subprocess.run(  # noqa: S603 - resolved executable
                [npm, "run", "build"], cwd=ROOT / "frontend", check=True, env=env
            )
            frontend = start([node, "build"], cwd=ROOT / "frontend", env=env)
            wait_for(f"http://127.0.0.1:{frontend_port}/", frontend)
            result = subprocess.run(  # noqa: S603
                [node, "scripts/smoke-real.mjs", f"http://127.0.0.1:{frontend_port}"],
                cwd=ROOT / "frontend",
                env=env,
                check=False,
            )
            return result.returncode
    finally:
        if frontend is not None:
            print("frontend server output:", stop(frontend))
        output = stop(backend)
        print(
            "backend startup warning present:",
            "Background certification produced no reusable result" in output,
        )
        print("backend server output:", output)


if __name__ == "__main__":
    raise SystemExit(main())
