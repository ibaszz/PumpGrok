"""P2-3 / P1-4b: agent reports stored as signals; orders cite them; fills carry them."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "execution"))

from test_paper_adapter import S0, T0, _harness, _place, _snap

from pumpgrok.engine.report import (
    SIGNAL_SEAT,
    AgentReport,
    Finding,
    is_signal,
    record_signal,
    signal_id_for,
)
from pumpgrok.execution.paper_adapter import OrderRejectedError
from pumpgrok.ledger.store import LedgerStore

MINT = "So11111111111111111111111111111111111111112"


def _report(**over: object) -> AgentReport:
    base: dict[str, object] = {
        "agent": "engine.pipeline",
        "agent_version": "test",
        "subject": MINT,
        "ts_utc": "2026-01-01T00:00:00.000000Z",
        "findings": (Finding(claim="filter verdict buy: ok", evidence_refs=("tick:0",)),),
        "recommendation": "RESEARCH",
    }
    return AgentReport.model_validate(base | over)


def _record(store: LedgerStore, report: AgentReport) -> str:
    return record_signal(
        store, report, seat=SIGNAL_SEAT, source="replay", ts_utc=report.ts_utc, correlation_id="t"
    )


def test_report_cannot_recommend_approval_or_skip_findings() -> None:
    with pytest.raises(ValidationError):
        _report(recommendation="APPROVED_FOR_PAPER_SIMULATION")
    with pytest.raises(ValidationError):
        _report(findings=())
    with pytest.raises(ValidationError):
        Finding(claim="x", confidence="1.5")  # type: ignore[arg-type]


def test_signal_is_idempotent_and_resolvable(tmp_path: Path) -> None:
    store = LedgerStore(str(tmp_path / "ledger.sqlite"))
    report = _report()
    first = _record(store, report)
    assert first == _record(store, report) == signal_id_for(report)
    assert len(list(store.read(limit=None))) == 1
    assert is_signal(store, first)
    assert not is_signal(store, "0" * 32)
    assert not is_signal(store, "not-an-id")
    assert signal_id_for(_report(agent_version="other")) != first


async def test_order_citing_unknown_signal_is_refused_before_risk(tmp_path: Path) -> None:
    adapter, clock, request = _harness(tmp_path, [_snap(S0, T0), _snap(S0, T0)])
    with pytest.raises(OrderRejectedError) as caught:
        await _place(adapter, clock, request.model_copy(update={"signal_id": "f" * 32}))
    assert (caught.value.reason, caught.value.stage) == ("signal_unknown", "ACCEPTED")
    kinds = [e["type"] for e in adapter._store.read(limit=None)]
    assert kinds == ["OrderRejected"]


async def test_fill_carries_the_cited_signal(tmp_path: Path) -> None:
    adapter, clock, request = _harness(tmp_path, [_snap(S0, T0), _snap(S0, T0)])
    signal_id = _record(adapter._store, _report())
    fill = await _place(adapter, clock, request.model_copy(update={"signal_id": signal_id}))
    assert fill.status.value == "filled"
    fills = [e for e in adapter._store.read(limit=None) if e["type"] == "FillRecorded"]
    assert [f["payload"]["signal_id"] for f in fills] == [signal_id]
