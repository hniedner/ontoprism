"""Worker-local, non-sliding certification reuse and shared live validation."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

CERTIFICATION_HEALTHY_SECONDS = 300.0


@dataclass(frozen=True)
class _Flight[T]:
    task: asyncio.Task[T]
    inputs: object
    forced: bool

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
    ) -> None:
        self._clock = clock
        self._inputs = inputs
        self._validate = validate
        self._healthy = healthy
        self._changed = changed
        self._entry: tuple[object, float, T] | None = None
        self._flight: _Flight[T] | None = None
        self._tasks: set[asyncio.Task[T]] = set()
        self._closed = False

    async def get(self, *, force: bool = False) -> T:
        if self._closed:
            raise RuntimeError("certification service is closed")
        try:
            inputs = self._inputs()
        except BaseException:
            self._entry = None
            self._flight = None
            raise
        flight = self._select_flight(inputs, force=force)
        if flight is not None:
            return await asyncio.shield(flight.task)
        if self._entry is not None:
            _, started, result = self._entry
            if self._clock() - started < CERTIFICATION_HEALTHY_SECONDS:
                return result
        self._entry = None
        task = asyncio.create_task(self._observe(inputs, self._clock()))
        self._flight = _Flight(task, inputs, force)
        self._tasks.add(task)
        task.add_done_callback(self._finished)
        return await asyncio.shield(task)

    def _select_flight(self, inputs: object, *, force: bool) -> _Flight[T] | None:
        flight = self._flight
        if flight is not None and not flight.reusable(inputs, force=force):
            self._flight = None
            flight = None
        if force or (self._entry is not None and self._entry[0] != inputs):
            self._entry = None
        return flight

    def _finished(self, task: asyncio.Task[T]) -> None:
        self._tasks.discard(task)
        if not task.cancelled():
            task.exception()  # Retrieve failures even when every waiter cancelled.

    async def _observe(self, inputs: object, started: float) -> T:
        task = asyncio.current_task()
        try:
            result = await self._validate()
            if (
                self._flight is None
                or self._flight.task is not task
                or self._inputs() != inputs
            ):
                return self._changed()
            if self._healthy(result):
                self._entry = (inputs, started, result)
            return result
        finally:
            if self._flight is not None and self._flight.task is task:
                self._flight = None

    async def aclose(self) -> None:
        self._closed = True
        self._entry = None
        self._flight = None
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
