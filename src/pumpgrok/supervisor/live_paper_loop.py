"""Long-running live-data paper loop: feed, health endpoint, orderly stop (ROADMAP P0-2b).

``run --mode paper --live-paper --serve`` lands here. The loop runs one PumpPortal
feed through :func:`~pumpgrok.events.feed_link.run_feed` (backoff, staleness, held-mint
resync) and serves ``/healthz`` and ``/readyz`` with the feed status. It places
nothing: the desk's place callback is the CLI's ``_no_place`` stub, and feed events
are counted, not traded. SIGINT / SIGTERM stop the feed and close the server.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from pumpgrok.datafeed.portal import PortalFeed, RateLimiter
from pumpgrok.events.feed_link import FeedLink, run_feed
from pumpgrok.events.sources import SourceStatus, SourceSupervisor
from pumpgrok.replay.clock import FixtureClock, WallClock
from pumpgrok.replay.rng import make_rng
from pumpgrok.risk.feed_health import FeedHealth
from pumpgrok.supervisor.health import HealthServer
from pumpgrok.supervisor.lifecycle import install_signal_handlers
from pumpgrok.supervisor.live_paper import LivePaperDesk

__all__ = ["LIVE_PAPER_FEED", "serve_live_paper"]

log = logging.getLogger(__name__)

LIVE_PAPER_FEED = "pumpportal"


async def serve_live_paper(
    desk: LivePaperDesk,
    *,
    connect: Callable[[], AsyncIterator[dict[str, Any]]],
    sidecar_path: Path,
    host: str,
    port: int,
    rate_limit_per_10s: int = 30,
    stop: asyncio.Event | None = None,
    on_bound: Callable[[int], object] | None = None,
) -> int:
    """Run the feed and the health server until stopped; return 0 on an orderly stop."""
    clock = desk.clock
    if not isinstance(clock, (FixtureClock, WallClock)):  # build_live_paper guarantees it
        raise TypeError("live-paper loop needs a fixture or wall clock")
    stop = stop if stop is not None else asyncio.Event()
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sources = SourceSupervisor(clock, make_rng(7), path=sidecar_path)
    link = FeedLink(LIVE_PAPER_FEED, sources, FeedHealth(clock), clock)
    seen: dict[str, int] = {"events": 0}

    def on_event(_event: Any) -> None:
        seen["events"] += 1

    portal = PortalFeed(
        clock, RateLimiter(clock, capacity=rate_limit_per_10s), link, lambda _r: None, on_event
    )

    def feed_up() -> bool:
        # The supervisor calls a never-framed source live; health waits for a real frame.
        return sources.last_frame_at is not None and link.status() is SourceStatus.live

    def status() -> dict[str, Any]:
        feed = "connecting" if sources.last_frame_at is None else str(link.status())
        return {
            "status": "ok" if feed_up() else "degraded",
            "mode": "paper",
            "places_orders": False,
            "feed": {LIVE_PAPER_FEED: feed},
            "events_seen": seen["events"],
            "unknown_frames": portal.unknown_frames,
            "held_mints": sorted(desk.book.held()),
        }

    server = HealthServer(clock, status, feed_up)
    feed_task: asyncio.Task[int] | None = None

    def request_stop(reason: str) -> None:
        log.info("live-paper stop requested (%s)", reason)
        stop.set()
        if feed_task is not None:
            feed_task.cancel()

    try:
        bound = await server.start(host, port)
        log.info("live-paper listening on %s:%d (paper only, places nothing)", host, bound)
        if on_bound is not None:
            on_bound(bound)
        install_signal_handlers(request_stop)
        feed_task = asyncio.create_task(
            run_feed(
                link,
                connect,
                portal.handle_frame,
                portal.handle_timeout,
                clock=clock,
                held_mints=lambda: sorted(desk.book.held()),
                stop=stop,
            )
        )
        stopper = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait({feed_task, stopper}, return_when=asyncio.FIRST_COMPLETED)
        stopper.cancel()
        if feed_task not in done:
            feed_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await feed_task  # re-raises an unknown feed failure
        return 0
    finally:
        await server.stop()
        sources.close()
