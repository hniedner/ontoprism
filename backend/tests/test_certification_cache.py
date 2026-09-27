"""Controlled-clock and concurrent certification reuse contracts."""

import asyncio

import pytest

from backend.certification_cache import CertificationCache

pytestmark = pytest.mark.unit


class _Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = asyncio.Queue()

    async def sleep(self, delay):
        wake = asyncio.Event()
        await self.sleeps.put((delay, wake))
        await wake.wait()

    async def advance(self, now):
        delay, wake = await asyncio.wait_for(self.sleeps.get(), 1)
        assert delay > 0
        self.now = now
        wake.set()


async def test_background_warmup_and_refresh_swap_before_original_expiry():
    clock = _Clock()
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def validate():
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
            await release.wait()
        return f"ready-{calls}"

    cache = CertificationCache(
        clock=lambda: clock.now,
        inputs=lambda: b"proof",
        validate=validate,
        healthy=lambda _: True,
        changed=lambda: "changed",
        sleep=clock.sleep,
    )
    cache.start()
    cache.start()  # Idempotent: never schedule a second repository loop.
    await clock.advance(269)
    assert await cache.get() == "ready-1"  # warmed before any request
    assert calls == 1
    await clock.advance(270)
    await asyncio.wait_for(entered.wait(), 1)
    assert await cache.get() == "ready-1"  # unexpired result during refresh
    release.set()
    await clock.advance(301)
    assert await cache.get() == "ready-2"  # new window starts at 270
    assert calls == 2
    await cache.aclose()


@pytest.mark.parametrize("failure", ["unhealthy", "exception", "input-change"])
async def test_background_refresh_failure_evicts_without_negative_cache(failure):
    clock = _Clock()
    proof = [b"first"]
    calls = 0

    async def validate():
        nonlocal calls
        calls += 1
        if calls == 2:
            if failure == "exception":
                raise OSError("unreachable")
            if failure == "input-change":
                proof[0] = b"second"
            return "unhealthy"
        return f"ready-{calls}"

    cache = CertificationCache(
        clock=lambda: clock.now,
        inputs=lambda: proof[0],
        validate=validate,
        healthy=lambda value: value.startswith("ready"),
        changed=lambda: "changed",
        sleep=clock.sleep,
    )
    cache.start()
    await clock.advance(270)
    # Waiting for the next sleep means refresh finished, without a request.
    delay, _wake = await asyncio.wait_for(clock.sleeps.get(), 1)
    assert delay > 0
    clock.now = 271
    assert await cache.get() == "ready-3"
    assert calls == 3
    clock.now = 301
    assert await cache.get() == "ready-3"
    await cache.aclose()


async def test_expired_entry_waits_for_slow_refresh_and_shutdown_cancels_it():
    clock = _Clock()
    entered, cancelled = asyncio.Event(), asyncio.Event()
    calls = 0

    async def validate():
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        return "ready"

    cache = CertificationCache(
        clock=lambda: clock.now,
        inputs=lambda: b"proof",
        validate=validate,
        healthy=lambda _: True,
        changed=lambda: "changed",
        sleep=clock.sleep,
    )
    cache.start()
    await clock.advance(270)
    await asyncio.wait_for(entered.wait(), 1)
    clock.now = 300
    request = asyncio.create_task(cache.get())
    await asyncio.sleep(0)
    assert not request.done()
    assert calls == 2
    await cache.aclose()
    assert cancelled.is_set()
    with pytest.raises(asyncio.CancelledError):
        await request


async def test_early_request_joins_startup_validation():
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def validate():
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return "ready"

    cache = CertificationCache(
        clock=lambda: 0,
        inputs=lambda: b"proof",
        validate=validate,
        healthy=lambda _: True,
        changed=lambda: "changed",
    )
    cache.start()
    await asyncio.wait_for(entered.wait(), 1)
    request = asyncio.create_task(cache.get())
    await asyncio.sleep(0)
    assert not request.done()
    release.set()
    assert await request == "ready"
    assert calls == 1
    await cache.aclose()


