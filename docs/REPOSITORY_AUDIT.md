# Repository audit

Audit date: 2026-11-26. Base commit: `7dc5db3` (branch `main`). Method: read the tree, the
docs, and the code; ran the gates listed under "Gate results". Nothing in this file is a
plan for work already done; where a capability is described it was found in code, and the
file path is given.

Companion documents: [ARCHITECTURE.md](ARCHITECTURE.md) (current and proposed diagrams),
[SECURITY_AUDIT.md](SECURITY_AUDIT.md), [DATA_FLOW.md](DATA_FLOW.md),
[AGENT_CATALOG.md](AGENT_CATALOG.md), [TRADING_RESEARCH.md](TRADING_RESEARCH.md),
[OPERATIONS.md](OPERATIONS.md), [ROADMAP.md](ROADMAP.md), [DECISIONS.md](DECISIONS.md).

## 1. Summary

PumpGrok is much further along than a greenfield "build a research desk" brief assumes.
About 44,000 lines of Python in `src/pumpgrok/` (323 modules) and 423 test modules already
cover most of the brief's paper-trading, risk, replay, backtest, and promotion asks. The
real gaps are narrower:

1. No operator-facing research dashboard. The only UI code
   (`services/desk-gateway/src/desk_gateway/console.py`) renders development-process screens
   (milestone tracker, gate dashboard) and is not routed by the gateway server.
2. No wallet-intelligence module. Holder and transfer reads exist; per-wallet PnL, win rate,
   and quality scoring do not.
3. Data acquisition is RPC + PumpPortal + pump.fun REST only. No DexScreener, GeckoTerminal,
   or RugCheck client exists. Provenance fields (source, retrieval time, confidence) are
   partial: freshness exists (`core/datasource.py`, `risk/feed_health.py`), a uniform
   per-datapoint envelope does not.
4. The event-source machinery in `src/pumpgrok/events/sources.py` is not wired into the
   supervisor (its own docstring says so).
5. The LLM layer is single-provider (x.ai Grok). Budget, cache, coalescing, and fallback
   exist; a provider abstraction (Ollama, Anthropic, OpenAI-compatible) does not.
6. Documentation drift: `README.md`, `ARCHITECTURE.md`, and `CLAUDE.md` carry stale lines
   next to their replacements (see section 9).

The safety posture is strong and should be preserved: live trading is refused in code, the
ledger is append-only and hash-chained, risk is a deterministic step walk, and model output
is parsed as data only.

## 2. Repository structure

| Path | Contents | Size |
|------|----------|------|
| `src/pumpgrok/` | Product package | 323 modules, ~44k lines |
| `services/desk-gateway/` | MCP JSON-RPC gateway over the paper tool registry; per-seat bearer tokens | 5 modules, 1,440 lines |
| `agents/` | Ten seat prompts plus `README.md` | 11 files |
| `skills/` | 35 skill procedures (`skills/<name>/SKILL.md`) | 35 dirs |
| `rules/` | Always-applied desk constitution `rules/pumpgrok-team.mdc` | |
| `contracts/` | 18 event JSON schemas, 10 per-seat tool rosters (+ `_core.yaml`) | |
| `tests/` | Unit, property, integration, e2e, soak, replay fixtures | 423 modules |
| `ci/` | Gate scripts (`ci/gates/`), security config (bandit, semgrep, gitleaks), CI meta-tests | |
| `docs/` | Design, runbooks, policy, observability alert rules | ~7k lines before this audit |
| `.planning/` | GSD milestone state; v4 complete, phase 12 PRs pending (`.planning/STATE.md`) | |
| `.github/workflows/` | `ci.yml`, `gates.yml`, `security-pr.yml` | |
| `hermes/`, `prompts/`, `prompts-assembled/` | Host cron bootstrap and assembled seat prompts | |

