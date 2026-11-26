# Decisions

Architecture decision log for the 2026-11-26 audit and the work it schedules. Earlier
decisions live in `.planning/` (CONTEXT files, `D-NN` ids) and in the design docs; this log
does not restate them. Format: context, decision, consequence.

## D-001 Evolve in place; no rewrite

Context: the brief asks for a production research desk. The audit found ~44k lines with
3,290 passing tests covering paper fills, risk, replay, backtest, and promotion.
Decision: keep every existing module and boundary. New work lands as additions next to the
modules it extends.
Consequence: roadmap items name existing files; no item replaces a working module.

## D-002 The deterministic risk engine stays the only approval authority

Context: the brief adds agent roles and research verdicts.
Decision: agents emit evidence and a recommendation limited to REJECT, WATCH, or RESEARCH.
Only the deterministic mapping from `RiskDecision` can yield
`APPROVED_FOR_PAPER_SIMULATION`, and agent input can only lower a verdict.
Consequence: no prompt change can raise the desk's risk appetite.

## D-003 No new seats in the first increment

Context: the brief lists QUANT, NARRATIVE, SKEPTIC, STRATEGY_RESEARCHER. Existing SIGNAL,
SHILL + `narrative`, and `checker` already cover the first three.
Decision: map the proposed roles onto existing seats and engine agents
([AGENT_CATALOG.md](AGENT_CATALOG.md) section 3). Add STRATEGY_RESEARCHER only after the lab
metrics it would read exist (ROADMAP P2-6).
Consequence: no change to `ownership.yaml` or rosters now. More agents are not assumed to
improve outcomes.

## D-004 SQLite stays

Context: the brief mentions time-series optimization and new entities.
Decision: keep the append-only SQLite ledger and the SQLite event store. Add indexes or
tables through `ledger/migrations/` only when a measured query needs them.
Consequence: no new database dependency; backup stays a file copy with chain verification.

## D-005 Security-posture changes wait for owner approval

Context: compose pre-approves `PUMPGROK_OD04_PROBE_REVIEW` and its default command does not
serve. Fixing either changes deployed behavior.
Decision: document both (SECURITY_AUDIT S-01, OPERATIONS section 2), schedule as P0-1 and
P0-2, and do not edit compose without explicit approval.
Consequence: the defect stays in the tree until approved; the workaround is documented.

## D-006 Do not touch the owner's uncommitted work

Context: four scripts, two tests, and twelve fixture directories are untracked; one test
fails collection.
Decision: leave them unmodified; report the lint and import failures; run the suite with
that test ignored and say so.
Consequence: `make check` on this working tree fails until the owner finishes or drops the
work (ROADMAP P0-3).

## D-007 New external data providers come after the provenance envelope

Context: DexScreener, GeckoTerminal, and RugCheck are absent.
Decision: add no provider before ROADMAP P1-2 defines how every datum records source,
times, quality, and availability.
Consequence: new providers arrive already attributed; missing data is reported as
`unavailable`, never filled.

## D-008 Default compose service serves the replay loop (amends HOST-16)

Context: HOST-16 put `run --live-paper` in the default `pumpgrok` service with
`PUMPGROK_OD04_PROBE_REVIEW: "approved"` committed. That command exits after building, so
the healthcheck never passed and `restart: unless-stopped` looped it; the committed flag
bypassed a human review.
Decision (owner approved 2026-11-26): the default service runs
`run --mode paper --serve --replay tests/fixtures/replay/smoke`. The live-paper root moves
to service `pumpgrok-live-paper` under profile `live-paper`, `restart: "no"`, no port, with
the OD-04 flag interpolated from the operator's shell and empty by default. The
`pumpgrok-replay` service is removed because the default now does the same job.
Consequence: `tests/hosted/test_compose_live_root.py` and
`tests/safety/test_compose_hardening.py` assert the new topology. A long-running live-paper
loop is a separate, code-level item (ROADMAP P0-2b).
