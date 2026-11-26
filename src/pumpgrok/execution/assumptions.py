"""Execution assumptions recorded on every simulated fill (ROADMAP P1-4).

A paper fill is only as good as the model that produced it. Each ``FillRecorded`` row,
and each ``OrderRejected`` row decided at CONFIRMING (slippage, dropped transaction),
carries ``assumptions_json``: the fee model, latency distribution, drop probability,
partial-fill cap, price basis, and curve-layout status in force when it was simulated.
A reviewer can then tell which fills came from which model, and a recalibration of any
REVIEW constant shows up in the ledger instead of silently changing history's meaning.

The value is canonical JSON (sorted keys, no floats), so identical models give identical
strings and replays stay byte-identical.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal

from pumpgrok.core.idempotency import canonical_json
from pumpgrok.execution.fees import FeeModel
from pumpgrok.execution.latency import LATENCY_MAX_S, LATENCY_MIN_S, LATENCY_MU, LATENCY_SIGMA
from pumpgrok.tools.onchain.curve_layout import LAYOUT_STATUS

__all__ = ["ASSUMPTIONS_VERSION", "execution_assumptions_json"]

#: Bump when the shape of the record changes.
ASSUMPTIONS_VERSION = "1"


def _dec(value: float) -> str:
    """Floats are refused by canonical_json; record them as exact decimal strings."""
    return str(Decimal(repr(value)))


def execution_assumptions_json(
    *, fees: FeeModel, p_drop: float, partial_fill_fraction: Decimal
) -> str:
    """Canonical JSON describing the simulation model for one adapter instance."""
    return canonical_json(
        {
            "version": ASSUMPTIONS_VERSION,
            "mode": "paper_simulation",
            "price_basis": "bonding_curve_reserves",
            "curve_layout_status": LAYOUT_STATUS,
            "fee_model": {
                "class": type(fees).__name__,
                **{f.name: getattr(fees, f.name) for f in dataclasses.fields(fees)},
            },
            "latency": {
                "distribution": "lognormal_clipped",
                "mu": _dec(LATENCY_MU),
                "sigma": _dec(LATENCY_SIGMA),
                "min_s": _dec(LATENCY_MIN_S),
                "max_s": _dec(LATENCY_MAX_S),
            },
            "p_drop": _dec(p_drop),
            "partial_fill_fraction": str(partial_fill_fraction),
            "not_modeled": ["mev_sandwich", "same_slot_competition", "post_graduation_amm"],
        }
    )
