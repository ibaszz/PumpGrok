"""P1-4: every simulated fill records the execution model it was simulated under."""

from __future__ import annotations

import json
from decimal import Decimal

from pumpgrok.execution.assumptions import ASSUMPTIONS_VERSION, execution_assumptions_json
from pumpgrok.execution.fees import FeeModel, FeeModelV2
from pumpgrok.execution.latency import P_DROP
from pumpgrok.ledger.events import FillRecorded, OrderRejected


def test_record_is_canonical_and_complete() -> None:
    text = execution_assumptions_json(
        fees=FeeModelV2(), p_drop=P_DROP, partial_fill_fraction=Decimal("0.02")
    )
    again = execution_assumptions_json(
        fees=FeeModelV2(), p_drop=P_DROP, partial_fill_fraction=Decimal("0.02")
    )
    assert text == again
    body = json.loads(text)
    assert body["version"] == ASSUMPTIONS_VERSION
    assert body["mode"] == "paper_simulation"
    assert body["fee_model"]["class"] == "FeeModelV2"
    assert body["fee_model"]["venue_fee_bps"] == 100
    assert body["fee_model"]["ata_rent_lamports"] > 0
    assert body["p_drop"] == "0.02"
    assert body["latency"]["min_s"] == "0.4"
    assert body["curve_layout_status"] in ("ASSUMED", "CONFIRMED")


def test_model_change_changes_record() -> None:
    base = execution_assumptions_json(
        fees=FeeModel(), p_drop=P_DROP, partial_fill_fraction=Decimal("0.02")
    )
    other = execution_assumptions_json(
        fees=FeeModel(venue_fee_bps=125), p_drop=P_DROP, partial_fill_fraction=Decimal("0.02")
    )
    assert base != other


def test_rows_written_before_p1_4_still_validate() -> None:
    old_fill = {
        "source": "paper",
        "order_id": "o",
        "decision_id": "d",
        "side": "buy",
        "mint": "m",
        "filled_lamports": 1,
        "filled_units": 1,
        "filled_fraction": "1",
        "price_avg": "1",
    }
    assert FillRecorded.model_validate(old_fill).assumptions_json == ""
    old_reject = {
        "source": "paper",
        "order_id": "o",
        "decision_id": "d",
        "reason": "r",
        "detail": "x",
        "stage": "ACCEPTED",
    }
    assert OrderRejected.model_validate(old_reject).assumptions_json == ""
