"""Deterministic research verdicts on top of risk decisions (ROADMAP P1-3, DECISIONS D-002).

Maps a risk outcome to one of four research verdicts, ordered from most to least
permissive::

    APPROVED_FOR_PAPER_SIMULATION > RESEARCH > WATCH > REJECT

Rules:

- Only a risk pass with every rule actually evaluated can be
  ``APPROVED_FOR_PAPER_SIMULATION``. A pass with skipped rules (the structural
  ``risk.check_limits`` view) caps at ``RESEARCH``: nothing was proven about the
  skipped rules.
- A failing rule maps through :data:`REASON_CATEGORY`. Data uncertainty and desk
  capacity become ``WATCH`` (the candidate may be fine, the desk cannot act now);
  governance and candidate-quality failures become ``REJECT``. An unknown reason is
  ``REJECT`` (fail closed).
- Agent recommendations may only lower the verdict. They are limited to
  ``REJECT | WATCH | RESEARCH``; an agent cannot express approval.

The verdict is evidence for operators and seats. It authorises nothing: orders still
pass ``RiskEngine.check`` inside the paper adapter.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

__all__ = [
    "REASON_CATEGORY",
    "AgentRecommendation",
    "ResearchVerdict",
    "VerdictResult",
    "research_verdict",
]


class ResearchVerdict(StrEnum):
    REJECT = "REJECT"
    WATCH = "WATCH"
    RESEARCH = "RESEARCH"
    APPROVED_FOR_PAPER_SIMULATION = "APPROVED_FOR_PAPER_SIMULATION"


#: Agent recommendations; deliberately no approval value.
AgentRecommendation = Literal["REJECT", "WATCH", "RESEARCH"]

Category = Literal["pass", "data", "desk_state", "governance", "candidate", "unknown"]

_RANK: dict[ResearchVerdict, int] = {
    ResearchVerdict.REJECT: 0,
    ResearchVerdict.WATCH: 1,
    ResearchVerdict.RESEARCH: 2,
    ResearchVerdict.APPROVED_FOR_PAPER_SIMULATION: 3,
}

#: Every risk reason the engine can emit, by category. Tests assert full coverage of
#: ``risk.engine.constants.STEP_IDS`` so a new step cannot silently fall to ``unknown``.
REASON_CATEGORY: dict[str, Category] = {
    # Data uncertainty: wait for fresh data.
    "data_stale": "data",
    "breaker_open": "data",
    "reputation_degraded": "data",
    # Desk capacity or state: the candidate is not judged.
    "halted": "desk_state",
    "kill_switch": "desk_state",
    "max_trades_per_day": "desk_state",
    "max_open_positions": "desk_state",
    "already_open": "desk_state",
    "max_trade_fraction": "desk_state",
    "max_token_fraction": "desk_state",
    "daily_loss_halt": "desk_state",
    "drawdown_halt": "desk_state",
    "no_position": "desk_state",
    # Governance: the order itself is not allowed.
    "ledger_inconsistent": "governance",
    "live_gate": "governance",
    "approval_missing": "governance",
    "approval_mismatch": "governance",
    "seat_may_trade": "governance",
    # Candidate quality.
    "creator_already_open": "candidate",
    "size_too_small": "candidate",
    "below_liquidity_floor": "candidate",
}

_CATEGORY_VERDICT: dict[Category, ResearchVerdict] = {
    "data": ResearchVerdict.WATCH,
    "desk_state": ResearchVerdict.WATCH,
    "governance": ResearchVerdict.REJECT,
    "candidate": ResearchVerdict.REJECT,
    "unknown": ResearchVerdict.REJECT,
}


class VerdictResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: ResearchVerdict
    category: Category
    reason: str
    lowered_by_agents: bool = False


def research_verdict(
    *,
    passed: bool,
    reason: str | None,
    complete: bool,
    agent_recommendations: Iterable[AgentRecommendation] = (),
) -> VerdictResult:
    """Map one risk outcome (plus optional agent recommendations) to a verdict.

    ``complete`` is True only when every risk rule was evaluated (none skipped).
    """
    if passed:
        category: Category = "pass"
        base = (
            ResearchVerdict.APPROVED_FOR_PAPER_SIMULATION if complete else ResearchVerdict.RESEARCH
        )
        why = "pass" if complete else "pass_incomplete"
    else:
        why = reason or "unknown"
        category = REASON_CATEGORY.get(why, "unknown")
        base = _CATEGORY_VERDICT[category]
    final = base
    for rec in agent_recommendations:
        lowered = ResearchVerdict(rec)  # ValueError on anything outside the three values
        if lowered is ResearchVerdict.APPROVED_FOR_PAPER_SIMULATION:
            raise ValueError("agents cannot recommend approval")
        if _RANK[lowered] < _RANK[final]:
            final = lowered
    return VerdictResult(
        verdict=final, category=category, reason=why, lowered_by_agents=final != base
    )
