"""P0-7: compose Docker secret reaches the process environment, safely."""

from __future__ import annotations

from pathlib import Path

import pytest

from pumpgrok.config.docker_secret import load_docker_secret_env
from pumpgrok.config.settings import ConfigError


def _secret(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "pumpgrok_env"
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_file_is_noop(tmp_path: Path) -> None:
    env: dict[str, str] = {}
    assert load_docker_secret_env(tmp_path / "absent", env) == []
    assert env == {}


def test_loads_allowed_keys_and_skips_comments(tmp_path: Path) -> None:
    path = _secret(
        tmp_path,
        "# comment\n\nPUMPGROK_RPC__ENDPOINTS=https://rpc.example\n"
        "DESK_GATEWAY_TOKEN_CHIEF=\"tok en\"\nPUMPGROK_X='a=b'\n",
    )
    env: dict[str, str] = {}
    loaded = load_docker_secret_env(path, env)
    assert sorted(loaded) == ["DESK_GATEWAY_TOKEN_CHIEF", "PUMPGROK_RPC__ENDPOINTS", "PUMPGROK_X"]
    assert env["DESK_GATEWAY_TOKEN_CHIEF"] == "tok en"
    assert env["PUMPGROK_X"] == "a=b"


def test_existing_environment_wins(tmp_path: Path) -> None:
    path = _secret(tmp_path, "PUMPGROK_HEALTH__HOST=127.0.0.1\n")
    env = {"PUMPGROK_HEALTH__HOST": "0.0.0.0"}
    assert load_docker_secret_env(path, env) == []
    assert env["PUMPGROK_HEALTH__HOST"] == "0.0.0.0"


@pytest.mark.parametrize("line", ["PATH=/evil", "LD_PRELOAD=/x.so", "pumpgrok_lower=zz-hidden"])
def test_disallowed_key_refused_without_value(tmp_path: Path, line: str) -> None:
    env: dict[str, str] = {}
    with pytest.raises(ConfigError) as info:
        load_docker_secret_env(_secret(tmp_path, f"PUMPGROK_OK=1\n{line}\n"), env)
    assert "line 2" in str(info.value)
    assert line.split("=", 1)[1] not in str(info.value)
    assert env == {}  # all-or-nothing


def test_malformed_line_refused_without_value(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as info:
        load_docker_secret_env(_secret(tmp_path, "sk-live-secret-no-equals\n"), {})
    assert "expected KEY=VALUE" in str(info.value)
    assert "sk-live-secret" not in str(info.value)
