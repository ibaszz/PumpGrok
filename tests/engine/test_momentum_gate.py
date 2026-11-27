"""Momentum gate: trailing curve rise over 60 s, and its filter verdicts."""

from __future__ import annotations

from pumpgrok.config.sections.engine import FilterSettings
from pumpgrok.engine.models import Token
from pumpgrok.engine.monitor import passes_filter
from pumpgrok.engine.pipeline import CurveRiseTracker


def _token(rise: float | None, age: float = 200.0) -> Token:
    return Token(
        mint="m",
        name="n",
        image_uri="i",
        unique_buyers=9,
        curve_progress=0.1,
        sol_in_curve=8_500_000_000,
        created_timestamp=1_000.0,
        curve_rise_60s=rise,
    )


def test_tracker_needs_a_tick_at_the_window_edge() -> None:
    t = CurveRiseTracker()
    assert t.observe("m", 0.0, 100) is None
    assert t.observe("m", 30.0, 105) is None
    rise_60 = t.observe("m", 60.0, 110)
    assert rise_60 is not None and round(rise_60, 6) == 0.1  # 100 -> 110
    # at t=90 the baseline is the tick at t=30 (105), not t=0: 105 -> 121
    rise_90 = t.observe("m", 90.0, 121)
    assert rise_90 is not None and round(rise_90, 6) == 0.152381


def test_tracker_ignores_unknown_reserves() -> None:
    t = CurveRiseTracker()
    assert t.observe("m", 0.0, 0) is None
    assert t.observe("m", 60.0, 100) is None


def test_gate_off_admits_without_history() -> None:
    assert passes_filter(_token(None), FilterSettings(), 1_200.0) == (True, "ok")


def test_gate_fails_closed_without_history() -> None:
    cfg = FilterSettings(min_curve_rise_60s=0.02)
    assert passes_filter(_token(None), cfg, 1_200.0) == (False, "no_momentum_data")


def test_gate_splits_falling_from_rising() -> None:
    cfg = FilterSettings(min_curve_rise_60s=0.02)
    assert passes_filter(_token(-0.05), cfg, 1_200.0) == (False, "curve_not_rising")
    assert passes_filter(_token(0.02), cfg, 1_200.0) == (True, "ok")
