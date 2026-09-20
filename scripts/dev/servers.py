"""ontoprism dev process manager — start/stop/restart the backend + frontend.

Invoked via ``pdm run start-all|stop-all|restart-all|start-backend|...``.

``start`` launches each server in its own process group and records that process's
pid and start time in ``.dev-logs/<target>.pid``. ``stop`` signals that group, and
only while the recorded start time still names the process ``start`` launched, so a
pid reused after ours exited is never signalled. Choosing the victim from a port
lookup instead is what took the Podman VM's gvproxy down on 2026-09-18
(``docs/DATA_SETUP.md``); ``AGENTS.md`` carries the rule.

This replaced a shell version. Shell has no process API, so every question about a
process had to be inferred from another tool's output and exit code, where "nothing
there" and "the lookup broke" arrive as the same empty string; six distinct bugs in
that inference were found over three review rounds of this PR. Here a dead pid raises
``NoSuchProcess``, a start time is a float and a zombie is a status constant, so those
questions have answers rather than conventions. ``lsof`` survives for one
job -- naming who else holds a port, which ``psutil`` cannot do unprivileged on
macOS -- and it is never used to choose what to signal.
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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import psutil

REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = REPO_ROOT / ".dev-logs"
# 8001/5173 are the sibling fairdata app's ports; ours are offset so both can run.
DEFAULT_PORTS = {"backend": 8011, "frontend": 5175}
HIGHEST_PORT = 65535
LONGEST_READY_WAIT = 3600.0
GREEN, YELLOW, RED, RESET = "\033[0;32m", "\033[1;33m", "\033[0;31m", "\033[0m"


class DevError(RuntimeError):
    """A question about the machine that could not be answered.

    The lookup that raises signals nothing and deletes nothing: an unanswered
    question is never the same as "nothing there". A command can still end in one
    after doing its work -- a stop that succeeded and then could not check the port
    -- so this is a promise about the lookup, not about the command.
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
        if self.name == "frontend":
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
        raise DevError(f"no launch command for target {self.name!r}")


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
        if name not in DEFAULT_PORTS:
            raise DevError(f"unknown target {name!r}")
        return DEFAULT_PORTS[name]
    # lsof answers an out-of-range port exactly as it answers a free one (exit 1, both
    # streams empty), so a port that is silently wrong would read as "nothing there".
    if not raw.isdigit() or not 1 <= int(raw) <= HIGHEST_PORT:
        raise DevError(
            f"{variable}={raw!r} is not a port between 1 and {HIGHEST_PORT} "
            f"(environment or .env)"
        )
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


class Record(NamedTuple):
    """The pid ``start`` launched and the start time that pins it to that launch.

    The two only mean anything together, so they travel together.
    """

    pid: int
    created: float


def read_record(target: Target) -> Record | None:
    """The record, or None only when no pidfile exists.

    A record that cannot be read *or parsed* is not a verdict: both raise rather
    than passing for "not ours" and having a live server disowned. ``write_record``
    truncates before it writes, so a half-written record is a state that happens.
    """
    if not target.pid_file.exists():
        return None
    try:
        raw = target.pid_file.read_text()
    except OSError as error:
        raise DevError(f"cannot read {target.pid_file}: {error}") from error
    pid, _, created = raw.partition(" ")
    try:
        number, started = int(pid), float(created)
    except ValueError:
        raise DevError(
            f"{target.pid_file} does not hold '<pid> <start time>': {raw!r}"
        ) from None
    if number <= 1:
        # `os.killpg` with 0 addresses this script's own process group. With 1 it
        # addresses pgid 1, which on Linux is `kill(-1)` -- the broadcast to every
        # process the user may signal -- and on macOS is launchd's group.
        raise DevError(
            f"{target.pid_file} records pid {number}, which would signal a group that "
            f"is not ours; nothing signalled"
        )
    return Record(number, started)


