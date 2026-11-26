"""P1-3: deterministic research verdicts; agents can only lower them."""

from __future__ import annotations

import pytest

from pumpgrok.risk.engine.constants import STEP_IDS
from pumpgrok.risk.verdict import REASON_CATEGORY, ResearchVerdict, research_verdict
from pumpgrok.tools.risk.check_limits import R34_RULE_IDS

V = ResearchVerdict


def test_every_engine_step_has_a_category() -> None:
    missing = [step for step in (*STEP_IDS, *R34_RULE_IDS) if step not in REASON_CATEGORY]
    assert missing == []


def test_complete_pass_is_approved_for_paper() -> None:
    out = research_verdict(passed=True, reason=None, complete=True)
    assert out.verdict is V.APPROVED_FOR_PAPER_SIMULATION
    assert out.category == "pass"


def test_incomplete_pass_caps_at_research() -> None:
    out = research_verdict(passed=True, reason=None, complete=False)
    assert out.verdict is V.RESEARCH
    assert out.reason == "pass_incomplete"


@pytest.mark.parametrize(
    ("reason", "verdict", "category"),
    [
        ("data_stale", V.WATCH, "data"),
        ("breaker_open", V.WATCH, "data"),
        ("max_open_positions", V.WATCH, "desk_state"),
        ("kill_switch", V.WATCH, "desk_state"),
        ("approval_missing", V.REJECT, "governance"),
        ("live_gate", V.REJECT, "governance"),
        ("below_liquidity_floor", V.REJECT, "candidate"),
        ("creator_already_open", V.REJECT, "candidate"),
    ],
)
def test_fail_reasons_map_by_category(reason: str, verdict: V, category: str) -> None:
    out = research_verdict(passed=False, reason=reason, complete=True)
    assert (out.verdict, out.category, out.reason) == (verdict, category, reason)


def test_unknown_or_missing_reason_fails_closed() -> None:
    assert (
        research_verdict(passed=False, reason="brand_new_step", complete=True).verdict is V.REJECT
    )
    assert research_verdict(passed=False, reason=None, complete=True).verdict is V.REJECT


def test_agents_lower_never_raise() -> None:
    approved = research_verdict(
        passed=True, reason=None, complete=True, agent_recommendations=["RESEARCH", "WATCH"]
    )
    assert approved.verdict is V.WATCH
    assert approved.lowered_by_agents is True
    rejected = research_verdict(
        passed=False, reason="approval_missing", complete=True, agent_recommendations=["RESEARCH"]
    )
    assert rejected.verdict is V.REJECT
    assert rejected.lowered_by_agents is False


@pytest.mark.parametrize("bad", ["APPROVED_FOR_PAPER_SIMULATION", "BUY", "approve"])
def test_agents_cannot_recommend_approval_or_free_text(bad: str) -> None:
    with pytest.raises(ValueError, match=r"approval|not a valid"):
        research_verdict(
            passed=True,
            reason=None,
            complete=True,
            agent_recommendations=[bad],  # type: ignore[list-item]
        )