Git: single visible commit on `main` (`7dc5db3`, merge of PR #135); history is squashed in
this clone. The working tree holds uncommitted work (section 8).

## 3. Technical stack

| Concern | Choice | Evidence |
|---------|--------|----------|
| Language | Python 3.12 | `pyproject.toml` `requires-python` |
| Packaging | `uv`, frozen lockfile | `uv.lock`, `Makefile` `sync` |
| Models/validation | pydantic v2, frozen + `extra="forbid"` configs | `config/sections/*.py`, `core/risk_budget.py` |
| HTTP | httpx (async), tenacity retries | `tools/onchain/rpc.py` |
| Storage | SQLite (WAL, `synchronous=FULL`), append-only triggers, hash chain | `ledger/store.py` |
| Event bus | SQLite-backed pending/applied/dedup/DLQ/cursors | `events/store.py`, `events/bus.py` |
| LLM | x.ai Grok via `/chat/completions` | `engine/agents/base.py`, `config/sections/core.py` `GrokSettings` |
| Lint/types/tests | ruff, mypy `--strict`, pytest + coverage floor 89.9% | `pyproject.toml`, `Makefile` |
| Security CI | bandit, semgrep, gitleaks, pip-audit, SBOM step | `ci/security/`, `Makefile` `audit` |
| Deploy | Docker (digest-pinned bases), Compose, read-only rootfs, `cap_drop: ALL` | `Dockerfile`, `docker-compose.yml` |

## 4. Inventory: AI agents

Two distinct agent layers exist. See [AGENT_CATALOG.md](AGENT_CATALOG.md) for contracts.

**Desk seats** (prompt files loaded by a host runtime; also supervised in-process by
`supervisor/scheduler/supervisor.py`): CHIEF, SCOUT, RISK, WHALE, SNIPER, RUG, EXIT, SHILL,
SIGNAL, JOURNAL. Only SNIPER and EXIT have `writes_to_exchange: true`, and in this release
their writes reach only the paper adapter. Each seat has a least-privilege tool roster in
`contracts/tool-rosters/bot-NN-<seat>.yaml`.

**Engine Grok agents** (`src/pumpgrok/engine/agents/`): `auditor` (wallet/trade-stream
audit, fast model), `timing` (market backdrop, cached 15 min), `narrative` (meme potential,
four 0..1 scores), `checker` (adversarial last gate on the strong model; `approve: false` is
the default and the failure outcome), plus thin `scout`, `rug`, `signal`, `whale` wrappers.
All responses are parsed into pydantic models; prompts are code constants.

Mapping to the brief's proposed roles: CHIEF = CHIEF; SCOUT = SCOUT; WHALE = WHALE + engine
`auditor`; QUANT = SIGNAL; NARRATIVE = SHILL + engine `narrative`; SKEPTIC = engine
`checker`; RISK = RISK seat + deterministic `RiskEngine`; JOURNAL = JOURNAL;
STRATEGY_RESEARCHER = no seat (the backtest/promotion code exists, no agent owns it).

## 5. Inventory: skills

35 skills, each `skills/<name>/SKILL.md`, name checked against the directory by
`scripts/check.sh`.

| Group | Skills |
|-------|--------|
| Bootstrap and operating model | pumpgrok-setup, tool-connections, desk-operating-model, desk-folders-and-journal, hermes-cron-desk, desk-doctor |
| Lifecycle and risk | desk-trade-lifecycle, desk-risk-limits, desk-execution-protocol, risk-audit, kill-switch, live-gate-procedure, key-custody, desk-incident-response |
| Monitoring and review | desk-monitoring, position-monitoring, desk-post-trade-review, daily-report, portfolio-ledger, verification-receipts |
| Research | desk-strategy-lab, replay-and-backtest, momentum-signals, safety-scoring, grokbot-pipeline |
| Market and chain | discovery-tools, holder-and-flow-analysis, social-sentiment, solana-market-data, solana-api-reference, solana-rpc-and-wallet, jupiter-routing, rpc-hygiene |
| Host tooling | browser-ops, paper-broker |

## 6. Inventory: data sources

| Source | Client | Use | Notes |
|--------|--------|-----|-------|
| Solana JSON-RPC | `tools/onchain/rpc.py` | curve account, authorities, priority fee, top holders, transfers, token metadata, deployer history | ordered failover, tenacity retry on injected clock, keyless URLs, key as header |
| PumpPortal WebSocket | `datafeed/portal.py`, `engine/monitor.py` `LaunchMonitor.stream` | new-launch and trade stream | live-paper path only |
| pump.fun frontend REST `https://frontend-api.pump.fun` | `engine/analyzer.py`, `tools/social/client.py` | token detail and trades | social client is not registered as a tool |
| Jupiter Swap v2 `/order` | `tools/market/jupiter.py`, `tools/exec/quote.py` | informational quote only | never `/execute`, never the fill price |
| x.ai Grok `https://api.x.ai/v1` | `engine/agents/base.py`, `grok/` | LLM verdicts | budget, cache, coalesce, fallback in `grok/` |
| Telegram Bot API | `tools/notify/telegram.py` | alerts | allowlisted destinations |
| Recorded datasets | `replay/fixtures.py`, `replay/recorder.py`, `replay/live_recorder.py` | replay and backtest | sealed, hashed manifests |

Absent: DexScreener, GeckoTerminal, RugCheck, Birdeye, any social API. Holder data comes from
`getTokenLargestAccounts` (top 20 accounts only), which bounds every concentration metric.

## 7. Inventory: trading, simulation, research

| Capability | Status | Where |
|------------|--------|-------|
| Bonding-curve fill model (constant product on curve reserves) | Implemented | `execution/curve_math.py`, `execution/paper_adapter/` |
| Fees: venue 100 bps, base 5,000 lamports, priority fee, ATA rent, builder fee (v2) | Implemented, constants marked REVIEW | `execution/fees.py` |
| Latency (seeded lognormal, clip 0.4-20 s) and 2% dropped-tx probability | Implemented | `execution/latency.py` |
| Re-read curve at confirm, slippage-exceeded path that still pays network cost | Implemented | `execution/paper_adapter/confirm_stages.py`, `execution/fees.py` |
| Sell planning against exit liquidity | Implemented | `execution/paper_adapter/sell.py`, `sell_stages.py` |
| Marks, unrealised PnL, equity | Implemented | `execution/paper_adapter/marks.py`, `ledger/reducer.py` |
| Deterministic risk walk (25 named steps) | Implemented | `risk/engine/constants.py` `STEP_IDS`, `risk/engine/steps.py` |
| Breakers: data-stale, loss-velocity, exec-errors, half-open probe | Implemented | `risk/breakers.py` |
| Kill switch (file + ledger, clear refusal rules) | Implemented | `risk/killswitch.py` |
| Daily-loss halt capped at 5% in schema; drawdown halt 15% default | Implemented | `core/risk_budget.py` |
| Historical replay with injected `FixtureClock` | Implemented | `replay/`, `supervisor/scheduler/replay_runtime.py` |
| Backtest harness, metrics v1/v2, round trips, divergence vs paper | Implemented | `backtest/` |
| Holdout (final 20% by time) and walk-forward folds | Implemented | `backtest/split.py` |
| Trial ledger: ineligible above 20 trials, refused above 50 | Implemented | `backtest/trials.py` |
| Four registered strategies | Implemented | `strategies/base.py` `STRATEGY_IDS` |
| Promotion stages BACKTEST, PAPER, LIVE_ELIGIBLE (LIVE derived, never stored) | Implemented | `promotion/stages.py` |
| Daily and weekly reports | Implemented | `reporting/` |
| Live adapter | Present, gated off; `LiveGate` refuses | `execution/live/`, `cli/verbs_state.py` |
| Monte Carlo / bootstrap confidence intervals | Not found | |
| Wallet PnL / quality score | Not found | |
| Portfolio exposure by strategy / risk category | Partial: exposure by token | `tools/portfolio/exposure.py`, `risk/strategy_slice.py` |

## 8. Gate results (run during this audit)

| Gate | Result |
|------|--------|
| `uv run mypy src/` | No issues |
| `./scripts/check.sh` | ok: 35 skills, 10 agents, 541 markdown files |
| `uv run ruff check .` | 27 findings, all in untracked work-in-progress scripts (`scripts/measure_replay.py`, `scripts/probe_portal_ws.py`, `scripts/probe_rpc_ws.py`, `scripts/record_pumpfun.py`) |
| `uv run ruff format --check .` | 4 files would be reformatted (same untracked scripts) |
| `uv run pytest -q -m 'not slow'` | Collection error in untracked `tests/engine/test_momentum_gate.py`: `ImportError: cannot import name 'CurveRiseTracker' from 'pumpgrok.engine.pipeline'`. Result with that file ignored: see "Test run" below |

Test run with `uv run pytest -q -m 'not slow' --ignore=tests/engine/test_momentum_gate.py`:
exit 0, about 3,290 tests collected, 2 skipped. The slow soak suite (`make test-soak`) was
not run.

Uncommitted work in the tree (not touched by this audit): the four scripts above,
`tests/test_record_pumpfun_offline.py`, `tests/engine/test_momentum_gate.py` (expects a
`CurveRiseTracker` that does not exist yet), and twelve fixture directories under
`tests/fixtures/replay/`. Three of those are large: `live-06` 124 MB, `live-07` 422 MB,
`live-08` 605 MB. Committing them would bloat the repository permanently.

## 9. Documentation drift

- `README.md`: the intro paragraph, the skills bullet, and the setup sentence each appear
  twice, an older and a newer version.
- `ARCHITECTURE.md` (root): duplicated rows in "System context", "Configuration and
  secrets", and "Unknowns"; the config table still names retired files
  (`authority_check.py`, `paper_sim.py`, `jupiter_quote.py`) and `/workspace/trading-desk`.
  It also says "It is not a long-running application", which the compose services contradict.
- `CLAUDE.md`: the Architecture section repeats the `run_once_replay` bullet, and the Safety
  section appears twice with different line references.
- `services/desk-gateway/src/desk_gateway/console.py` cites `docs/design/ux-spec.json` and
  `docs/architecture/contracts/pumpgrok-gateway.yaml`; neither exists in this checkout.
  `services/desk-gateway/tests/test_console.py` fails collection on the missing
  `docs/design/tokens.json` (outside the default `testpaths`, so `make test` misses it).

## 10. Critical weaknesses

Ranked by consequence. Security items are detailed in [SECURITY_AUDIT.md](SECURITY_AUDIT.md).

1. **Compose pre-approves a human review gate.** `docker-compose.yml` sets
   `PUMPGROK_OD04_PROBE_REVIEW: "approved"` for the default service, so the live-paper
   root's review gate (`cli/verbs_state.py` `_live_paper_outcome`) is satisfied by config,
   not by a person.
2. **Default compose service does no work.** It runs `run --mode paper --live-paper`, which
   builds the desk with `_no_place` (places nothing) and exits 0; with
   `restart: unless-stopped` it restarts in a loop. It binds no health port, so the
   `/healthz` healthcheck cannot pass.
3. **Compose secret file absent.** `secrets.pumpgrok_env.file: ./.secrets/pumpgrok.env`
   does not exist; `docker compose up` fails until an operator creates it. Not documented
   in `SETUP.md`.
4. **`.env` is mode 664** (group-writable) on this host. It is git-ignored and untracked.
5. **No research dashboard.** Operators read JSON and markdown reports only.
6. **Single LLM provider, no local fallback.** A Grok outage degrades every LLM verdict to
   its pessimistic fallback.
7. **Holder metrics are bounded by top-20 accounts** and there is no wallet history model,
   so "smart money" claims cannot be evidenced.
8. **Engine prompt payloads carry untrusted token text** (name, description) inside JSON
   without an explicit untrusted-data marker. Output parsing is strict, and the decision is
   deterministic downstream, so the blast radius is a wrong score, not an action.
9. **Many fee and latency constants are marked REVIEW** (`execution/fees.py`,
   `execution/latency.py`): realistic, but not calibrated against recorded fills.
10. **Curve layout is `[ASSUMED]`** until `scripts/probe_curve_layout.py` confirms it.

## 11. Performance and failure points

- SQLite single-writer: fine for one desk; the event bus and ledger share the process.
  `busy_timeout=5000` is set. No measured bottleneck was found in this audit; none should
  be optimized without a measurement (`scripts/measure_replay.py` is in progress).
- Large fixtures (up to 605 MB) make replay I/O-bound; trimming exists (`replay/trim.py`).
- Single process: supervisor, bus, and health server share one event loop; a blocking call
  stalls the tick. `TICK_S = 1.0` (`supervisor/scheduler/supervisor.py`).
- External dependencies with no redundancy: PumpPortal WS (one provider), x.ai.

## 12. Improvements safe to make now

These do not change runtime behavior or any risk limit:

1. Remove the duplicated stale lines from `README.md`, `ARCHITECTURE.md`, `CLAUDE.md`.
2. Document the compose secret file and the `.env` permission requirement in
   `docs/OPERATIONS.md` (done in this audit).
3. Tighten `.env` to mode 600 (operator action, one command).
4. Add the large `live-06/07/08` fixture directories to `.gitignore` or move them under
   `var/recordings/`, if the owner agrees they are local data.
5. Fix the four untracked scripts' ruff findings before they are committed.

Items that change behavior or security posture (compose OD-04 value, compose default
command) need owner approval and are listed in [ROADMAP.md](ROADMAP.md) as P0.
