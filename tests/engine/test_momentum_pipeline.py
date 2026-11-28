"""Momentum gate wired into replay candidate evaluation (CurveRiseTracker per mint)."""

from __future__ import annotations

from pumpgrok.config.sections.engine import FilterSettings
from pumpgrok.engine.pipeline import BUY, evaluate_candidates


def _tick(ts: str, s: int, mint: str = "m") -> dict[str, object]:
    return {
        "mint": mint,
        "ts_utc": ts,
        "S": s,
        "name": "n",
        "image_uri": "i",
        "created_ts_utc": "2026-01-01T00:00:00+00:00",
        "unique_buyers": 9,
    }


TICKS = [
    _tick("2026-01-01T00:03:00+00:00", 8_000_000_000),
    _tick("2026-01-01T00:04:00+00:00", 8_400_000_000),  # +5 % over 60 s
    _tick("2026-01-01T00:04:00+00:00", 9_000_000_000, mint="other"),  # no history
]


def test_rise_is_attached_per_mint_and_gate_reads_it() -> None:
    cfg = FilterSettings(min_curve_rise_60s=0.02)
    first, second, other = evaluate_candidates(TICKS, cfg)
    assert first.token.curve_rise_60s is None
    assert first.verdict.reason == "no_momentum_data"
    assert second.token.curve_rise_60s is not None
    assert round(second.token.curve_rise_60s, 6) == 0.05
    assert second.verdict.decision == BUY
    assert other.verdict.reason == "no_momentum_data"


def test_gate_off_keeps_existing_verdicts() -> None:
    verdicts = evaluate_candidates(TICKS, FilterSettings())
    assert [v.verdict.decision for v in verdicts] == [BUY, BUY, BUY]
