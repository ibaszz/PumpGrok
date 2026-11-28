# Operations

Operator view of running PumpGrok. Detailed procedures live in [runbooks/](runbooks/):
`start_stop.md`, `setup.md`, `outage.md`, `kill_switch.md`, `breaker_reset.md`,
`rollback.md`, `promotion.md`, `recorder.md`, `datafeed.md`, `events.md`, `engine.md`,
`reading_reports.md`, `go_live.md`. This file is the index plus the gaps found in the
2026-11-26 audit.

## 1. Run forms

| Command | What it does | Exits |
|---------|--------------|-------|
| `uv run pumpgrok run --mode paper --once --replay tests/fixtures/replay/smoke` | One replay cycle; ledger under `var/replay/<dataset>/` | 0 |
| `uv run pumpgrok run --mode paper --serve --replay <dir>` | Replay loop with `/healthz`, `/metrics` | runs |
| `uv run pumpgrok run --mode paper --live-paper` | Composes live-data paper desk; places nothing today; needs `PUMPGROK_OD04_PROBE_REVIEW=approved` and the paper onboarding inputs | 0 after build |
| `uv run pumpgrok run --mode paper --live-paper --serve` | Same desk plus the PumpPortal feed loop and `/healthz` (`status=ok` only after a frame); needs `PUMPGROK_HEALTH__PORT`; SIGINT/SIGTERM stop it | 0 on stop; 1 config error without a port |
| `uv run pumpgrok run --mode live` | Refused | 3 |
| `uv run pumpgrok doctor` | Effective config, killswitch, v4 runtime report (read-only) | 0 |
| `uv run pumpgrok ledger backup [--keep N]` | Verified, timestamped snapshot of `var/ledger.sqlite` under the state directory; keeps the newest N (default 7) | 0; 1 if the ledger is missing or the copy fails verification |
| `uv run pumpgrok ledger restore --from <file>` | Verify hash chain, then atomically replace | 0 or 1 |
| `make gateway` | Desk gateway (MCP per-seat) on loopback | runs |

Paper start without `--replay` first runs `onboarding/start_gate.py` `check_start` and
refuses with the list of missing inputs.

## 2. Docker Compose

Services in `docker-compose.yml`: `pumpgrok` (default; `run --serve --replay` on the smoke
fixture, `/healthz` on 127.0.0.1:8080), `pumpgrok-live-paper` (profile `live-paper`, `--live-paper --serve` loop,
container-internal `/healthz` on 8081, no published port, no restart), `desk-gateway`. All three: read-only rootfs, `tmpfs /tmp`, `cap_drop: ALL`,
`no-new-privileges`, loopback-only published ports, state on the `pumpgrok-state` volume.

### Required before `docker compose up`

1. Create the secret file the compose `secrets` block names. It does not ship:

   ```bash
   mkdir -p .secrets
   install -m 600 /dev/null .secrets/pumpgrok.env
   # never commit this file
   ```

   Format: one `KEY=VALUE` per line, `#` comments allowed. Only `PUMPGROK_*` and
   `DESK_GATEWAY_*` keys are accepted (e.g. `DESK_GATEWAY_TOKEN_CHIEF=...`); any other key
   refuses startup with exit 1. Values in compose `environment:` win over the file.

   Compose (non-swarm) bind-mounts the file with its host owner and mode. The container
   runs as the non-root `pumpgrok` user, so a host-owned mode-600 file is unreadable inside
   and startup refuses with `docker secret ... unreadable (PermissionError)`. Give the file
   to the container uid and keep mode 600:

   ```bash
   uid=$(docker compose run --rm --no-deps --entrypoint id pumpgrok -u)
   sudo chown "$uid" .secrets/pumpgrok.env
   ```

2. Keep the local `.env` private: `chmod 600 .env`. `pumpgrok doctor` warns when it is not.
3. Validate: `docker compose config --quiet`.

### Live-data paper root (opt-in)

After the OD-04 probe review, and only then:

