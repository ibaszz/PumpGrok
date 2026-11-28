"""PumpPortal websocket transport for the live-data paper loop (ROADMAP P0-2b).

Free, keyless PumpPortal data stream. The transport subscribes only through the
read-only allowlist (``datafeed/readonly.py``) and turns each raw message into the
frame shape :class:`~pumpgrok.datafeed.portal.PortalFeed` handles. Messages are
untrusted: anything that is not a JSON object with a known ``txType`` and a mint is
dropped here, before it reaches the feed. Websocket failures are re-raised as
``ConnectionError`` so :func:`~pumpgrok.events.feed_link.run_feed` reconnects.
Nothing here can send a transaction.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import websockets
from websockets.asyncio.client import connect as ws_connect

from pumpgrok.datafeed.readonly import ALLOWED_PORTAL_METHODS, check_method

__all__ = ["PORTAL_URL", "normalize_portal_message", "portal_connect"]

PORTAL_URL = "wss://pumpportal.fun/api/data"

#: PumpPortal ``txType`` -> the subscription method PortalFeed dispatches on.
_TX_METHOD = {
    "create": "subscribeNewToken",
    "migrate": "subscribeMigration",
    "migration": "subscribeMigration",
}


def normalize_portal_message(raw: str | bytes) -> dict[str, Any] | None:
    """One raw message -> a PortalFeed frame, or None (ack, unknown, malformed)."""
    try:
        msg = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(msg, dict):
        return None
    method = _TX_METHOD.get(str(msg.get("txType", "")))
    mint = msg.get("mint")
    if method is None or not isinstance(mint, str) or not mint:
        return None
    signature = msg.get("signature")
    return {
        "method": method,
        "mint": mint,
        "signature": signature if isinstance(signature, str) else "",
    }


def portal_connect(url: str = PORTAL_URL) -> Callable[[], AsyncIterator[dict[str, Any]]]:
    """A ``connect`` factory for ``run_feed``: one websocket session per call."""

    async def connect() -> AsyncIterator[dict[str, Any]]:
        try:
            async with ws_connect(url, ping_interval=30, open_timeout=20) as ws:
                for method in sorted(ALLOWED_PORTAL_METHODS):
                    check_method(method)
                    await ws.send(json.dumps({"method": method}))
                async for raw in ws:
                    frame = normalize_portal_message(raw)
                    if frame is not None:
                        yield frame
        except websockets.exceptions.WebSocketException as exc:
            raise ConnectionError(f"pumpportal: {type(exc).__name__}") from exc

    return connect
