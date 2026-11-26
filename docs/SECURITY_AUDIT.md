# Security audit

Audit date: 2026-11-26, base `7dc5db3`. Scope: secrets, network exposure, auth, agent and
LLM boundaries, trading boundaries, supply chain, container. The repository policy is in
[`SECURITY.md`](../SECURITY.md) and [SAFETY_INVARIANTS.md](SAFETY_INVARIANTS.md); this file
records what was verified and what remains open.

## 1. Controls verified in code

| Area | Control | Evidence |
|------|---------|----------|
| Live trading | `run --mode live` evaluates `LiveGate` and exits 3 on any unmet precondition; `live.enabled` defaults to `False` | `cli/verbs_state.py` `_run`; `execution/live/live_gate.py`; `config/sections/core.py` `LiveSettings` |
| Live-paper root | Refuses unless mode is paper; feed calls pass a read-method allowlist | `supervisor/live_paper.py`; `datafeed/readonly.py` |
| Keys | No signer in the package; key-bearing RPC URLs refused at startup; paid keys sent as header | `config/settings.py` `RpcSettings`; `tools/onchain/rpc.py` |
| Log redaction | Redaction filter on structured logs; RPC errors never carry URL, headers, or `str(exc)` | `logging/redaction.py`; `tools/onchain/rpc.py` |
| Risk authority | Deterministic 25-step walk; daily-loss halt capped at 0.05 by schema validator | `risk/engine/`; `core/risk_budget.py` `_cap_daily_loss` |
| Risk config immutability | Pydantic models are `frozen=True, extra="forbid"`; config changes are ledger events (`config_changed`) | `core/risk_budget.py`; `contracts/events/config_changed.schema.json` |
| Ledger integrity | Append-only triggers, hash chain, `UNIQUE` idempotency key; backup/restore verifies chain | `ledger/store.py`; `ledger/backup.py` |
| LLM output | Single strict JSON object; code fences and injection markers rejected; pydantic parse | `grok/sanitize.py`; `engine/agents/base.py` |
| LLM spend | Per-window, per-agent, global budget; fail closed when exhausted | `grok/budget.py` |
| Tool least privilege | Per-seat rosters; registry refuses tools outside a seat's grant | `contracts/tool-rosters/`; `tests/test_registry_least_privilege.py` |
| Gateway auth | Per-seat bearer tokens from `DESK_GATEWAY_TOKEN_*`, compared with `hmac.compare_digest`: no token 401, wrong seat 403, unconfigured seat 503; refuses live at start; loopback bind check | `services/desk-gateway/src/desk_gateway/server.py`, `main` |
| Health server | Loopback default; `/metrics` 401-gated beyond loopback | `supervisor/health_server.py` |
| Notifications | Destination allowlist; idempotency key per kind/ticket/severity/minute; Telegram errors name status or exception type only, so the bot token in the URL path never reaches a message | `tools/notify/allowlist.py`, `tools/notify/notifier.py` |
| Container | Digest-pinned bases, non-root user, read-only rootfs, `cap_drop: ALL`, `no-new-privileges`, loopback-only ports, no secrets in image | `Dockerfile`, `docker-compose.yml` |
| CI | bandit, semgrep, gitleaks, pip-audit, SBOM, image digest pin tests | `ci/security/`, `ci/tests/`, `.github/workflows/security-pr.yml` |
| Network in tests | Network blocked in the suite | `tests/test_network_block.py` |

## 2. Findings

Severity: High = can weaken a safety gate or leak a secret; Medium = operational exposure
or missing defense in depth; Low = hygiene.