```bash
PUMPGROK_OD04_PROBE_REVIEW=approved docker compose --profile live-paper run --rm pumpgrok-live-paper
```

The flag is never committed. Without it the CLI refuses. The root builds the desk, places
nothing, and exits; it has no port and no restart policy.

## 3. Health and monitoring

| Surface | Where | Notes |
|---------|-------|-------|
| `/healthz`, `/metrics` | serve mode (`cli/verbs_state.py` `_serve_replay_loop`) | loopback |
| `/livez`, `/readyz`, `/metrics` | `supervisor/health_server.py` | `/readyz` 503 on failed ledger verification or stale tick; metrics 401 beyond loopback |
| `var/health.json` | `supervisor/health.py` | event, strategy, promotion sections |
| Alert rules | [observability/alerts.yaml](observability/alerts.yaml) | name only registry metrics |
| Gateway liveness | `desk-gateway` `/healthz` | no token, no detail |

No log shipping or external alert receiver is configured; the compose logging driver is
`json-file`, 5 x 20 MB.

## 4. Backup and restore

- Backup: `uv run pumpgrok ledger backup` (in compose:
  `docker compose exec pumpgrok pumpgrok ledger backup` once the service stays up).
- Restore: stop the desk, then `uv run pumpgrok ledger restore --from <copy>`. Restore
  refuses a candidate whose hash chain fails; the active ledger is untouched in that case.
- Each backup is a consistent snapshot taken with SQLite's online backup API (safe while
  the desk writes), verified against the hash chain before it is kept, and named
  `ledger-backup-<UTC>.sqlite` under the state directory. `--keep N` (default 7) prunes
  older snapshots. A snapshot that fails verification is deleted and the command exits 1.
- Schedule it on the host (P1-7). Example crontab line, daily at 03:17 UTC, two weeks kept:

  ```text
  17 3 * * * cd /srv/pumpgrok && uv run pumpgrok ledger backup --keep 14 >> var/backup.log 2>&1
  ```

  Copy the newest snapshot off the host as well; a snapshot on the same disk does not
  survive a disk loss.

## 5. Recovery behavior

| Failure | Behavior |
|---------|----------|
| Feed stale | `DataStaleBreaker` opens; entries refused with `data_stale`; half-open probe after cooldown |
| Loss velocity / exec errors | breakers open; reset by runbook `breaker_reset.md` |
| Daily loss >= limit | halt row; cleared only by operator (`halt_cleared`) |
| Kill switch | file + ledger row; clear refused while conditions hold (`risk/killswitch.py`) |
| Poison event | dead-letter queue (`events/dlq.py`) |
| Seat crash | `supervisor/seat_restart.py` restart policy |
| Process crash | compose `restart: unless-stopped`; ledger replays from snapshot + events |
| LLM outage or budget exhausted | typed pessimistic fallback; no entry on missing verdict |

## 6. Upgrades

1. `git pull`, then `make check` (lint, typecheck, tests, consistency, gates).
2. `docker compose build`.
3. `uv run pumpgrok ledger backup`.
4. `docker compose up -d`.
5. `uv run pumpgrok doctor` and check `/readyz`.

Ledger migrations are additive SQL in `src/pumpgrok/ledger/migrations/` with paired
`.down.sql`; schema creation uses `IF NOT EXISTS`, so an existing ledger gains new indexes on
next open.

## 7. Troubleshooting

| Symptom | Check |
|---------|-------|
| `paper start refused: missing required inputs: ...` | `uv run pumpgrok doctor`; seat access file per `onboarding/` |
| `live-paper root refused: PUMPGROK_OD04_PROBE_REVIEW must be approved` | Expected until the probe review is done; then pass the flag in the shell (section 2) |
| `warn  secret-file .env mode=664` in doctor | `chmod 600 .env` |
| `/readyz` 503 | ledger verification failed or tick stale; read `var/health.json` |
| Gateway 503 for a seat | `DESK_GATEWAY_TOKEN_<SHORT>` unset for that seat |
| `docker compose up` secret error | section 2, step 1 |