async def test_force_supersedes_background_refresh_without_old_result_overwrite():
    clock = _Clock()
    events = [asyncio.Event() for _ in range(4)]
    calls = 0

    async def validate():
        nonlocal calls
        calls += 1
        index = calls
        if index == 2:
            events[0].set()
            await events[1].wait()
        if index == 3:
            events[2].set()
            await events[3].wait()
        return f"ready-{index}"

    cache = CertificationCache[str](
        clock=lambda: clock.now,
        inputs=lambda: b"proof",
        validate=validate,
        healthy=lambda _: True,
        changed=lambda: "changed",
        sleep=clock.sleep,
    )
    cache.start()
    await clock.advance(270)
    await asyncio.wait_for(events[0].wait(), 1)
    forced = asyncio.create_task(cache.get(force=True))
    await asyncio.wait_for(events[2].wait(), 1)
    ordinary = asyncio.create_task(cache.get())
    await asyncio.sleep(0)
    assert not ordinary.done()  # force=True evicts even an unexpired old result
    events[3].set()
    assert await forced == await ordinary == "ready-3"
    events[1].set()
    await clock.advance(301)
    assert await cache.get() == "ready-3"
    assert calls == 3
    await cache.aclose()


async def test_background_waits_for_existing_validation_and_stops_while_asleep():
    clock = _Clock()
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def validate():
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return "ready"

    cache = CertificationCache[str](
        clock=lambda: clock.now,
        inputs=lambda: b"proof",
        validate=validate,
        healthy=lambda _: True,
        changed=lambda: "changed",
        sleep=clock.sleep,
    )
    request = asyncio.create_task(cache.get())
    await entered.wait()
    cache.start()
    await asyncio.sleep(0)
    assert calls == 1
    release.set()
    assert await request == "ready"
    delay, wake = await asyncio.wait_for(clock.sleeps.get(), 1)
    assert delay == 270
    await cache.aclose()
    clock.now = 300
    wake.set()
    await asyncio.sleep(0)
    assert calls == 1
    with pytest.raises(RuntimeError, match="closed"):
        cache.start()


async def test_refresh_result_that_itself_exceeded_window_is_not_served():
    clock = _Clock()
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def validate():
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
            await release.wait()
        return "ready"

    cache = CertificationCache[str](
        clock=lambda: clock.now,
        inputs=lambda: b"proof",
        validate=validate,
        healthy=lambda _: True,
        changed=lambda: "changed",
        sleep=clock.sleep,
    )
    cache.start()
    await clock.advance(270)
    await asyncio.wait_for(entered.wait(), 1)
    clock.now = 570
    request = asyncio.create_task(cache.get())
    await asyncio.sleep(0)
    release.set()
    assert await request == "changed"
    await cache.aclose()


async def test_concurrent_callers_share_validation_despite_cancelled_waiter():
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def validate():
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return "ready"

    cache = CertificationCache(
        clock=lambda: 0,
        inputs=lambda: b"proof",
        validate=validate,
        healthy=lambda result: result == "ready",
        changed=lambda: "changed",
    )
    first = asyncio.create_task(cache.get())
    await entered.wait()
    second = asyncio.create_task(cache.get())
    await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert await second == "ready"
    assert await cache.get() == "ready"
    assert calls == 1
    await cache.aclose()


