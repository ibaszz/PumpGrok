"""HOST-16 as amended 2026-11-26 (docs/DECISIONS.md D-008).

The default desk service serves the replay loop so its /healthz probe can pass; the
live-data paper root is opt-in under the ``live-paper`` profile, never restarts, and
its OD-04 review flag is never committed as ``approved``.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_services = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))["services"]


def _command(name: str) -> str:
    return " ".join(str(x) for x in _services[name]["command"])


def test_default_desk_serves_replay_loop() -> None:
    command = _command("pumpgrok")
    assert "--serve" in command
    assert "--replay" in command
    assert "--live-paper" not in command
    assert "profiles" not in _services["pumpgrok"]


def test_live_paper_root_is_opt_in_profile() -> None:
    live = _services["pumpgrok-live-paper"]
    assert live["profiles"] == ["live-paper"]
    assert "--live-paper" in _command("pumpgrok-live-paper")
    assert "--replay" not in _command("pumpgrok-live-paper")
    # The root exits after building; a restart policy would loop it.
    assert str(live["restart"]) == "no"
    assert "ports" not in live
    assert "healthcheck" not in live


def test_od04_review_never_committed_as_approved() -> None:
    for name, svc in _services.items():
        value = str(svc.get("environment", {}).get("PUMPGROK_OD04_PROBE_REVIEW", ""))
        assert "approved" not in value, name
    live_env = _services["pumpgrok-live-paper"]["environment"]
    assert live_env["PUMPGROK_OD04_PROBE_REVIEW"] == "${PUMPGROK_OD04_PROBE_REVIEW:-}"


def test_gateway_has_no_fixture_command() -> None:
    assert "command" not in _services["desk-gateway"]
