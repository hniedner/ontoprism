"""ontoprism dev process manager — start/stop/restart the backend + frontend.

Invoked via ``pdm run start-all|stop-all|restart-all|start-backend|...``.

``start`` launches each server in its own process group and records that process's
pid and start time in ``.dev-logs/<target>.pid``. ``stop`` signals that group, and
only while the recorded start time still names the process ``start`` launched, so a
pid reused after ours exited is never signalled. Choosing the victim from a port
lookup instead is what took the Podman VM's gvproxy down on 2026-09-18
(``docs/DATA_SETUP.md``); ``AGENTS.md`` carries the rule.

This replaced a shell version. The shell spent two thirds of its lines inferring
facts about processes by parsing ``ps`` output and interpreting exit codes, where
"nothing there" and "the lookup broke" are the same empty string; three rounds of
review found four separate bugs in that inference. Here a dead pid raises
``NoSuchProcess`` and a start time is a float, so those questions have answers
instead of conventions. ``lsof`` survives for one job — naming who else holds a
port, which ``psutil`` cannot do unprivileged on macOS — and it is never used to
choose what to signal.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = REPO_ROOT / ".dev-logs"
# 8001/5173 are the sibling fairdata app's ports; ours are offset so both can run.
DEFAULT_PORTS = {"backend": 8011, "frontend": 5175}
GREEN, YELLOW, RED, RESET = "\033[0;32m", "\033[1;33m", "\033[0;31m", "\033[0m"


class DevError(RuntimeError):
    """A question about the machine that could not be answered.

    Nothing is signalled and no record is deleted on the way out: an unanswered
    question is never the same as "nothing there".
    """


def green(message: str) -> None:
    print(f"{GREEN}{message}{RESET}")


def yellow(message: str) -> None:
    print(f"{YELLOW}{message}{RESET}")


def red(message: str) -> None:
    print(f"{RED}{message}{RESET}")


@dataclass(frozen=True)
class Target:
    """One dev server: how to launch it, where it listens, where its record lives."""

    name: str
    port: int

    @property
    def pid_file(self) -> Path:
        return LOG_DIR / f"{self.name}.pid"

    @property
    def log_file(self) -> Path:
        return LOG_DIR / f"{self.name}.log"

    @property
    def command(self) -> list[str]:
        if self.name == "backend":
            return [
                "pdm",
                "run",
                "uvicorn",
                "backend.main:app",
                "--reload",
                "--port",
                str(self.port),
            ]
        return [
            "npm",
            "--prefix",
            "frontend",
            "run",
            "dev",
            "--",
            "--port",
            str(self.port),
            "--strictPort",
        ]


def port_of(name: str, environment: dict[str, str] | None = None) -> int:
    """The port for ``name``; a caller's environment wins over ``.env``.

    A command aimed at one port must never act on whatever holds another.
    """
    source = os.environ if environment is None else environment
    variable = f"{name.upper()}_PORT"
    raw = source.get(variable)
    if raw is None:
        raw = _dotenv_value(variable)
    if raw is None:
        return DEFAULT_PORTS[name]
    if not raw.isdigit():
        raise DevError(f"{variable}={raw!r} is not a number (environment or .env)")
    return int(raw)


def _dotenv_value(variable: str) -> str | None:
    dotenv = REPO_ROOT / ".env"
    if not dotenv.is_file():
        return None
    for line in dotenv.read_text().splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == variable:
            return value.strip()
    return None


def read_record(target: Target) -> tuple[int, float] | None:
    """The recorded (pid, start time), or None when nothing usable is recorded.

    A record that cannot be *read* is not a verdict, so an unreadable file raises
    rather than passing for "not ours" and having its live server disowned.
    """
    if not target.pid_file.exists():
        return None
    try:
        raw = target.pid_file.read_text()
    except OSError as error:
        raise DevError(f"cannot read {target.pid_file}: {error}") from error
    pid, separator, created = raw.partition(" ")
    if not separator:
        return None
    try:
        number, started = int(pid), float(created)
    except ValueError:
        return None
    # os.killpg(0, ...) would signal dev.py's own process group and 1 is init, so the
    # field is bounded here, before it can reach a signal.
    return (number, started) if number > 1 else None


def write_record(target: Target, pid: int, created: float) -> None:
    target.pid_file.write_text(f"{pid} {created}\n")


def start_time_of(pid: int) -> float | None:
    """When ``pid`` started, or None when no live process has that pid.

    A process that has exited but has not been reaped yet holds no port and
    receives no signal, so it counts as gone.
    """
    try:
        process = psutil.Process(pid)
        if process.status() == psutil.STATUS_ZOMBIE:
            return None
        return process.create_time()
    except psutil.NoSuchProcess:
        return None
    except psutil.Error as error:  # AccessDenied and friends: we cannot tell
        raise DevError(f"cannot inspect pid {pid}: {error}") from error


def group_members(pgid: int) -> list[int]:
    """The pids in process group ``pgid`` that have not exited."""
    members = []
    for process in psutil.process_iter(["pid", "status"]):
        with contextlib.suppress(psutil.Error, OSError):
            if process.info["status"] == psutil.STATUS_ZOMBIE:
                continue
            if os.getpgid(process.info["pid"]) == pgid:
                members.append(process.info["pid"])
    return sorted(members)


def target_alive(pid: int, created: float) -> bool:
    """Whether the thing ``start`` launched is still there.

    A pid is not reused while it is still a process group id, so a group with this
    id is still the one ``start`` created. The second test catches a recorded pid
    that never led a group, where a group signal would reach nothing at all.
    """
    if group_members(pid):
        return True
    return start_time_of(pid) == created


def _run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    """Run a helper binary, turning its absence into an answerable error.

    A tool that is not installed is a question we cannot answer, not an answer.
    """
    try:
        return subprocess.run(  # noqa: S603 - fixed argv from the caller, no shell
            argv, capture_output=True, text=True, check=False, **kwargs
        )  # pyright: ignore[reportArgumentType]
    except OSError as error:
        raise DevError(f"cannot run {argv[0]}: {error}") from error


def listeners_on(port: int) -> list[int]:
    """The pids listening on ``port``.

    Listeners only: a bare ``lsof -i :PORT`` also lists every client of the port (a
    browser tab on the dev server, a proxy). Nothing is ever signalled because of
    this lookup; it only tells the user who is in the way.
    """
    result = _run(["lsof", "-w", "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"])
    # lsof exits 1 with nothing on stderr when nothing matched; that is the only
    # failure meaning "nothing there".
    if result.returncode > 1 or (result.returncode == 1 and result.stderr.strip()):
        raise DevError(
            f"the lsof lookup for :{port} failed "
            f"(exit {result.returncode}): {result.stderr.strip()}"
        )
    return [int(line) for line in result.stdout.split()]


def port_answers(port: int) -> bool:
    """Whether anything accepts a connection on ``port``, on either loopback family.

    Vite binds ``localhost``, which resolves to ``::1`` here, so an IPv4-only probe
    reports a healthy dev server as never coming up.
    """
    for address in ("127.0.0.1", "::1"):
        with (
            contextlib.suppress(OSError),
            socket.create_connection((address, port), timeout=0.5),
        ):
            return True
    return False


def await_group_exit(pid: int, created: float, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not target_alive(pid, created):
            return True
        time.sleep(0.1)
    return not target_alive(pid, created)


def stop(target: Target) -> int:
    """Signal only the group ``start`` recorded. Returns a process exit status."""
    record = read_record(target)
    if record is not None and start_time_of(record[0]) == record[1]:
        pid, created = record
        # The group, not the pid: the process that serves is a descendant of the
        # launcher, so the group is the handle that reaches it whether or not the
        # launcher forwards the signal and whether or not it outlives it.
        _signal_group(pid, signal.SIGTERM)
        if not await_group_exit(pid, created, seconds=5):
            _signal_group(pid, signal.SIGKILL)
            await_group_exit(pid, created, seconds=2)
        if target_alive(pid, created):
            red(
                f"✗ {target.name} did not stop: pid {pid} is still there. "
                f"{target.pid_file} is kept, so the next stop still knows it is ours."
            )
            return 1
        target.pid_file.unlink()
        green(f"✓ {target.name} stopped (pid {pid})")
    elif record is not None:
        survivors = group_members(record[0]) if start_time_of(record[0]) is None else []
        if survivors:
            # The launcher is gone and the server it started is not. Nothing is
            # signalled: once the group empties the pid can be reused, and a reused
            # pid leading a group of its own would look the same from here. A pid
            # that *is* in use with a different start time is a stranger, and falls
            # through to the stale-record branch below.
            red(
                f"✗ {target.name}: the process in {target.pid_file} is gone but its "
                f"group still runs pid(s) {_joined(survivors)}. "
                f"Nothing was signalled and {target.pid_file} is kept."
            )
            return 1
        target.pid_file.unlink()
        green(f"✓ {target.name} not running (stale pidfile)")
    else:
        target.pid_file.unlink(missing_ok=True)
        green(f"✓ {target.name} was not running")
    _report_port_holders(target.port)
    return 0


def _signal_group(pid: int, number: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, number)


def _joined(pids: list[int]) -> str:
    return " ".join(str(pid) for pid in pids)


def _report_port_holders(port: int) -> None:
    holders = listeners_on(port)
    if holders:
        yellow(
            f"⚠ :{port} is held by pid(s) {_joined(holders)}, which dev.py has no "
            f"record of starting — not signalled"
        )


def start(target: Target) -> int:
    record = read_record(target)
    if record is not None and start_time_of(record[0]) == record[1]:
        yellow(f"⚠ {target.name} already running (pid {record[0]}) on :{target.port}")
        return 0
    holders = listeners_on(target.port)
    if holders:
        red(
            f"✗ {target.name} not started: :{target.port} is held by pid(s) "
            f"{_joined(holders)}, which dev.py has no record of starting"
        )
        return 1
    if target.name == "backend" and not _data_services_are_up():
        return 1
    if target.name == "frontend" and not (REPO_ROOT / "frontend/node_modules").is_dir():
        installed = _run(["npm", "install", "--silent"], cwd=REPO_ROOT / "frontend")
        if installed.returncode != 0:
            raise DevError(f"npm install failed: {installed.stderr.strip()}")
    LOG_DIR.mkdir(exist_ok=True)
    with target.log_file.open("wb") as log:
        # Its own session, so the group id equals this pid and `stop` can reach
        # every process the launcher spawns.
        process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            target.command,
            cwd=REPO_ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return _await_listening(target, process.pid)


def _await_listening(target: Target, pid: int) -> int:
    """``start`` is only honest once the port answers.

    A server that fails just after launch — a bad config, a port taken inside a
    container — would otherwise be reported as a working URL.
    """
    created = start_time_of(pid)
    if created is None:
        red(f"✗ {target.name} exited immediately — see {target.log_file}")
        return 1
    write_record(target, pid, created)
    # Generous, because it costs nothing for a server that is failing: the loop
    # exits as soon as the process dies. Vite's cold start alone can pass ten
    # seconds on this app.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if not target_alive(pid, created):
            target.pid_file.unlink(missing_ok=True)
            red(f"✗ {target.name} exited while starting — see {target.log_file}")
            return 1
        if port_answers(target.port):
            green(
                f"✓ {target.name} → http://localhost:{target.port} "
                f"(pid {pid}, logs: {target.log_file})"
            )
            return 0
        time.sleep(0.1)
    # `uvicorn --reload` can legitimately be slow, so the record is kept and the
    # wait is reported rather than called a failure.
    yellow(
        f"⚠ {target.name} (pid {pid}) is running but :{target.port} is not "
        f"listening yet — see {target.log_file}"
    )
    return 0


def _data_services_are_up() -> bool:
    result = _run(["docker", "ps", "--format", "{{.Names}}"])
    if result.returncode != 0:
        raise DevError(f"the container runtime did not answer: {result.stderr.strip()}")
    if "ontoprism-postgres" in result.stdout:
        return True
    red("✗ data services are not running — start them with: pdm run up")
    return False


def start_data_services() -> None:
    """Not fatal: ``_data_services_are_up`` is the real gate, but a compose failure
    is the reason that gate is about to fail, so it has to be visible."""
    LOG_DIR.mkdir(exist_ok=True)
    compose_log = LOG_DIR / "compose.log"
    result = _run(["docker", "compose", "up", "-d"], cwd=REPO_ROOT)
    compose_log.write_text(result.stdout + result.stderr)
    if result.returncode != 0:
        yellow(f"⚠ docker compose did not start the data services — see {compose_log}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dev.py", description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "restart"])
    parser.add_argument(
        "target", nargs="?", default="all", choices=["backend", "frontend", "all"]
    )
    arguments = parser.parse_args(argv)
    names = ["backend", "frontend"] if arguments.target == "all" else [arguments.target]

    try:
        LOG_DIR.mkdir(exist_ok=True)
        targets = [Target(name, port_of(name)) for name in names]
        if arguments.action == "start":
            if arguments.target == "all":
                start_data_services()
            return max(start(target) for target in targets)
        # Stop the frontend first, and attempt every target even when one fails.
        stopped = [stop(target) for target in reversed(targets)]
        if arguments.action == "stop":
            return max(stopped)
        if any(stopped):
            red("✗ not restarted: a target could not be stopped")
            return 1
        if arguments.target == "all":
            start_data_services()
        return max(start(target) for target in targets)
    except DevError as error:
        red(f"✗ dev.py: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
