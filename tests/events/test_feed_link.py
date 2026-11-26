"""P1-5: event sources wired into the event desk (frames reach risk; reconnect with backoff)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from pumpgrok.core.adapter import ExecutionMode
from pumpgrok.datafeed.portal import PortalFeed, RateLimiter
from pumpgrok.events.feed_link import run_feed
from pumpgrok.events.sources import SourceStatus
from pumpgrok.ledger.store import LedgerStore
from pumpgrok.replay.clock import FixtureClock
from pumpgrok.supervisor.event_desk import PUMPPORTAL_FEED, EventDesk, build_event_desk

START = 1_769_900_000.0
FRAME = {"method": "subscribeNewToken", "mint": "MintA", "signature": "s1"}


def _desk(tmp_path: Path, clock: FixtureClock) -> EventDesk:
    store = LedgerStore(str(tmp_path / "ledger.sqlite"))
    store.set_meta("initial_equity_lamports", "10000000000")
    store.set_meta("source", "paper")
    return build_event_desk(
        mode=ExecutionMode.PAPER,
        store=store,
        clock=clock,
        place=lambda intent: None,
        params={"momentum_breakout": {"breakout_bps": 100}},
        sidecar_path=tmp_path / "sidecar.sqlite",
    )


def _portal(desk: EventDesk, clock: FixtureClock) -> PortalFeed:
    assert desk.feed is not None
    return PortalFeed(clock, RateLimiter(clock), desk.feed, lambda _r: None, lambda _e: None)


def test_portal_frames_reach_risk_feed_health(tmp_path: Path) -> None:
    clock = FixtureClock(START)
    desk = _desk(tmp_path, clock)
    assert desk.feed_health.status_of(PUMPPORTAL_FEED) is SourceStatus.disconnected
    assert _portal(desk, clock).handle_frame(FRAME)
    assert desk.feed_health.status_of(PUMPPORTAL_FEED) is SourceStatus.live
    assert desk.source_states() == {PUMPPORTAL_FEED: "live"}
    clock.jump(121.0)
    assert desk.feed_health.status_of(PUMPPORTAL_FEED) is SourceStatus.stale
    assert desk.source_states() == {PUMPPORTAL_FEED: "stale"}
    verdict = desk.feed_health.check("paper", held_mints=1)
    assert (verdict.status, verdict.stale_feeds) == ("degraded", (PUMPPORTAL_FEED,))
    assert desk.feed_health.check("live", held_mints=1).blocks_entries()


async def _drive(clock: FixtureClock, task: asyncio.Task[int]) -> int:
    for _ in range(500):
        if task.done():
            break
        await asyncio.sleep(0)
        clock.advance_to(clock.now() + 0.1)
    return await task


async def test_run_feed_reconnects_with_backoff_and_resyncs_held_mints(tmp_path: Path) -> None:
    clock = FixtureClock(START)
    desk = _desk(tmp_path, clock)
    assert desk.feed is not None
    portal = _portal(desk, clock)
    stop = asyncio.Event()
    opened: list[float] = []

    async def connect() -> AsyncIterator[dict[str, Any]]:
        opened.append(clock.now())
        yield {**FRAME, "signature": f"s{len(opened)}"}
        if len(opened) == 1:
            raise ConnectionResetError("dropped")
        stop.set()
        yield {**FRAME, "signature": "late"}

    task = asyncio.create_task(
        run_feed(
            desk.feed,
            connect,
            portal.handle_frame,
            portal.handle_timeout,
            clock=clock,
            held_mints=lambda: ["MintA"],
            stop=stop,
        )
    )
    assert await _drive(clock, task) == 2
    # The first failure is backoff step 1: BACKOFF_INITIAL_S * 2 = 2 s, plus jitter in [0, 1).
    assert 2.0 <= opened[1] - opened[0] < 3.1
    assert desk.sources.cursor["held_mints"] == ["MintA"]
    assert desk.feed.status() is SourceStatus.live  # the reconnect frame ended resync


async def test_run_feed_lets_unknown_failures_propagate(tmp_path: Path) -> None:
    clock = FixtureClock(START)
    desk = _desk(tmp_path, clock)
    assert desk.feed is not None

    async def connect() -> AsyncIterator[dict[str, Any]]:
        raise ValueError("bad frame decoder")
        yield {}  # pragma: no cover

    with pytest.raises(ValueError, match="decoder"):
        await run_feed(
            desk.feed,
            connect,
            lambda _f: None,
            lambda: None,
            clock=clock,
            held_mints=list,
            stop=asyncio.Event(),
        )
