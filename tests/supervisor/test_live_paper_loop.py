"""P0-2b: long-running live-data paper loop (feed + /healthz), offline with a fake transport."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from pumpgrok.cli import main
from pumpgrok.cli.codes import EXIT_CONFIG
from pumpgrok.core.adapter import ExecutionMode
from pumpgrok.datafeed.portal_ws import normalize_portal_message
from pumpgrok.ledger.store import LedgerStore
from pumpgrok.replay.clock import FixtureClock
from pumpgrok.supervisor.live_paper import LivePaperDesk, build_live_paper
from pumpgrok.supervisor.live_paper_loop import serve_live_paper

START = 1_769_900_000.0


@pytest.mark.parametrize(
    ("raw", "frame"),
    [
        (
            '{"txType": "create", "mint": "MintA", "signature": "s1", "name": "x"}',
            {"method": "subscribeNewToken", "mint": "MintA", "signature": "s1"},
        ),
        (
            '{"txType": "migrate", "mint": "MintB"}',
            {"method": "subscribeMigration", "mint": "MintB", "signature": ""},
        ),
        ('{"message": "Successfully subscribed to token creation events."}', None),
        ('{"txType": "buy", "mint": "MintA"}', None),
        ('{"txType": "create"}', None),
        ("[1, 2]", None),
        ("not json", None),
        (b"\xff\xfe", None),
    ],
)
def test_normalize_keeps_only_known_messages(
    raw: str | bytes, frame: dict[str, Any] | None
) -> None:
    assert normalize_portal_message(raw) == frame


def _desk(tmp_path: Path) -> LivePaperDesk:
    async def no_place(_intent: Any) -> None:
        return None

    return build_live_paper(
        mode=ExecutionMode.PAPER,
        store=LedgerStore(str(tmp_path / "ledger.sqlite")),
        clock=FixtureClock(START),
        place=no_place,
        params={"momentum_breakout": {"breakout_bps": 100}},
    )


async def _get(port: int, path: str) -> tuple[int, dict[str, Any]]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    await writer.drain()
    raw = await reader.read()
    writer.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    return int(head.split()[1]), json.loads(body)


async def test_loop_serves_feed_health_and_stops_cleanly(tmp_path: Path) -> None:
    desk = _desk(tmp_path)
    stop = asyncio.Event()
    bound: list[int] = []
    framed = asyncio.Event()
    hold = asyncio.Event()

    async def connect() -> AsyncIterator[dict[str, Any]]:
        yield {"method": "subscribeNewToken", "mint": "MintA", "signature": "s1"}
        yield {"method": "subscribeNewToken", "mint": "MintB", "signature": "s2"}
        framed.set()
        await hold.wait()  # an open connection with no traffic
        yield {}  # pragma: no cover

    task = asyncio.create_task(
        serve_live_paper(
            desk,
            connect=connect,
            sidecar_path=tmp_path / "feed" / "sidecar.sqlite",
            host="127.0.0.1",
            port=0,
            stop=stop,
            on_bound=bound.append,
        )
    )
    await asyncio.wait_for(framed.wait(), 5)
    code, body = await _get(bound[0], "/healthz")
    assert code == 200
    assert body["status"] == "ok"
    assert (body["mode"], body["places_orders"]) == ("paper", False)
    assert body["feed"] == {"pumpportal": "live"}
    assert body["events_seen"] == 2
    ready, _ = await _get(bound[0], "/readyz")
    assert ready == 200
    stop.set()
    assert await asyncio.wait_for(task, 5) == 0
    trades = desk.store.read(types=["OrderPlaced"], limit=None)
    assert trades == []


async def test_loop_reports_degraded_before_first_frame_and_propagates_unknown_errors(
    tmp_path: Path,
) -> None:
    desk = _desk(tmp_path)
    bound: list[int] = []
    listening = asyncio.Event()
    gate = asyncio.Event()

    def on_bound(port: int) -> None:
        bound.append(port)
        listening.set()

    async def connect() -> AsyncIterator[dict[str, Any]]:
        await gate.wait()
        raise ValueError("decoder bug")
        yield {}  # pragma: no cover

    task = asyncio.create_task(
        serve_live_paper(
            desk,
            connect=connect,
            sidecar_path=tmp_path / "sidecar.sqlite",
            host="127.0.0.1",
            port=0,
            on_bound=on_bound,
        )
    )
    await asyncio.wait_for(listening.wait(), 5)
    code, body = await _get(bound[0], "/healthz")
    assert (code, body["status"], body["feed"]) == (503, "degraded", {"pumpportal": "connecting"})
    gate.set()
    with pytest.raises(ValueError, match="decoder"):
        await asyncio.wait_for(task, 5)


def test_cli_serve_needs_a_health_port(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PUMPGROK_OD04_PROBE_REVIEW", "approved")
    # The paper onboarding gate (xai key, seat grants) is tested on its own; not under test here.
    monkeypatch.setattr("pumpgrok.cli.verbs_state.check_start", lambda *_a, **_k: [])
    monkeypatch.delenv("PUMPGROK_HEALTH__PORT", raising=False)
    assert main(["run", "--mode", "paper", "--live-paper", "--serve"]) == EXIT_CONFIG


def test_cli_live_paper_builds_with_the_review_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The OD-04 flag is a known env name, and the default strategy gets its params."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "var").mkdir()
    monkeypatch.setenv("PUMPGROK_OD04_PROBE_REVIEW", "approved")
    # The paper onboarding gate (xai key, seat grants) is tested on its own; not under test here.
    monkeypatch.setattr("pumpgrok.cli.verbs_state.check_start", lambda *_a, **_k: [])
    assert main(["run", "--mode", "paper", "--live-paper"]) == 0