def write_record(target: Target, record: Record) -> None:
    # `repr` of the float, because `read_record` compares it with `==`: formatting it
    # to fewer digits would silently break every identity check in this module.
    try:
        target.pid_file.write_text(f"{record.pid} {record.created}\n")
    except OSError as error:
        # The process is already running here, so its pid is the whole value of the
        # message: without a record nothing else can reach it.
        raise DevError(
            f"{target.name} is running as pid {record.pid} but its record could not "
            f"be written to {target.pid_file}: {error}"
        ) from error


def forget_record(target: Target) -> None:
    """Drop a record whose process is known to be gone.

    Outside ``DevError`` this would escape ``_attempt`` and take the other target
    down with it, which is the failure ``_attempt`` exists to prevent.
    """
    try:
        target.pid_file.unlink(missing_ok=True)
    except OSError as error:
        raise DevError(f"cannot remove {target.pid_file}: {error}") from error


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
        try:
            # A process that has exited but not been reaped is still in its group on
            # Linux, and `getpgid` still answers for it. It holds nothing and receives
            # nothing, so it must not keep the group alive.
            if process.info["status"] == psutil.STATUS_ZOMBIE:
                continue
            if os.getpgid(process.info["pid"]) == pgid:
                members.append(process.info["pid"])
        except ProcessLookupError:
            # "It exited" is an answer, and the only one that may be skipped. This arm
            # must stay above the OSError arm it is a subclass of: that ordering is
            # what keeps an unanswerable lookup from being silently skipped.
            continue
        except OSError as error:
            # Anything else would quietly shorten the list, and a short list reads as
            # "the group is gone" -- which deletes the record of a live server.
            raise DevError(
                f"cannot tell whether pid {process.pid} is in group {pgid}: {error}"
            ) from error
    return sorted(members)


