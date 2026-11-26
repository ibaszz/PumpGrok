"""Wire one event source into the desk: backoff, staleness, resync, risk (ROADMAP P1-5).

:class:`FeedLink` is the ``supervisor`` a datafeed adapter (``datafeed/portal.py``
``PortalFeed``, ``datafeed/logs.py`` ``LogsFeed``) reports to. Each good frame lands in
both the :class:`~pumpgrok.events.sources.SourceSupervisor` (backoff reset, staleness,
cursor) and the risk engine's :class:`~pumpgrok.risk.feed_health.FeedHealth`, so a silent
feed is visible to ``RiskEngine.check`` through the same timestamps the source keeps.

:func:`run_feed` is the reconnect loop around one transport: read frames until the
stream ends or fails, record the gap, resync the held mints, wait the supervisor's
backoff on the injected clock, reconnect. It opens nothing itself; the caller passes
``connect``. Time comes only from the clock.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

from pumpgrok.events.sources import SourceStatus, SourceSupervisor
from pumpgrok.replay.clock import Clock
from pumpgrok.risk.feed_health import FeedHealth, FeedSpec

__all__ = ["FeedLink", "run_feed"]

#: Transport failures that mean "reconnect", not "crash the desk".
_RECONNECTABLE = (OSError, TimeoutError, ConnectionError)


class FeedLink:
    """One named source, reported to both the source supervisor and risk feed health."""

    def __init__(
        self,
        name: str,
        sources: SourceSupervisor,
        health: FeedHealth,
        clock: Clock,
        *,
        required: bool = True,
    ) -> None:
        self.name = name
        self.sources = sources
        self.health = health
        self._clock = clock
        health.register(FeedSpec(name=name, required=required))

    def note_frame(self, at: float | None = None) -> None:
        self.sources.note_frame(at)
        self.health.note_tick(self.name, self.sources.last_frame_at)

    def note_failure(self) -> None:
        self.sources.note_failure()

    def resync(self, held_mints: Sequence[str]) -> None:
        self.sources.resync(held_mints)

    def next_delay_s(self) -> float:
        return self.sources.next_delay_s()

    def status(self) -> SourceStatus:
        return self.sources.status_at(self._clock.now())


async def run_feed(
    link: FeedLink,
    connect: Callable[[], AsyncIterator[dict[str, Any]]],
    on_frame: Callable[[dict[str, Any]], object],
    on_disconnect: Callable[[], object],
    *,
    clock: Clock,
    held_mints: Callable[[], Sequence[str]],
    stop: asyncio.Event,
) -> int:
    """Read, reconnect with backoff, resync; return the number of connections made.

    ``on_frame`` is the adapter's frame handler (it calls ``link.note_frame``);
    ``on_disconnect`` records the gap (``PortalFeed.handle_timeout`` calls
    ``link.note_failure``). A stream that ends cleanly is a disconnect too.
    Any exception outside ``_RECONNECTABLE`` propagates: an unknown failure stops
    the feed instead of retrying forever.
    """
    connections = 0
    while not stop.is_set():
        connections += 1
        try:
            async for frame in connect():
                on_frame(frame)
                if stop.is_set():
                    return connections
        except _RECONNECTABLE:
            pass
        on_disconnect()
        link.resync(held_mints())
        if stop.is_set():
            break
        await clock.sleep(link.next_delay_s())
    return connections
