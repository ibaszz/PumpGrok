# Trading research: paper engine and strategy lab

What the simulator models today, what it does not, and how strategy evidence is gated.
Design source: [design/paper-engine.md](design/paper-engine.md). Risk policy:
[RISK_POLICY.md](RISK_POLICY.md).

No strategy in this repository has demonstrated profitability. Every number below is a
model parameter, not a result.

## 1. Paper fill model

| Effect | Model | Location | Calibrated? |
|--------|-------|----------|-------------|
| Price | Constant-product on bonding-curve reserves read from the curve account; never a quote | `execution/curve_math.py`, `execution/paper_adapter/quote.py` | Layout `[ASSUMED]` (`tools/onchain/curve_layout.py`) |
| Price impact | Implicit in the curve math for the order size | `execution/curve_math.py` | Exact for the assumed curve |
| Venue fee | 100 bps | `execution/fees.py` `VENUE_FEE_BPS` | REVIEW |
| Network fee | 5,000 lamports base + priority (`micro_lamports_per_cu * 200,000 // 1e6`), capped | `execution/fees.py` | REVIEW |
| ATA rent, builder fee | First buy of a mint; 50 bps when that builder is used (v2) | `execution/fees.py` `FeeModelV2` | REVIEW |
| Latency | Seeded lognormal, mu ln(1.2), sigma 0.5, clip 0.4-20 s | `execution/latency.py` | REVIEW |
| Failed tx | 2% drop probability; dropped orders are never resubmitted | `execution/latency.py` `P_DROP` | REVIEW |
| Slippage | Curve re-read after latency; `slippage_exceeded` rejects but still pays network cost | `execution/paper_adapter/confirm_stages.py` | Model |
| Partial fills | `partial_fill_fraction` in `RiskBudget` | `core/risk_budget.py` | REVIEW |
| Exit liquidity | Sell plan against current reserves; liquidity floor step in risk | `execution/paper_adapter/sell_stages.py`, `risk/engine/constants.py` `below_liquidity_floor` | Model |
| Post-graduation | Out of scope for the curve model | | Gap |

Not modeled: MEV/sandwich, competing same-slot buyers, spread on post-graduation AMMs,
RPC propagation differences. These belong in the "execution assumptions" record of every
trade (ROADMAP P1-4).

## 2. Trade record

Ledger events (`contracts/events/*.schema.json`): `order_placed`, `order_rejected`,
`fill_recorded`, `position_opened`, `mark_to_market`, `position_closed`, plus
`approval_recorded`, halts, breakers, kills. Each row carries `ts_utc`, `source`,
`correlation_id`, `idempotency_key`, and a hash link. Rebuild state with
`ledger/reducer.py`.

Against the brief's per-trade field list: strategy id and parameter-set hash travel with
promotion rows (`promotion/stages.py`); fees, sizes, fills, exit reason, and realized PnL
are on the fill and close rows. Not yet on every trade row: an explicit `signal_id`, the
dataset snapshot id for live-paper, and an `execution_assumptions` block naming the fee
model version and latency parameters. ROADMAP P1-4 adds them without a schema-breaking
change (new optional payload keys).

## 3. Strategy lab (`src/pumpgrok/backtest/`)

| Guard | Implementation |
|-------|----------------|
| Lookahead | `FixtureClock` drives every read; ticks are consumed at their own time |
| Holdout | Final 20% of the time span (`backtest/split.py` `HOLDOUT_FRACTION = 0.20`), never read by tuning |
| Walk-forward | Folds inside the pre-holdout region (`backtest/split.py`) |
| Multiple testing | Trial ledger: above 20 trials a result is ineligible; above 50 recording is refused (`backtest/trials.py`) |
| Reproducibility | Sealed dataset manifests with hashes (`backtest/manifest.py`); seeded RNG (`replay/rng.py`) |
| Paper vs backtest drift | `backtest/divergence.py` |
| Undefined ratios | `None`, never 0 or inf; annualization needs >= 7 days (`backtest/metrics_v2.py`) |
| Gaps in data | `backtest/gaps.py` |

Metrics present (`MetricsV2`): total return, realized net lamports, max drawdown, Sharpe
(per-fill steps), win rate, profit factor, trade count, exposure, turnover. Missing:
expectancy, Sortino, median return, average win/loss, bootstrap or Monte Carlo confidence intervals, sensitivity
sweeps reported as a surface, survivorship accounting for tokens that never reached the
recorder.

## 4. Strategy lifecycle

Code stages: `BACKTEST -> PAPER -> LIVE_ELIGIBLE` (`promotion/stages.py`); `LIVE` is derived
and never stored. Every transition is a ledger row with evidence run id, parameter-set hash,
and metrics snapshot; only the writer seat may write. Demotion is automatic on thresholds
(`promotion/demotion.py`).

Brief lifecycle mapping:

| Brief | Code |
|-------|------|
| PROPOSED | not stored (a strategy id must be in the static registry, a code change) |
| TESTING, VALIDATION | `BACKTEST` (with holdout and trial eligibility) |
| PAPER OBSERVATION | `PAPER` |
| REVIEW, APPROVED | `LIVE_ELIGIBLE` requires human evidence; live itself stays refused |
| REJECTED | demotion rows |

Registered strategies (`strategies/base.py` `STRATEGY_IDS`): `legacy_screening`,
`momentum_breakout`, `new_launch_sniper`, `mean_reversion`.

## 5. Research verdicts (proposed)

The brief's outcomes map onto the existing gate without giving agents authority:

| Outcome | Condition |
|---------|-----------|
| REJECT | any risk step fails that is not data-related, or an engine fallback/`checker` reject |
| WATCH | risk passes structural checks but data is stale or incomplete (`data_stale`, missing fields) |
| RESEARCH | passes risk, but at least one agent report lists an unresolved contradiction |
| APPROVED_FOR_PAPER_SIMULATION | `RiskEngine` approves and no agent recommends REJECT |

Agents can move a candidate down this list, never up.
