"""Load the compose Docker secret into the process environment (P0-7, SECURITY_AUDIT S-03).

``docker-compose.yml`` mounts ``.secrets/pumpgrok.env`` at ``/run/secrets/pumpgrok_env``.
Settings and the gateway read only the process environment, so both entry points call
:func:`load_docker_secret_env` first. Rules:

- A missing file is a no-op (local runs, tests).
- Lines are ``KEY=VALUE``; blank lines and ``#`` comments are skipped; one pair of
  matching surrounding quotes is stripped from the value.
- Only ``PUMPGROK_*`` and ``DESK_GATEWAY_*`` keys are accepted, so the file cannot set
  ``PATH``, ``LD_PRELOAD`` or similar.
- A key already in the environment wins: compose ``environment:`` stays authoritative.
- Any defect raises :class:`ConfigError` naming the line number and key only, never a value.
"""

from __future__ import annotations

import os
import re
from collections.abc import MutableMapping
from pathlib import Path

from pumpgrok.config.settings import ConfigError

DOCKER_SECRET_PATH = Path("/run/secrets/pumpgrok_env")  # pragma: allowlist secret: path
_KEY_RE = re.compile(r"^(PUMPGROK_|DESK_GATEWAY_)[A-Z0-9_]+$")

__all__ = ["DOCKER_SECRET_PATH", "load_docker_secret_env"]  # pragma: allowlist secret: names


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def load_docker_secret_env(
    path: Path = DOCKER_SECRET_PATH,
    environ: MutableMapping[str, str] | None = None,
) -> list[str]:
    """Merge the secret file into ``environ``; return the key names set (never values)."""
    env = os.environ if environ is None else environ
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError([f"docker secret {path}: unreadable ({type(exc).__name__})"]) from None
    pairs: dict[str, str] = {}
    problems: list[str] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep:
            problems.append(f"docker secret {path} line {number}: expected KEY=VALUE")
        elif not _KEY_RE.match(key):
            problems.append(
                f"docker secret {path} line {number}: key not allowed "
                "(PUMPGROK_* or DESK_GATEWAY_* only)"
            )
        else:
            pairs[key] = _unquote(value.strip())
    if problems:
        raise ConfigError(problems)
    loaded = []
    for key, value in pairs.items():
        if key not in env:
            env[key] = value
            loaded.append(key)
    return loaded