async def test_force_supersedes_old_validation_and_forced_callers_share_new_work():
    started = [asyncio.Event(), asyncio.Event()]
    release = [asyncio.Event(), asyncio.Event()]
    calls = 0

    async def validate():
        nonlocal calls
        index = calls
        calls += 1
        started[index].set()
        await release[index].wait()
        return f"ready-{index}"

    cache = CertificationCache(
        clock=lambda: 0,
        inputs=lambda: b"proof",
        validate=validate,
        healthy=lambda _: True,
        changed=lambda: "changed",
    )
    old = asyncio.create_task(cache.get())
    await started[0].wait()
    forced = asyncio.create_task(cache.get(force=True))
    await started[1].wait()
    shared = asyncio.create_task(cache.get(force=True))
    ordinary = asyncio.create_task(cache.get())
    await asyncio.sleep(0)
    release[0].set()
    assert await old == "changed"
    release[1].set()
    assert await asyncio.gather(forced, shared, ordinary) == ["ready-1"] * 3
    assert calls == 2
    assert await cache.get() == "ready-1"
    await cache.aclose()


async def test_changed_inputs_and_slow_validation_prevent_reuse():
    now = [0.0]
    proof = [b"old"]
    calls = 0

    async def validate():
        nonlocal calls
        calls += 1
        if calls == 1:
            proof[0] = b"new"
        if calls == 2:
            now[0] += 300
        return "ready"

    cache = CertificationCache(
        clock=lambda: now[0],
        inputs=lambda: proof[0],
        validate=validate,
        healthy=lambda result: result == "ready",
        changed=lambda: "changed",
    )
    assert await cache.get() == "changed"
    assert await cache.get() == "ready"
    assert await cache.get() == "ready"
    assert calls == 3
    proof[0] = b"third"
    assert await cache.get() == "ready"
    assert calls == 4
    await cache.aclose()


async def test_failure_evicts_success_and_shutdown_cancels_owned_work():
    entered = asyncio.Event()
    calls = 0

    async def validate():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("external failure")
        if calls == 4:
            entered.set()
            await asyncio.Event().wait()
        return "ready"

    cache = CertificationCache(
        clock=lambda: 0,
        inputs=lambda: b"proof",
        validate=validate,
        healthy=lambda _: True,
        changed=lambda: "changed",
    )
    assert await cache.get() == "ready"
    with pytest.raises(RuntimeError, match="external failure"):
        await cache.get(force=True)
    assert await cache.get() == "ready"
    assert calls == 3
    waiting = asyncio.create_task(cache.get(force=True))
    await entered.wait()
    await cache.aclose()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    with pytest.raises(RuntimeError, match="closed"):
        await cache.get()


async def test_input_read_failure_evicts_and_supersedes_inflight_work():
    entered, release = asyncio.Event(), asyncio.Event()
    proof = [b"first"]
    failed = [False]
    calls = 0

    def inputs():
        if failed[0]:
            raise OSError("proof unreadable")
        return proof[0]

    async def validate():
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
            await release.wait()
        return "ready"

    cache = CertificationCache[str](
        clock=lambda: 0,
        inputs=inputs,
        validate=validate,
        healthy=lambda _: True,
        changed=lambda: "changed",
    )
    assert await cache.get() == "ready"
    old = asyncio.create_task(cache.get(force=True))
    await entered.wait()
    failed[0] = True
    with pytest.raises(OSError, match="proof unreadable"):
        await cache.get()
    failed[0] = False
    proof[0] = b"second"
    assert await cache.get() == "ready"
    release.set()
    assert await old == "changed"
    assert calls == 3
    await cache.aclose()


async def test_changed_inputs_supersede_inflight_work():
    entered, release = asyncio.Event(), asyncio.Event()
    proof = [b"first"]
    calls = 0

    async def validate():
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
        return "ready"

    cache = CertificationCache[str](
        clock=lambda: 0,
        inputs=lambda: proof[0],
        validate=validate,
        healthy=lambda _: True,
        changed=lambda: "changed",
    )
    old = asyncio.create_task(cache.get())
    await entered.wait()
    proof[0] = b"second"
    assert await cache.get() == "ready"
    release.set()
    assert await old == "changed"
    assert await cache.get() == "ready"
    assert calls == 2
    await cache.aclose()
