# Architecture (runtime view)

The root [`ARCHITECTURE.md`](../ARCHITECTURE.md) describes the instruction pack (agents,
skills, rules, plugin manifests). This file describes the Python runtime in
`src/pumpgrok/` as it exists at `7dc5db3`, then the proposed target. Evidence for every
box is in [REPOSITORY_AUDIT.md](REPOSITORY_AUDIT.md).

## 1. Current architecture

```mermaid
flowchart TB
  subgraph Ext[External, untrusted]
    RPC[Solana JSON-RPC]
    PP[PumpPortal WS]
    PF[pump.fun REST]
    JUP[Jupiter /order quote]
    XAI[x.ai Grok]
    TG[Telegram]
  end

  subgraph Acq[Acquisition]
    RPCC[tools/onchain/rpc.py<br/>failover + retry]
    DF[datafeed/<br/>portal, price, marks, watchset]
    REC[replay/recorder<br/>live_recorder]
  end

  subgraph Intel[Screening and LLM]
    ENG[engine/<br/>monitor filter, analyzer, scoring, reputation]
    GA[engine/agents/<br/>auditor, timing, narrative, checker]
    GROK[grok/<br/>budget, cache, coalesce, fallback, sanitize]
  end

  subgraph Desk[Supervised desk]
    SUP[supervisor/scheduler/supervisor.py<br/>10 seats, TICK_S=1.0]
    BUS[events/bus.py<br/>SQLite queue, dedup, DLQ]
    STR[strategies/<br/>4 registered ids]
  end

  subgraph Gate[Deterministic control]
    RISK[risk/engine/<br/>25-step walk]
    BRK[risk/breakers.py]
    KILL[risk/killswitch.py]
    LG[execution/live/live_gate.py<br/>refuses]
  end

  subgraph Exec[Execution]
    PA[execution/paper_adapter/<br/>curve fills, fees, latency, drops]
    LA[execution/live/live_adapter.py<br/>unreachable]
  end

  subgraph Store[Persistence]
    LED[(ledger.sqlite<br/>append-only, hash chain)]
    EVS[(events store)]
    BT[(var/backtest/*)]
  end

  subgraph Out[Operator surfaces]
    CLI[pumpgrok CLI]
    HS[health_server<br/>/livez /readyz /metrics]
    GW[desk-gateway<br/>MCP per-seat tokens]
    REP[reporting/ daily, weekly]
    NOT[tools/notify/<br/>telegram, webhook]
  end

  RPC --> RPCC --> DF
  PP --> DF
  PF --> ENG
  DF --> ENG
  ENG --> GA --> GROK --> XAI
  DF --> REC
  SUP --> BUS
  SUP --> STR --> RISK
  ENG --> SUP
  RISK --> BRK
  RISK --> KILL
  RISK -->|approved| PA
  RISK -.->|live mode| LG -.-> LA
  PA --> LED
  JUP -.->|informational| CLI
  BUS --> EVS
  LED --> REP
  LED --> HS
  GW --> LED
  NOT --> TG
  CLI --> SUP
```

Notes on the diagram:

- `run --mode paper --once --replay <dir>` and `run --serve --replay <dir>` drive the
  supervisor on a `FixtureClock`. `run --live-paper` composes the live datafeed into a
  paper desk (`supervisor/live_paper.py`) but currently places nothing (`_no_place`).
- The live adapter exists but every path to it passes `LiveGate`, and `run --mode live`
  exits 3.
- `events/sources.py` (backoff, staleness, cursors) is not wired into the supervisor.
- `services/desk-gateway/src/desk_gateway/console.py` is not routed; there is no UI.

### Layer rules already enforced

| Rule | Enforced by |
|------|-------------|
| Only risk-approved orders reach a fill path | `risk/engine/core.py`; `supervisor/intent_router.py` is the only placement path in live-paper |
| Model output is data | `grok/sanitize.py` `parse_strict_json`; pydantic parsing in `engine/agents/base.py` |
| Time is injected | `replay/clock.py` `Clock`; no wall-clock defaults |
| Money is integer lamports, ratios `Decimal` | `core/units.py`, `core/risk_budget.py` |
| Each file has one owning seat | `ownership.yaml`, `ci/gates/check_ownership.py` |
| Ledger cannot be edited | SQLite triggers in `ledger/store.py` |

## 2. Proposed architecture

The target keeps every box above. It adds four components and wires one existing one.
Nothing new may bypass the deterministic gate.

```mermaid
flowchart TB
  subgraph Prov[Provider layer: NEW abstraction over existing clients]
    PR[providers/<br/>Provider protocol: fetch, health, rate limit,<br/>breaker, cache, provenance envelope]
    P1[rpc adapter<br/>existing rpc.py]
    P2[pumpportal adapter<br/>existing datafeed/portal.py]
    P3[pump.fun adapter<br/>existing analyzer/client]
    P4[DexScreener / GeckoTerminal<br/>P4, optional, post-graduation only]
  end

  subgraph Research[Research layer]
    DISC[Discovery<br/>engine/ filters, emits CANDIDATE not BUY]
    WI[Wallet intelligence: NEW<br/>wallet history, PnL, quality score + confidence]
    LLM[LLM gateway: NEW abstraction<br/>Grok, Ollama, Anthropic, OpenAI-compatible]
    AG[Seat and engine agents<br/>typed reports with evidence + limitations]
  end

  subgraph Control[Deterministic control: UNCHANGED authority]
    RISK[RiskEngine + breakers + killswitch]
    VER[Research verdict mapping<br/>REJECT / WATCH / RESEARCH /<br/>APPROVED_FOR_PAPER_SIMULATION]
  end

  subgraph Sim[Simulation]
    PA[Paper adapter]
    LAB[Strategy lab<br/>backtest, holdout, walk-forward, trials,<br/>+ bootstrap CIs: NEW]
    PROM[Promotion stages<br/>human approval only]
  end

  subgraph Surf[Operator surfaces]
    DASH[Research dashboard: NEW<br/>read-only, served by desk-gateway, token-gated]
    NOT[Notifications<br/>dedup + cooldown, SIMULATED label]
  end

  LED[(ledger.sqlite)]

  P1 & P2 & P3 & P4 --> PR --> DISC
  PR --> WI
  DISC --> AG
  WI --> AG
  AG --> LLM
  AG -->|evidence only| VER
  RISK --> VER
  VER -->|APPROVED_FOR_PAPER_SIMULATION| PA
  PA --> LED
  LED --> LAB --> PROM
  LED --> DASH
  LED --> NOT
```

### Design constraints for the target

1. Agents produce evidence. `VER` is computed from `RiskEngine` output plus deterministic
   thresholds; an agent verdict can lower a verdict, never raise one past risk.
2. The provider envelope records source, observed time, retrieved time, subject id,
   quality, confidence, and one of `realtime | delayed | inferred | unavailable`.
   Missing data stays `unavailable`; it is never filled.
3. The dashboard is read-only. Any future control (kill switch, breaker reset) goes
   through the existing CLI verbs and their refusal rules, behind a confirmation.
4. The LLM gateway preserves the existing budget, cache, coalesce, and fallback modules;
   it adds a provider field, not a second budget system.
5. SQLite stays. No new database technology is justified by any measurement so far.

See [ROADMAP.md](ROADMAP.md) for sequencing and [DECISIONS.md](DECISIONS.md) for the
recorded choices.
