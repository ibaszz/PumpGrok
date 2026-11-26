"""P1-2: every tool output carries a provenance envelope (quality, subject, observed_at)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pumpgrok.core.side_effect import SideEffect
from pumpgrok.core.tool import DataQuality, Tool, ToolInput, ToolOutput, ToolSpec, tool
from pumpgrok.replay.clock import FixtureClock
from pumpgrok.tools._common import meta_now
from registry_harness import build_full_registry

START = 1_769_900_000.0
FETCHED = datetime.fromtimestamp(START, UTC)


def test_every_production_tool_declares_quality(tmp_path: Path) -> None:
    reg, _audits, _bots = build_full_registry(tmp_path)
    missing = [name for name in reg.names() if reg.spec(name).quality is None]
    assert missing == []
    assert {reg.spec(n).quality for n in reg.names()} <= set(get_args(DataQuality))


def test_assumed_curve_layout_tools_say_so(tmp_path: Path) -> None:
    reg, _audits, _bots = build_full_registry(tmp_path)
    for name in ("market.price", "market.liquidity", "onchain.lp_status"):
        assert reg.spec(name).quality == "onchain_assumed"
    for name in ("exec.quote", "exec.simulate", "exec.place", "exec.cancel"):
        assert reg.spec(name).quality == "simulated"


class _In(ToolInput):
    mint: str | None = None
    ticket_id: str | None = None


def _make(quality: DataQuality | None, *, preset: dict[str, object] | None = None) -> Tool:
    spec = ToolSpec(
        name="test.read",
        description="provenance stamping probe",
        input_model=_In,
        output_model=ToolOutput,
        side_effect=SideEffect.READ,
        timeout_s=1.0,
        rate_limit_key="test",
        backend="tests",
        quality=quality,
    )

    @tool(spec)
    async def run(inp: _In) -> dict[str, object]:
        meta = meta_now("test", None, FixtureClock(START))
        return {"meta": meta.model_copy(update=preset or {})}

    return run


async def test_wrapper_stamps_quality_and_subject_from_input() -> None:
    out = await _make("onchain")(_In(mint="MintA", ticket_id="SOL-1"))
    assert (out.meta.quality, out.meta.subject, out.meta.observed_at) == ("onchain", "MintA", None)


async def test_ledger_reads_are_observed_when_fetched() -> None:
    out = await _make("ledger")(_In(ticket_id="SOL-1"))
    assert (out.meta.subject, out.meta.observed_at) == ("SOL-1", FETCHED)


async def test_tool_set_values_win_and_missing_stays_none() -> None:
    preset = {"quality": "derived", "subject": "other", "observed_at": FETCHED}
    out = await _make("onchain", preset=preset)(_In(mint="MintA"))
    assert (out.meta.quality, out.meta.subject) == ("derived", "other")
    bare = await _make(None)(_In())
    assert (bare.meta.quality, bare.meta.subject, bare.meta.observed_at) == (None, None, None)
