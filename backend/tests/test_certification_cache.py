"""Controlled-clock and concurrent certification reuse contracts."""

import asyncio

import pytest

from backend.certification_cache import CertificationCache

pytestmark = pytest.mark.unit


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
