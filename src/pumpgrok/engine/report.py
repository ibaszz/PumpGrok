"""Agent reports and persisted signals (ROADMAP P2-3, P1-4b; DECISIONS D-013).

An :class:`AgentReport` is the common envelope for what an agent concluded about one
subject. A *signal* is an agent report stored in the ledger as a ``JournalNote`` with
``note_kind = signal`` and the report's canonical JSON as ``text``. The note's
idempotency key is the ``signal_id``: an order may cite it, the paper adapter checks it
resolves to a signal note, and the resulting fill carries it.

``recommendation`` has no approval value; only ``risk/verdict.py`` maps to
``APPROVED_FOR_PAPER_SIMULATION``. A report is evidence, never clearance.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pumpgrok.core.idempotency import canonical_json
from pumpgrok.ledger.store import LedgerStore
from pumpgrok.risk.verdict import AgentRecommendation

__all__ = [
    "SIGNAL_ID_RE",
    "SIGNAL_SEAT",
    "AgentReport",
    "Finding",
    "is_signal",
    "record_signal",
    "signal_id_for",
]

#: The seat that owns signals (ownership.yaml); replay writes engine verdicts as it.
SIGNAL_SEAT = "bot-08-signal"

#: A signal id is the 32-hex idempotency key of its JournalNote.
SIGNAL_ID_RE = r"^[0-9a-f]{32}$"
_SIGNAL_ID = re.compile(SIGNAL_ID_RE)

_Text = Field(min_length=1, max_length=500)


class Finding(BaseModel):
    """One claim with the evidence it rests on; ``confidence`` only when measured."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    claim: str = _Text
    evidence_refs: tuple[str, ...] = ()
    confidence: Decimal | None = Field(default=None, ge=0, le=1)


class AgentReport(BaseModel):
    """Standard agent output (docs/AGENT_CATALOG.md section 4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent: str = Field(min_length=1, max_length=64)
    agent_version: str = Field(min_length=1, max_length=128)
    subject: str = Field(min_length=1, max_length=64)
    ts_utc: str = Field(min_length=1)
    findings: tuple[Finding, ...] = Field(min_length=1)
    unknowns: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    contradictions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    recommendation: AgentRecommendation


def _text_of(report: AgentReport) -> str:
    return canonical_json(report.model_dump(mode="json"))


def signal_id_for(report: AgentReport) -> str:
    """Content-derived id: the same report always gets the same id."""
    return hashlib.sha256(f"signal|{_text_of(report)}".encode()).hexdigest()[:32]


def is_signal(store: LedgerStore, signal_id: str) -> bool:
    """True only when ``signal_id`` names a stored ``signal`` JournalNote."""
    if not _SIGNAL_ID.fullmatch(signal_id):
        return False
    row = store.by_key(signal_id)
    return (
        row is not None
        and row["type"] == "JournalNote"
        and row["payload"].get("note_kind") == "signal"
    )


def record_signal(
    store: LedgerStore,
    report: AgentReport,
    *,
    seat: str,
    source: str,
    ts_utc: str,
    correlation_id: str,
) -> str:
    """Append ``report`` as a signal note (idempotent); return its ``signal_id``."""
    signal_id = signal_id_for(report)
    payload: dict[str, Any] = {
        "source": source,
        "seat": seat,
        "note_kind": "signal",
        "text": _text_of(report),
        "refs": [report.subject],
    }
    try:
        store.append(
            ts_utc=ts_utc,
            source=source,
            correlation_id=correlation_id,
            idempotency_key=signal_id,
            type="JournalNote",
            payload=payload,
        )
    except sqlite3.IntegrityError:
        if not is_signal(store, signal_id):
            raise
    return signal_id
