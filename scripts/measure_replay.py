"""Measure the desk's own entry gate and exit rules over a recorded dataset.

Uses the repo's admission gate (`engine.pipeline.decide_token`) and exit rules
(`engine.exits.apply_price`), so the numbers come from the desk's real predicates, not a
re-implementation. The seat runtime is bypassed on purpose: `run --once --replay` aborts on
dense live recordings because a seat cycle has a 30 s virtual budget.

Usage:
    uv run python scripts/measure_replay.py --fixture tests/fixtures/replay/live-01
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

from pumpgrok.config.settings import load_settings
from pumpgrok.engine.exits import apply_price
from pumpgrok.engine.models import PRICE_SCALE, ExitPosition
from pumpgrok.engine.pipeline import CurveRiseTracker, decide_token, tick_to_candidate
from pumpgrok.replay.clock import parse_ts_utc
from pumpgrok.replay.fixtures import load_dataset

VENUE_FEE_BPS_EACH_SIDE = 100  # matches the desk fee model quoted by `pumpgrok backtest`


def price_lamports(event: dict) -> int:
    """Scaled lamports-per-token from the curve reserves (S/T)."""
    sol = int(event.get("S") or 0)
    tokens = int(event.get("T") or 0)
    return sol * PRICE_SCALE // tokens if tokens > 0 else 0


def measure(fixture: Path) -> dict:
    settings = load_settings()
    dataset = load_dataset(fixture)
    events = sorted(dataset.events, key=lambda e: e["ts_utc"])
    # The momentum gate reads history the fixture already holds: replay the same
    # CurveRiseTracker the live pipeline uses, so a gate-on run measures the real gate.
    rises: dict[tuple[str, float], float | None] = {}
    if settings.filter.min_curve_rise_60s > 0:
        tracker = CurveRiseTracker()
        for event in events:
            token = tick_to_candidate(event)
            ts = parse_ts_utc(str(event["ts_utc"]))
            rises[(token.mint, round(ts, 6))] = tracker.observe(
                token.mint, ts, token.sol_in_curve
            )

    def candidate(event: dict):
        token = tick_to_candidate(event)
        if rises:
            token.curve_rise_60s = rises[(token.mint, round(parse_ts_utc(str(event["ts_utc"])), 6))]
        return token

    open_pos: dict[str, ExitPosition] = {}
    closed: list[dict] = []
    traded: set[str] = set()  # one round trip per mint: no re-entry churn after an exit
    buys = 0
    for event in events:
        mint = str(event.get("mint", ""))
        ts = parse_ts_utc(str(event["ts_utc"]))
        price = price_lamports(event)
        pos = open_pos.get(mint)
        if pos is not None and price > 0:
            signal = apply_price(pos, price, settings.exits, ts)
            if signal is not None:
                gross = (price - pos.entry_price) / pos.entry_price * 100.0
                closed.append(
                    {
                        "mint": mint,
                        "rule": signal.reason,
                        "gross_pct": round(gross, 2),
                        "net_pct": round(gross - 2 * VENUE_FEE_BPS_EACH_SIDE / 100.0, 2),
                        "held_s": round(ts - pos.opened_at, 1),
                    }
                )
                del open_pos[mint]
            continue
        if pos is not None or mint in traded or len(open_pos) >= settings.risk.max_open_positions:
            continue
        verdict = decide_token(candidate(event), settings.filter, ts)
        if verdict.decision != "buy" or price <= 0:
            continue
        if buys >= settings.risk.max_trades_per_day:
            continue
        buys += 1
        traded.add(mint)
        open_pos[mint] = ExitPosition(
            mint=mint,
            entry_price=price,
            peak_price=price,
            sol_spent=0,
            token_amount=0,
            opened_at=ts,
        )
    # Signal study: every gated tick is one hypothetical entry; score the price move that
    # follows. Immune to the 3-position cap, so it answers "how often is the gate right".
    horizon_s = 300.0
    by_mint: dict[str, list[tuple[float, int]]] = {}
    for event in events:
        by_mint.setdefault(str(event.get("mint", "")), []).append(
            (parse_ts_utc(str(event["ts_utc"])), price_lamports(event))
        )
    gated = {
        (str(e.get("mint", "")), round(parse_ts_utc(str(e["ts_utc"])), 6))
        for e in events
        if decide_token(candidate(e), settings.filter, parse_ts_utc(str(e["ts_utc"]))).decision
        == "buy"
    }
    moves: list[float] = []
    sim_outcomes: list[float] = []
    sim_open = 0
    for mint, series in by_mint.items():
        for ts, price in series:
            if price <= 0 or (mint, round(ts, 6)) not in gated:
                continue
            later = [p for t, p in series if t >= ts + horizon_s and p > 0]
            if later:
                moves.append((later[0] - price) / price * 100.0)
            # Simulate each signal independently through the desk's own exit rules.
            pos = ExitPosition(mint=mint, entry_price=price, peak_price=price, opened_at=ts)
            closed_at = None
            for t, p in series:
                if t <= ts or p <= 0:
                    continue
                signal = apply_price(pos, p, settings.exits, t)
                if signal is not None:
                    closed_at = (p - price) / price * 100.0
                    break
            if closed_at is None:
                sim_open += 1
            else:
                sim_outcomes.append(closed_at - 2 * VENUE_FEE_BPS_EACH_SIDE / 100.0)
    nets = [t["net_pct"] for t in closed]
    wins = [n for n in nets if n > 0]
    return {
        "dataset": dataset.manifest.dataset,
        "ticks": len(events),
        "mints": len({str(e.get("mint")) for e in events}),
        "entries": buys,
        "closed": len(closed),
        "still_open": len(open_pos),
        "wins": len(wins),
        "win_rate_pct": round(100.0 * len(wins) / len(closed), 1) if closed else None,
        "avg_net_pct": round(statistics.fmean(nets), 2) if nets else None,
        "median_net_pct": round(statistics.median(nets), 2) if nets else None,
        "best_pct": max(nets) if nets else None,
        "worst_pct": min(nets) if nets else None,
        "by_rule": dict(Counter(t["rule"] for t in closed)),
        "signal_horizon_s": int(horizon_s),
        "signals": len(moves),
        "signal_up_pct": round(100.0 * len([m for m in moves if m > 0]) / len(moves), 1)
        if moves
        else None,
        "signal_median_pct": round(statistics.median(moves), 2) if moves else None,
        "signal_p90_pct": round(sorted(moves)[int(len(moves) * 0.9) - 1], 2) if moves else None,
        "signal_over_30pct": round(100.0 * len([m for m in moves if m >= 30]) / len(moves), 1)
        if moves
        else None,
        "sim_trades": len(sim_outcomes),
        "sim_wins": len([s for s in sim_outcomes if s > 0]),
        "sim_win_rate_pct": round(
            100.0 * len([s for s in sim_outcomes if s > 0]) / len(sim_outcomes), 1
        )
        if sim_outcomes
        else None,
        "sim_avg_net_pct": round(statistics.fmean(sim_outcomes), 2) if sim_outcomes else None,
        "sim_median_net_pct": round(statistics.median(sim_outcomes), 2)
        if sim_outcomes
        else None,
        "sim_unclosed": sim_open,
        "open_positions": [
            {
                "mint": p.mint[:10],
                "entry": p.entry_price,
                "last": by_mint[p.mint][-1][1] if p.mint in by_mint else 0,
                "net_pct": round(
                    (by_mint[p.mint][-1][1] - p.entry_price) / p.entry_price * 100.0
                    - 2 * VENUE_FEE_BPS_EACH_SIDE / 100.0,
                    2,
                )
                if p.mint in by_mint and p.entry_price
                else None,
            }
            for p in open_pos.values()
        ],
        "trades": closed,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", required=True, type=Path)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    result = measure(args.fixture)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        head = {k: v for k, v in result.items() if k != "trades"}
        print(json.dumps(head, indent=2))
        for t in result["trades"]:
            print(f"  {t['mint'][:10]} rule={t['rule']:<13} net={t['net_pct']:+.1f}% held={t['held_s']:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
