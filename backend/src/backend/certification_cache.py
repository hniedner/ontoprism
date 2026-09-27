"""Worker-local, non-sliding certification reuse and shared live validation."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

CERTIFICATION_HEALTHY_SECONDS = 300.0
CERTIFICATION_REFRESH_LEAD_SECONDS = 30.0
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Entry[T]:
    inputs: object
    started: float
    result: T


@dataclass(frozen=True)
class _Flight[T]:
    task: asyncio.Task[T]
    inputs: object
    forced: bool
    background: bool = False

    def reusable(self, inputs: object, *, force: bool) -> bool:
        return self.inputs == inputs and (not force or self.forced)


class CertificationCache[T]:
    def __init__(
        self,
        *,
        clock: Callable[[], float],
        inputs: Callable[[], object],
        validate: Callable[[], Awaitable[T]],
        healthy: Callable[[T], bool],
        changed: Callable[[], T],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._clock = clock
        self._inputs = inputs
        self._validate = validate
        self._healthy = healthy
        self._changed = changed
        self._entry: _Entry[T] | None = None
        self._flight: _Flight[T] | None = None
        self._tasks: set[asyncio.Task[T]] = set()
        self._closed = False
        self._sleep = sleep
        self._maintenance: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Warm once and maintain this repository without delaying application boot."""
        if self._closed:
            raise RuntimeError("certification service is closed")
        if self._maintenance is None:
            self._maintenance = asyncio.create_task(self._maintain())

    async def _maintain(self) -> None:
        while not self._closed:
            try:
                await self._refresh()
            except Exception:
                # Background failure is logged, not cached; requests still validate.
                _logger.exception("Background repository certification failed")
            delay = CERTIFICATION_REFRESH_LEAD_SECONDS
            if self._entry is not None:
                delay = max(
                    0.0,
                    self._entry.started
                    + CERTIFICATION_HEALTHY_SECONDS
                    - CERTIFICATION_REFRESH_LEAD_SECONDS
                    - self._clock(),
                )
            await self._sleep(delay)

    async def _refresh(self) -> None:
        # Superseded forced validations may still be running. Background work
        # waits for ALL owned validation tasks, never adding an overlapping one.
        if self._tasks:
            await asyncio.gather(
                *(asyncio.shield(task) for task in tuple(self._tasks)),
                return_exceptions=True,
            )
            return
        if not self._refresh_due():
            return
        inputs = self._read_inputs()
        if self._entry is not None and self._entry.inputs != inputs:
            self._entry = None
        result = await asyncio.shield(self._begin(inputs, force=False, background=True))
        if not self._healthy(result):
            _logger.warning(
                "Background certification produced no reusable result (unhealthy, "
                "inputs changed, expired or superseded)"
            )

    def _refresh_due(self) -> bool:
        if self._entry is None:
            return True
        due = (
            self._entry.started
            + CERTIFICATION_HEALTHY_SECONDS
            - CERTIFICATION_REFRESH_LEAD_SECONDS
        )
        return self._clock() >= due

    async def get(self, *, force: bool = False) -> T:
        if self._closed:
            raise RuntimeError("certification service is closed")
        inputs = self._read_inputs()
        flight = self._select_flight(inputs, force=force)
        if flight is None or flight.background:
            entry = self._valid_entry()
            if entry is not None:
                return entry.result
        self._entry = None
        if flight is not None:
            return await asyncio.shield(flight.task)
        return await asyncio.shield(self._begin(inputs, force=force))

    def _read_inputs(self) -> object:
        try:
            return self._inputs()
        except BaseException:
            self._entry = None
            self._flight = None
            raise

    def _valid_entry(self) -> _Entry[T] | None:
        if (
            self._entry is not None
            and self._clock() - self._entry.started < CERTIFICATION_HEALTHY_SECONDS
        ):
            return self._entry
        return None

    def _begin(
        self, inputs: object, *, force: bool, background: bool = False
    ) -> asyncio.Task[T]:
        task = asyncio.create_task(self._observe(inputs, self._clock(), background))
        self._flight = _Flight(task, inputs, force, background)
        self._tasks.add(task)
        task.add_done_callback(self._finished)
        return task

    def _select_flight(self, inputs: object, *, force: bool) -> _Flight[T] | None:
        flight = self._flight
        if flight is not None and not flight.reusable(inputs, force=force):
            self._flight = None
            flight = None
        if force or (self._entry is not None and self._entry.inputs != inputs):
            self._entry = None
        return flight

    def _finished(self, task: asyncio.Task[T]) -> None:
        self._tasks.discard(task)
        if not task.cancelled():
            task.exception()  # Retrieve failures even when every waiter cancelled.

    async def _observe(self, inputs: object, started: float, background: bool) -> T:
        task = asyncio.current_task()
        try:
            result = await self._validate()
            if not self._is_current(task):
                return self._changed()
            if not self._valid_observation(inputs, started, background):
                self._entry = None
                return self._changed()
            if self._healthy(result):
                self._entry = _Entry(inputs, started, result)
            else:
                self._entry = None
            return result
        except BaseException:
            if self._is_current(task):
                self._entry = None
            raise
        finally:
            if self._is_current(task):
                self._flight = None

    def _is_current(self, task: asyncio.Task[T] | None) -> bool:
        return self._flight is not None and self._flight.task is task

    def _valid_observation(
        self, inputs: object, started: float, background: bool
    ) -> bool:
        if self._inputs() != inputs:
            return False
        return not background or self._clock() - started < CERTIFICATION_HEALTHY_SECONDS

    async def aclose(self) -> None:
        self._closed = True
        if self._maintenance is not None:
            self._maintenance.cancel()
            await asyncio.gather(self._maintenance, return_exceptions=True)
        self._entry = None
        self._flight = None
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