| ID | Sev | Finding | Recommendation | Needs approval |
|----|-----|---------|----------------|----------------|
| S-01 | High | `docker-compose.yml` sets `PUMPGROK_OD04_PROBE_REVIEW: "approved"` for the default service. A human review gate is satisfied by committed config. | Fixed 2026-11-26: live-paper moved to opt-in profile `live-paper`; the flag is read from the operator's shell (`${PUMPGROK_OD04_PROBE_REVIEW:-}`, default empty, refused by the CLI). Test: `tests/hosted/test_compose_live_root.py`. | Approved, done |
| S-02 | Medium | `.env` on the audit host is mode 664 (group read/write). It is untracked and git-ignored. Contents were not read. | `chmod 600 .env` (operator). `pumpgrok doctor` now warns on group/other bits of `.env` and `.secrets/pumpgrok.env` (mode only, never content); test `tests/test_doctor_secret_perms.py`. | Doctor check done; chmod pending |
| S-03 | Medium | Compose mounts `./.secrets/pumpgrok.env` as Docker secret `pumpgrok_env` (at `/run/secrets/pumpgrok_env`), but no code in `src/` or `services/` reads `/run/secrets`. Values put there never reach the app, so gateway seat tokens (`DESK_GATEWAY_TOKEN_*`) and `PUMPGROK_*` settings are unset in containers. The file is also absent and undocumented in `SETUP.md`. | Fixed 2026-11-26: `src/pumpgrok/config/docker_secret.py` `load_docker_secret_env` runs first in the `pumpgrok` CLI and in `desk_gateway`. It accepts only `PUMPGROK_*` / `DESK_GATEWAY_*` keys, lets existing env win, applies all-or-nothing, and names line numbers, never values, in errors. Test: `tests/config/test_docker_secret.py`. | Approved, done |
| S-04 | Medium | Engine prompts embed untrusted token text (name, description) as JSON fields with no explicit "untrusted data" instruction. Output parsing is strict and decisions are downstream, so impact is a wrong score. The injection marker list in `grok/sanitize.py` is five phrases and easy to evade. | Add an untrusted-content instruction to each system prompt and length-cap token text fields before prompting. Treat the marker list as a tripwire, not a defense. | No |
| S-05 | Low | `services/desk-gateway` is excluded from `mypy --strict` (`Makefile` comment: one finding in `server.py`). | Fix the finding and include the service in typecheck. | No |
| S-06 | Low | Large untracked fixtures (`live-06/07/08`, up to 605 MB) may contain recorded wallet addresses; committing them is irreversible repo bloat. | Fixed 2026-11-26: the three directories are in `.gitignore`. | Approved, done |

No hard-coded secret, private key, or seed phrase was found by reading the configuration
and the files listed above. A full `gitleaks` run was not part of this audit; CI runs it on
pull requests.

## 3. Agent and LLM boundary checklist

| Requirement | Status |
|-------------|--------|
| Least-privilege tools per seat | Done (rosters) |
| No shell or code-execution tool in any roster | Done; no such tool id exists in `docs/TOOL_CATALOG.md` |
| No secret reachable by a tool | Done; tools take no keys and read no env |
| External content cannot change instructions | Partial (S-04) |
| Tool argument validation | Done (JSON schema per tool, `additionalProperties: false`) |
| Structured output validation | Done (strict JSON + pydantic) |
| Timeouts | Done for HTTP (`DataSettings.request_timeout`), Grok calls, confirm (`EngineLimits.confirm_timeout_s`) |
| Tool usage recorded | Done (`core/registry.py` grant audit) |
| Research permissions separate from execution | Done (`writes_to_exchange` only on SNIPER/EXIT; paper only) |
| Agents cannot change risk limits | Done; risk config is frozen settings plus ledger `config_changed`; no tool writes limits |

## 4. Trading boundary checklist

| Requirement | Status |
|-------------|--------|
| Live disabled by default | Done |
| No keys in prompts | Done (constitution in every `agents/*.md`) |
| No seed phrases in logs | Done (redaction) |
| No research-agent access to wallet secrets | Done (no wallet secret exists in the package) |
| No autonomous risk-limit change | Done |
| No unrestricted signing or arbitrary tx | Done (no signer; live adapter gated) |
| Separate boundary for future execution | Planned: `services/desk-signer` is referenced in `Makefile` but absent |

## 5. Remaining risks accepted for now

- Curve layout is `[ASSUMED]`; a wrong layout gives wrong paper prices, not a fund loss.
- Single LLM provider; outage degrades to pessimistic fallbacks (fail closed).
- SQLite file on a named volume; loss of the volume without a backup loses history
  (`pumpgrok ledger backup` exists; scheduling it is an operator task).
