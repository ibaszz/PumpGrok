"""Offline check for scripts/record_pumpfun.py: decoded events seal into a loadable dataset."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from pumpgrok.replay.fixtures import load_dataset
from pumpgrok.tools.onchain.pump_events import CompleteEvent, CreateEvent, TradeEvent

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "record_pumpfun.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("record_pumpfun", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["record_pumpfun"] = module
    spec.loader.exec_module(module)
    return module


def test_recorder_seals_loadable_dataset(tmp_path: Path) -> None:
    rec_mod = _load_script()
    out = tmp_path / "unit-live"
    rec = rec_mod.Recorder(out, max_tokens=5)
    out.mkdir()
    created_ms = 1_767_225_600_000
    rec.handle(
        CreateEvent(
            name="Unit",
            symbol="UNIT",
            uri="https://example.invalid/u.json",
            mint="UnitMint111111111111111111111111111111111",
            bonding_curve="Curve",
            user="Creator",
            creator="Creator",
            timestamp=created_ms,
            virtual_token_reserves=1_073_000_000_000_000,
            virtual_sol_reserves=30_000_000_000,
            real_token_reserves=793_100_000_000_000,
            token_total_supply=1_000_000_000_000_000,
        )
    )
    for i, buyer in enumerate(["w1", "w2", "w3"]):
        rec.handle(
            TradeEvent(
                mint="UnitMint111111111111111111111111111111111",
                sol_amount=1_000_000_000,
                token_amount=10_000_000_000,
                is_buy=True,
                user=buyer,
                timestamp=created_ms + (i + 1) * 1000,
                virtual_sol_reserves=30_000_000_000 + (i + 1) * 1_000_000_000,
                virtual_token_reserves=1_073_000_000_000_000 - (i + 1) * 10_000_000_000,
                real_sol_reserves=1,
                real_token_reserves=1,
                fee_recipient="F",
                fee_basis_points=95,
                fee=1,
                creator="Creator",
                creator_fee_basis_points=5,
                creator_fee=1,
                track_volume=False,
                total_unclaimed_tokens=0,
                total_claimed_tokens=0,
                current_sol_volume=0,
                last_update_timestamp=created_ms,
                ix_name="buy",
                mayhem_mode=False,
            )
        )
    rec.handle(
        CompleteEvent(
            user="w1",
            mint="UnitMint111111111111111111111111111111111",
            bonding_curve="Curve",
            timestamp=created_ms + 9000,
        )
    )

    manifest = rec.seal()
    assert manifest["dataset"] == "unit-live"
    dataset = load_dataset(out)
    assert dataset.manifest.sha256 == manifest["sha256"]
    assert len(dataset.events) == 4
    last = json.loads((out / "events.jsonl").read_text().splitlines()[-1])
    assert last["unique_buyers"] == 3
    assert last["S"] > 30_000_000_000
