# Agent catalog

Two agent layers exist. Seat agents are prompt files plus a supervised in-process seat; engine
agents are typed Grok calls inside the screening pipeline. Neither layer can approve an
order: `RiskEngine` (`src/pumpgrok/risk/engine/`) decides, and its veto is final.

Seat prompt sources: `agents/*.md`. Rosters: `contracts/tool-rosters/bot-NN-<seat>.yaml`.
Every seat also gets the eight `_core.yaml` desk tools (`desk.brief`, `desk.docs_search`,
`desk.memory_retain`, `desk.memory_recall`, `desk.ownership_resolve`, `desk.receipt_check`,
`desk.event_emit`, `desk.doctor`). Full role text: [ROSTER.md](ROSTER.md).

## 1. Seat agents

| Seat | Bot id | Purpose | Seat-specific tools | Writes |
|------|--------|---------|---------------------|--------|
| CHIEF | bot-00-chief | Orchestrate, route briefs to tickets, consolidate, desk status; never trades | exec.positions, portfolio.exposure, portfolio.pnl, risk.check_limits, risk.halt_status, notify.alert, journal.append | journal |
| SCOUT | bot-01-scout | Discovery; LEAD block with mint, source, UTC | market.price, market.liquidity, market.trades, market.holders_snapshot, onchain.token_metadata, onchain.authorities, journal.append | journal |
| RISK | bot-02-risk | Explain and run audits; CLEAR / CONDITIONAL / KILL; KILL is final | portfolio.ledger_read, portfolio.exposure, portfolio.pnl, risk.check_limits, risk.drawdown, risk.halt_status, risk.kill_switch | kill switch |
| WHALE | bot-03-whale | Holder and flow context after RISK | onchain.top_holders, onchain.deployer_history, onchain.large_transfers, market.trades, market.holders_snapshot, journal.append | journal |
| SNIPER | bot-04-sniper | Only buy sender (paper fills only in this release) | market.price, exec.quote, exec.simulate, exec.place, exec.cancel, exec.positions, onchain.priority_fee | paper orders |
| RUG | bot-05-rug | Post-fill red-flag monitor; alerts, never closes | market.price, market.liquidity, onchain.token_metadata, onchain.authorities, onchain.lp_status, onchain.top_holders, safety.score_report | none |
| EXIT | bot-06-exit | Only sell sender (paper) | same as SNIPER | paper orders |
| SHILL | bot-07-shill | Social velocity and narrative quality | onchain.token_metadata, market.trades, journal.append | journal |
| SIGNAL | bot-08-signal | Momentum scoring; score is evidence, never clearance | market.price, market.ohlcv, market.liquidity, market.trades, market.holders_snapshot, journal.append | journal |
| JOURNAL | bot-09-journal | Ledger reports and reviews; never trades | exec.positions, portfolio.ledger_read, portfolio.exposure, portfolio.pnl, notify.alert, journal.append, journal.render_report | journal, reports |

## 2. Engine agents (`src/pumpgrok/engine/agents/`)

Shared contract from `engine/agents/base.py`:

| Field | Value |
|-------|-------|
| Provider | x.ai, `GrokSettings.base_url` + `/chat/completions` |
| Model | `strong_model` (`grok-4.3`) for `checker`; `fast_model` (`grok-4.20-0309-non-reasoning`) for the rest (`config/sections/core.py`) |
| Temperature | 0 |
| Timeout | 30 s per request (constructor default) |
| Retry | 3 attempts, backoff on the injected clock and seeded RNG; no retry on 4xx except 429; budget checked on every attempt |
| Output | one strict JSON object, parsed into a pydantic `result_model` |
| Failure | typed pessimistic fallback (e.g. `checker` returns `approve: false`) |
| Budget | `grok/budget.py` per-window, per-agent, global; exhaustion fails closed |
| Cache | `timing` verdict cached 15 min; fallbacks never cached |

| Agent | Brief role | Result model | Inputs |
|-------|-----------|--------------|--------|
| `auditor` | WHALE (wallet behavior) | `AuditResult` | raw trades, holder list |
| `timing` | QUANT (market backdrop) | `TimingResult` | market window aggregates |
| `narrative` | NARRATIVE | `NarrativeResult` (four 0..1 scores) | token name, symbol, description, links |
| `checker` | SKEPTIC | `CheckerResult` | every earlier verdict |

## 3. Mapping to the proposed role set

| Proposed | Existing | Gap |
|----------|----------|-----|
| CHIEF | CHIEF seat | Report consolidation is prose in the host; no typed consolidated report |
| SCOUT | SCOUT seat + engine filter | Discovery output is a candidate; a candidate is never a buy (kept) |
| WHALE | WHALE seat + `auditor` | No wallet history model (ROADMAP P2-1) |
| QUANT | SIGNAL seat + `timing` | None structural |
| NARRATIVE | SHILL seat + `narrative` | No social data source |
| SKEPTIC | `checker` | Not a seat; runs only inside the engine pipeline |
| RISK | RISK seat + `RiskEngine` | None; the engine is the authority |
| JOURNAL | JOURNAL seat | Agent disagreements are not a recorded event type |
| STRATEGY_RESEARCHER | none | Backtest and promotion code exist with no seat; adding one needs an `ownership.yaml` entry and a roster with read-only tools |

Decision: no new seats in the first increment (see [DECISIONS.md](DECISIONS.md) D-003).

## 4. Standard report contract (proposed)

Engine agents already return typed models. Seat agents return prose to the host. The
proposed common envelope, to be added as a pydantic model next to `engine/models.py` and
emitted through `journal.append`:

```text
AgentReport
  agent: str              # seat or engine agent name
  agent_version: str      # prompt hash (prompts are code constants)
  subject: str            # mint or wallet
  ts_utc: str             # injected clock
  findings: list[Finding] # claim, evidence refs (ledger seq / tool call id), confidence 0..1
  unknowns: list[str]
  assumptions: list[str]
  contradictions: list[str]
  limitations: list[str]
  recommendation: REJECT | WATCH | RESEARCH   # never APPROVED; only risk maps to approval
```

`recommendation` deliberately has no approval value: only the deterministic mapping in
ROADMAP P1-3 can produce `APPROVED_FOR_PAPER_SIMULATION`.