def target_alive(record: Record) -> bool:
    """Whether the thing ``start`` launched is still there.

    A pid is not reused while it is still a process group id, so a group with this
    id is still the one ``start`` created. The second test catches a recorded pid
    that never led a group, where a group signal would reach nothing at all.
    """
    if group_members(record.pid):
        return True
    return start_time_of(record.pid) == record.created


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
    this lookup; it names who is in the way and decides whether ``start`` refuses.
    """
    result = _run(["lsof", "-w", "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"])
    # lsof exits 1 with nothing on stderr when nothing matched; that is the only
    # failure meaning "nothing there".
    if result.returncode > 1 or (result.returncode == 1 and result.stderr.strip()):
        raise DevError(
            f"the lsof lookup for :{port} failed "
            f"(exit {result.returncode}): {result.stderr.strip()}"
        )
    pids = [int(line) for line in result.stdout.split()]
    if not pids and port_answers(port):
        # Unprivileged lsof reports another user's socket exactly as it reports a free
        # port: exit 1, both streams empty. Something answering on a port lsof calls
        # empty is that case, and "free" is the one conclusion we must not draw.
        raise DevError(
            f"something is listening on :{port} but lsof cannot see it, most likely "
            f"another user's process. Try: sudo lsof -nP -iTCP:{port} -sTCP:LISTEN"
        )
    return pids


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


def await_group_exit(record: Record, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not target_alive(record):
            return True
        time.sleep(0.1)
    return not target_alive(record)


def _orphaned_group(record: Record) -> list[int]:
    """The live members of a record's group when the process it names is gone.

    Empty when that pid is in use by something else: a different start time means a
    stranger inherited the number, not that our group survived.
    """
    if start_time_of(record.pid) is not None:
        return []
    return group_members(record.pid)


def stop(target: Target) -> int:
    """Signal only the group ``start`` recorded. Returns a process exit status."""
    record = read_record(target)
    if record is not None and start_time_of(record.pid) == record.created:
        pid = record.pid
        # The group, not the pid: the process that serves is a descendant of the
        # launcher, so the group is the handle that reaches it whether or not the
        # launcher forwards the signal and whether or not it outlives it.
        _signal_group(pid, signal.SIGTERM)
        if not await_group_exit(record, seconds=5):
            _signal_group(pid, signal.SIGKILL)
            await_group_exit(record, seconds=2)
        if target_alive(record):
            red(
                f"✗ {target.name} did not stop: pid {pid} is still there. "
                f"{target.pid_file} is kept, so the next stop still knows it is ours."
            )
            return 1
        forget_record(target)
        green(f"✓ {target.name} stopped (pid {pid})")
    elif record is not None:
        survivors = _orphaned_group(record)
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
        forget_record(target)
        green(f"✓ {target.name} not running (stale pidfile)")
    else:
        green(f"✓ {target.name} was not running")
    _report_port_holders(target.port)
    return 0


def _signal_group(pid: int, number: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, number)


def _joined(pids: list[int]) -> str:
    return " ".join(str(pid) for pid in pids)


def _report_port_holders(port: int) -> None:
    """Advisory. Who else holds the port must never decide whether the command
    worked, so a lookup that cannot answer is reported and the verdict stands."""
    try:
        holders = listeners_on(port)
    except DevError as error:
        yellow(f"⚠ could not check who holds :{port}: {error}")
        return
    if holders:
        yellow(
            f"⚠ :{port} is held by pid(s) {_joined(holders)}, which this "
            f"script did not start — not signalled"
        )


def start(target: Target) -> int:
    # Resolved here, before anything is launched: it is input validation, and a typo
    # must refuse rather than abort a server that is already running. Binding it
    # inside `_await_listening` instead would raise before the record is written and
    # leave exactly the orphan this module refuses to create.
    seconds = ready_seconds()
    record = read_record(target)
    if record is not None:
        if start_time_of(record.pid) == record.created:
            yellow(
                f"⚠ {target.name} already running (pid {record.pid}) on :{target.port}"
            )
            return 0
        survivors = _orphaned_group(record)
        if survivors:
            # `stop` refuses to touch this state; overwriting the record here would
            # leave those processes with nothing that knows they are ours.
            red(
                f"✗ {target.name} not started: the process in {target.pid_file} is "
                f"gone but its group still runs pid(s) {_joined(survivors)}. "
                f"{target.pid_file} is kept rather than overwritten."
            )
            return 1
    holders = listeners_on(target.port)
    if holders:
        red(
            f"✗ {target.name} not started: :{target.port} is held by pid(s) "
            f"{_joined(holders)}, which this script did not start"
        )
        return 1
    if target.name == "backend" and not _data_services_are_up():
        return 1
    if target.name == "frontend" and not (REPO_ROOT / "frontend/node_modules").is_dir():
        installed = _run(["npm", "install", "--silent"], cwd=REPO_ROOT / "frontend")
        if installed.returncode != 0:
            raise DevError(f"npm install failed: {installed.stderr.strip()}")
    LOG_DIR.mkdir(exist_ok=True)
    try:
        process = _launch(target)
    except OSError as error:
        raise DevError(f"cannot launch {target.name}: {error}") from error
    try:
        return _await_listening(target, process.pid, seconds)
    except DevError as error:
        # Whatever went wrong, a process is running and the user has to be able to
        # find it; `start` must not report a clean "did not start" it cannot keep.
        raise DevError(
            f"{error}; {target.name} was launched as pid {process.pid} and may still "
            f"be running — check :{target.port}"
        ) from error


def _launch(target: Target) -> subprocess.Popen[bytes]:
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
    return process


def ready_seconds() -> float:
    """How long ``start`` waits for the port, overridable so the timeout branch is
    testable without a half-minute test. Validated like every other input: a wrong
    value must not read as an answer."""
    raw = os.environ.get("ONTOPRISM_DEV_READY_SECONDS")
    if raw is None:
        return 30.0
    try:
        seconds = float(raw)
    except ValueError:
        raise DevError(f"ONTOPRISM_DEV_READY_SECONDS={raw!r} is not a number") from None
    if not 0 < seconds < LONGEST_READY_WAIT:
        raise DevError(
            f"ONTOPRISM_DEV_READY_SECONDS={raw!r} must be greater than 0 and less "
            f"than {LONGEST_READY_WAIT:g} seconds"
        )
    return seconds


def _await_listening(target: Target, pid: int, seconds: float) -> int:
    """``start`` is only honest once the port answers.

    A server that fails just after launch — a bad config, a port taken inside a
    container — would otherwise be reported as a working URL.
    """
    created = start_time_of(pid)
    if created is None:
        red(f"✗ {target.name} exited immediately — see {target.log_file}")
        return 1
    record = Record(pid, created)
    write_record(target, record)
    # The early exit covers a launcher whose whole group dies. It does not cover a
    # group that outlives its failed child -- `uvicorn --reload` restarting a worker
    # that crashes on import keeps the group alive -- so this bound is what ends that
    # case, and it is generous for that reason rather than for cold-start time
    # (measured: vite answers in well under a second on this app). `ready_seconds`
    # says why it is overridable.
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not target_alive(record):
            forget_record(target)
            red(f"✗ {target.name} exited while starting — see {target.log_file}")
            return 1
        if port_answers(target.port):
            green(
                f"✓ {target.name} → http://localhost:{target.port} "
                f"(pid {pid}, logs: {target.log_file})"
            )
            return 0
        time.sleep(0.1)
    # An unanswered "is it serving?" is not a success. The record is kept so `stop`
    # can still reach the process, and the status says the question went unanswered.
    red(
        f"✗ {target.name} (pid {pid}) did not listen on :{target.port} within "
        f"{seconds:g}s — "
        f"see {target.log_file}. {target.pid_file} is kept, so `stop` can reach it."
    )
    return 1


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
    is the reason that gate is about to fail, so it has to be visible. A missing
    container runtime is not fatal either -- the frontend does not need one."""
    LOG_DIR.mkdir(exist_ok=True)
    compose_log = LOG_DIR / "compose.log"
    try:
        result = _run(["docker", "compose", "up", "-d"], cwd=REPO_ROOT)
    except DevError as error:
        yellow(f"⚠ could not start the data services: {error}")
        return
    compose_log.write_text(result.stdout + result.stderr)
    if result.returncode != 0:
        yellow(f"⚠ docker compose did not start the data services — see {compose_log}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dev/servers.py", description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "restart"])
    parser.add_argument(
        # Derived from one list, so the selectable targets and the default ports
        # cannot drift apart. `Target.command`'s refusal stays a guard for a target
        # added without a launch command rather than a branch reachable today.
        "target",
        nargs="?",
        default="all",
        choices=[*DEFAULT_PORTS, "all"],
    )
    arguments = parser.parse_args(argv)
    names = list(DEFAULT_PORTS) if arguments.target == "all" else [arguments.target]

    try:
        LOG_DIR.mkdir(exist_ok=True)
        targets = [Target(name, port_of(name)) for name in names]
    except DevError as error:
        red(f"✗ dev: {error}")
        return 1

    if arguments.action == "start":
        if arguments.target == "all":
            start_data_services()
        return max([_attempt(start, target) for target in targets])
    # Stop the frontend first, and attempt every target even when one fails.
    stopped = [_attempt(stop, target) for target in reversed(targets)]
    if arguments.action == "stop":
        return max(stopped)
    if any(stopped):
        red("✗ not restarted: a target could not be stopped")
        return 1
    if arguments.target == "all":
        start_data_services()
    return max([_attempt(start, target) for target in targets])


def _attempt(action: Callable[[Target], int], target: Target) -> int:
    """Run one target's action, keeping its failure to itself.

    An unanswered question about one target must not skip the others: `stop all`
    that gives up after the frontend leaves the backend running and unmentioned.
    """
    try:
        return action(target)
    except DevError as error:
        red(f"✗ dev: {target.name}: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
