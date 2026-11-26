"""Characterization pins for PaperAdapter sell placement (``_place_sell``).

Each scenario runs the unsplit adapter on a fresh ledger and records the
returned outcome (fill fields or refusal) together with every ledger row,
hash included. The digests in ``PINS`` were recorded on the unsplit code
(CONTEXT D-10) and must still hold after ``sell_stages.py`` lands.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
from collections.abc import Awaitable, Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from pumpgrok.core.adapter import Fill, OrderRequest, Side
from pumpgrok.core.errors import OrderRejectedError
from pumpgrok.core.ids import IdFactory
from pumpgrok.core.risk_budget import RiskBudget
from pumpgrok.execution.paper_adapter import CurveSnapshot, PaperAdapter
from pumpgrok.ledger.store import LedgerStore
from pumpgrok.replay.clock import FixtureClock
from pumpgrok.replay.rng import make_rng
from pumpgrok.risk.engine import SELL_SEAT, RiskEngine

START = 1_769_900_000.0
EQUITY = 10_000_000_000
TICKET = "SOL-20260201-001"
SELL_TICKET = "SOL-20260201-002"
MINT = "So11111111111111111111111111111111111111112"
S0 = 42_000_000_000
T0 = 766_428_571_428_571
SIZE = 200_000_000


class Curves:
    """Pop one scripted snapshot per read; hold the last."""

    def __init__(self, reads: list[CurveSnapshot | None]) -> None:
        self.reads = list(reads)

    async def curve(self, mint: str) -> CurveSnapshot | None:
        if len(self.reads) > 1:
            return self.reads.pop(0)
        return self.reads[0]


def snap(s: int, t: int) -> CurveSnapshot:
    return CurveSnapshot(mint=MINT, s_lamports=s, t_units=t)


class Harness:
    def __init__(
        self,
        root: Path,
        reads: list[CurveSnapshot | None],
        *,
        autonomy: str = "autonomous-paper",
        seed: int = 7,
    ) -> None:
        self.clock = FixtureClock(START)
        self.store = LedgerStore(str(root / "ledger.sqlite"))
        self.store.set_meta("initial_equity_lamports", str(EQUITY))
        self.store.set_meta("source", "paper")
        self.ids = IdFactory(self.clock, make_rng(seed))
        self.curves = Curves(reads)
        budget = RiskBudget()
        self.risk = RiskEngine(
            store=self.store,
            budget=budget,
            ids=IdFactory(self.clock, random.Random(seed)),
            clock=self.clock,
            autonomy=autonomy,
            get_reserves=lambda _mint: (S0, T0),
        )
        self.adapter = PaperAdapter(
            store=self.store,
            risk=self.risk,
            ids=self.ids,
            clock=self.clock,
            rng=make_rng(seed),
            budget=budget,
            price_source=self.curves,
        )

    async def drive(self, *coros: Awaitable[Any]) -> list[Any]:
        """Advance virtual time until every call settles; errors come back as values."""
        tasks = [asyncio.create_task(c) for c in coros]
        for _ in range(400):
            if all(t.done() for t in tasks):
                break
            self.clock.advance_to(self.clock.now() + 1.0)
            await asyncio.sleep(0)
        results: list[Any] = []
        for task in tasks:
            if not task.done():
                task.cancel()
                raise AssertionError("scenario did not settle in virtual time")
            try:
                results.append(await task)
            except Exception as exc:
                results.append(exc)
        return results

    def buy_request(self) -> OrderRequest:
        return OrderRequest(
            ticket_id=TICKET,
            mint=MINT,
            side=Side.BUY,
            decision_id=self.ids.ulid(),
            size_lamports=SIZE,
            max_slippage_bps=100,
            approval_id=f"apr-{self.ids.ulid()}",
            correlation_id="test",
        )

    def sell_request(
        self,
        *,
        units: int | None = None,
        size: int | None = None,
        exit_reason: str | None = None,
    ) -> OrderRequest:
        return OrderRequest(
            ticket_id=SELL_TICKET,
            mint=MINT,
            side=Side.SELL,
            decision_id=self.ids.ulid(),
            token_units=units,
            size_lamports=size,
            max_slippage_bps=100,
            approval_id=f"apr-{self.ids.ulid()}",
            exit_reason=exit_reason,
            correlation_id="test",
        )

    async def open_position(self) -> Fill:
        self.curves.reads = [snap(S0, T0)]
        result = await self.drive(
            self.adapter.place(self.buy_request(), seat="bot-04-sniper", correlation_id="test")
        )
        fill = result[0]
        assert isinstance(fill, Fill)
        await self.adapter.mark_to_market(MINT, correlation_id="test")
        return fill

    def ledger(self) -> list[dict[str, Any]]:
        return [dict(event) for event in self.store.read(limit=None)]


def outcome(value: Any) -> dict[str, Any]:
    if isinstance(value, Fill):
        return {"fill": value.model_dump(mode="json")}
    if isinstance(value, OrderRejectedError):
        return {
            "error": type(value).__name__,
            "reason": value.reason,
            "stage": value.stage,
            "fees": value.fees_lamports,
            "message": str(value),
        }
    raise AssertionError(f"unexpected outcome {value!r}")


def digest(trace: dict[str, Any]) -> str:
    text = json.dumps(trace, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


async def scn_sell_full_close(root: Path) -> dict[str, Any]:
    h = Harness(root, [snap(S0, T0)])
    buy = await h.open_position()
    h.curves.reads = [snap(S0, T0), snap(S0, T0)]
    sell = await h.drive(
        h.adapter.place(
            h.sell_request(units=buy.filled_units), seat=SELL_SEAT, correlation_id="test"
        )
    )
    return {"outcome": outcome(sell[0]), "rows": h.ledger()}


async def scn_sell_partial_over_holding(root: Path) -> dict[str, Any]:
    h = Harness(root, [snap(S0, T0)])
    buy = await h.open_position()
    h.curves.reads = [snap(S0, T0), snap(S0, T0)]
    sell = await h.drive(
        h.adapter.place(
            h.sell_request(units=buy.filled_units * 2), seat=SELL_SEAT, correlation_id="test"
        )
    )
    return {"outcome": outcome(sell[0]), "rows": h.ledger()}


async def scn_sell_size_based(root: Path) -> dict[str, Any]:
    h = Harness(root, [snap(S0, T0)])
    await h.open_position()
    h.curves.reads = [snap(S0, T0), snap(S0, T0)]
    sell = await h.drive(
        h.adapter.place(h.sell_request(size=SIZE // 2), seat=SELL_SEAT, correlation_id="test")
    )
    return {"outcome": outcome(sell[0]), "rows": h.ledger()}


async def scn_sell_no_position(root: Path) -> dict[str, Any]:
    h = Harness(root, [snap(S0, T0)])
    sell = await h.drive(
        h.adapter.place(h.sell_request(units=1_000_000), seat=SELL_SEAT, correlation_id="test")
    )
    return {"outcome": outcome(sell[0]), "rows": h.ledger()}


async def scn_sell_below_liquidity_floor(root: Path) -> dict[str, Any]:
    h = Harness(root, [snap(S0, T0)])
    await h.open_position()
    h.curves.reads = [snap(S0, T0)]
    sell = await h.drive(
        h.adapter.place(h.sell_request(units=T0 // 5), seat=SELL_SEAT, correlation_id="test")
    )
    return {"outcome": outcome(sell[0]), "rows": h.ledger()}


async def scn_sell_no_reserves_accepted(root: Path) -> dict[str, Any]:
    h = Harness(root, [snap(S0, T0)])
    buy = await h.open_position()
    h.curves.reads = [None]
    sell = await h.drive(
        h.adapter.place(
            h.sell_request(units=buy.filled_units), seat=SELL_SEAT, correlation_id="test"
        )
    )
    return {"outcome": outcome(sell[0]), "rows": h.ledger()}


async def scn_sell_slippage_confirming(root: Path) -> dict[str, Any]:
    h = Harness(root, [snap(S0, T0)])
    buy = await h.open_position()
    h.curves.reads = [snap(S0, T0), snap(S0 - S0 // 20, T0)]
    sell = await h.drive(
        h.adapter.place(
            h.sell_request(units=buy.filled_units), seat=SELL_SEAT, correlation_id="test"
        )
    )
    return {"outcome": outcome(sell[0]), "rows": h.ledger()}


async def scn_sell_no_reserves_confirming(root: Path) -> dict[str, Any]:
    h = Harness(root, [snap(S0, T0)])
    buy = await h.open_position()
    h.curves.reads = [snap(S0, T0), None]
    sell = await h.drive(
        h.adapter.place(
            h.sell_request(units=buy.filled_units), seat=SELL_SEAT, correlation_id="test"
        )
    )
    return {"outcome": outcome(sell[0]), "rows": h.ledger()}


async def scn_sell_concurrent_confirming(root: Path) -> dict[str, Any]:
    h = Harness(root, [snap(S0, T0)])
    buy = await h.open_position()
    h.curves.reads = [snap(S0, T0)]
    first = h.sell_request(units=buy.filled_units)
    second = h.sell_request(units=buy.filled_units)
    results = await h.drive(
        h.adapter.place(first, seat=SELL_SEAT, correlation_id="test"),
        h.adapter.place(second, seat=SELL_SEAT, correlation_id="test"),
    )
    return {"outcomes": [outcome(r) for r in results], "rows": h.ledger()}


async def scn_sell_repeat_replays_fill(root: Path) -> dict[str, Any]:
    h = Harness(root, [snap(S0, T0)])
    buy = await h.open_position()
    h.curves.reads = [snap(S0, T0), snap(S0, T0)]
    request = h.sell_request(units=buy.filled_units)
    first = await h.drive(h.adapter.place(request, seat=SELL_SEAT, correlation_id="test"))
    second = await h.drive(h.adapter.place(request, seat=SELL_SEAT, correlation_id="test"))
    return {"first": outcome(first[0]), "second": outcome(second[0]), "rows": h.ledger()}


async def scn_sell_direct_place_sell(root: Path) -> dict[str, Any]:
    h = Harness(root, [snap(S0, T0)])
    buy = await h.open_position()
    h.curves.reads = [snap(S0, T0), snap(S0, T0)]
    request = h.sell_request(units=buy.filled_units)
    result = await h.drive(
        h.adapter._place_sell(
            request,
            curve0=snap(S0, T0),
            ledger_source="paper",
            correlation_id="test",
            seat=SELL_SEAT,
            exit_reason=None,
            creator=None,
            owner=object(),
        )
    )
    return {"outcome": outcome(result[0]), "rows": h.ledger()}


SCENARIOS: dict[str, Callable[[Path], Awaitable[dict[str, Any]]]] = {
    "sell_full_close": scn_sell_full_close,
    "sell_partial_over_holding": scn_sell_partial_over_holding,
    "sell_size_based": scn_sell_size_based,
    "sell_no_position": scn_sell_no_position,
    "sell_below_liquidity_floor": scn_sell_below_liquidity_floor,
    "sell_no_reserves_accepted": scn_sell_no_reserves_accepted,
    "sell_slippage_confirming": scn_sell_slippage_confirming,
    "sell_no_reserves_confirming": scn_sell_no_reserves_confirming,
    "sell_concurrent_confirming": scn_sell_concurrent_confirming,
    "sell_repeat_replays_fill": scn_sell_repeat_replays_fill,
    "sell_direct_place_sell": scn_sell_direct_place_sell,
}

_PIN_TABLE = """
sell_full_close 875de950bcaf42e126a6abcf9d42f42632a4837129c238597b2b114f306b6824
sell_partial_over_holding 91f58623e46cdf89148373020b42e2b37ed2cf3e008cfe342433862a0caa1cdd
sell_size_based 7ab6365d1f67f94c7402f0311bf58be1b151aa1cb8fbc9649381229c71b737b1
sell_no_position 442d4380c78cb83159abb38a1d6113caf217daa2c8a7411f513b831d839ca966
sell_below_liquidity_floor 55eb5000315e618caaf6cd1e4ae29236d4e8e59ae9ca207043b94d6599a330b9
sell_no_reserves_accepted 8482301bbc045836a223a654ceff7b88498cc8e40a390b8ddc6ea202f3ac37c5
sell_slippage_confirming eff800b146037dfbad129bbb885a82842c1fb9878fe63c0f5a51924405cee4e1
sell_no_reserves_confirming fd0c1b6a09bbef96683a48c57f4788fb60e57c7c9811b59804930f907c40e8c0
sell_concurrent_confirming 0bf05192528b98c12986cde31a8676141688ce79ee6fe92fbe21663d314f98e7
sell_repeat_replays_fill 1cb76af9566d0b629820d871abac426c4cb48abc0fbdabe0613863f8abb38b96
sell_direct_place_sell 875de950bcaf42e126a6abcf9d42f42632a4837129c238597b2b114f306b6824
"""
PINS: dict[str, str] = dict(line.split() for line in _PIN_TABLE.strip().splitlines())


@pytest.mark.parametrize("name", sorted(SCENARIOS))
async def test_sell_scenario_matches_pinned_digest(tmp_path: Path, name: str) -> None:
    trace = await SCENARIOS[name](tmp_path)
    assert digest(trace) == PINS[name]


async def test_full_close_fills_and_closes_the_position(tmp_path: Path) -> None:
    trace = await scn_sell_full_close(tmp_path)
    assert trace["outcome"]["fill"]["status"] == "filled"
    kinds = [row["type"] for row in trace["rows"]]
    assert kinds[-2:] == ["FillRecorded", "PositionClosed"]


async def test_over_holding_exit_is_partial_not_clipped_silently(tmp_path: Path) -> None:
    trace = await scn_sell_partial_over_holding(tmp_path)
    fill = trace["outcome"]["fill"]
    assert fill["status"] == "partial"
    assert fill["filled_units"] > 0
    assert Decimal(fill["filled_fraction"]) < 1


async def test_flat_sell_refuses_no_position_at_accepted(tmp_path: Path) -> None:
    trace = await scn_sell_no_position(tmp_path)
    assert trace["outcome"]["reason"] == "no_position"
    assert trace["outcome"]["stage"] == "ACCEPTED"


async def test_oversize_sell_refuses_below_liquidity_floor(tmp_path: Path) -> None:
    trace = await scn_sell_below_liquidity_floor(tmp_path)
    assert trace["outcome"]["reason"] == "below_liquidity_floor"
    assert trace["outcome"]["stage"] == "ACCEPTED"


async def test_slippage_refuses_at_confirming_and_charges_network_cost(tmp_path: Path) -> None:
    trace = await scn_sell_slippage_confirming(tmp_path)
    assert trace["outcome"]["reason"] == "slippage_exceeded"
    assert trace["outcome"]["stage"] == "CONFIRMING"
    assert trace["outcome"]["fees"] > 0
    kinds = [row["type"] for row in trace["rows"]]
    assert kinds.count("FillRecorded") == 1
    assert "PositionClosed" not in kinds


async def test_concurrent_exits_fill_once_and_refuse_the_other_at_confirming(
    tmp_path: Path,
) -> None:
    trace = await scn_sell_concurrent_confirming(tmp_path)
    fills = [o for o in trace["outcomes"] if "fill" in o]
    refusals = [o for o in trace["outcomes"] if "reason" in o]
    assert len(fills) == 1
    assert len(refusals) == 1
    assert refusals[0]["reason"] == "no_position"
    assert refusals[0]["stage"] == "CONFIRMING"


async def test_repeated_sell_replays_the_first_fill(tmp_path: Path) -> None:
    trace = await scn_sell_repeat_replays_fill(tmp_path)
    assert trace["second"] == trace["first"]
    fills = [row for row in trace["rows"] if row["type"] == "FillRecorded"]
    assert len(fills) == 2
