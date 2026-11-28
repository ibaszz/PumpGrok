"""Ledger backup and verified restore (HOST-07, ROADMAP P1-7).

``backup_ledger`` writes a consistent snapshot under the state directory with SQLite's
online backup API (safe while the desk is writing: a plain file copy of a WAL database
can miss committed rows or tear), verifies the copy's hash chain, names it by UTC time
and keeps the newest ``keep`` snapshots.
``restore_ledger`` verifies the candidate hash chain before atomically
replacing the active file; a failed verification leaves the active file
byte-identical and is reported as failure.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from contextlib import closing, suppress
from datetime import UTC, datetime
from pathlib import Path

from pumpgrok.replay.clock import WallClock

log = logging.getLogger("pumpgrok.ledger.backup")


BACKUP_PREFIX = "ledger-backup-"
DEFAULT_KEEP = 7


class BackupError(RuntimeError):
    """The snapshot could not be written or did not verify; nothing was kept."""


def _drop(path: Path) -> None:
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        candidate.unlink(missing_ok=True)


def backup_ledger(
    active: str | Path,
    state_dir: str | Path,
    name: str | None = None,
    *,
    keep: int = DEFAULT_KEEP,
    now: float | None = None,
) -> Path:
    """Snapshot the active ledger, verify it, prune old snapshots; return the copy path."""
    src = Path(active)
    if not src.is_file():
        raise BackupError(f"active ledger not found: {src}")
    if keep < 1:
        raise ValueError("keep must be at least 1")
    dest_dir = Path(state_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    when = WallClock().now() if now is None else now
    stamp = datetime.fromtimestamp(when, UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = dest_dir / (name or f"{BACKUP_PREFIX}{stamp}.sqlite")
    partial = dest.with_name(dest.name + ".partial")
    _drop(partial)
    try:
        with (
            closing(sqlite3.connect(str(src))) as source,
            closing(sqlite3.connect(str(partial))) as target,
        ):
            source.backup(target)
    except sqlite3.Error as exc:
        _drop(partial)
        raise BackupError(f"snapshot failed: {type(exc).__name__}") from exc
    os.replace(partial, dest)
    if not verify_candidate(dest):
        _drop(dest)
        raise BackupError("snapshot failed hash-chain verification; not kept")
    if name is None:
        _prune(dest_dir, keep)
    return dest


def _prune(dest_dir: Path, keep: int) -> None:
    """Keep the newest ``keep`` timestamped snapshots (names sort by time)."""
    snapshots = sorted(dest_dir.glob(f"{BACKUP_PREFIX}*.sqlite"))
    for old in snapshots[:-keep]:
        _drop(old)


def verify_candidate(candidate: str | Path) -> bool:
    """True iff the candidate's hash chain verifies."""
    from pumpgrok.ledger.store import LedgerStore  # noqa: PLC0415

    try:
        store = LedgerStore(candidate)
    except Exception as exc:
        log.warning("backup candidate unreadable: %s (%s)", candidate, exc)
        return False
    try:
        return bool(store.verify())
    except Exception as exc:
        log.warning("backup candidate verification failed: %s (%s)", candidate, exc)
        return False
    finally:
        with suppress(Exception):
            store._db.close()


def restore_ledger(candidate: str | Path, active: str | Path) -> bool:
    """Verify then atomically replace. False leaves the active file byte-identical."""
    cand = Path(candidate)
    act = Path(active)
    before = act.read_bytes() if act.exists() else None
    if not verify_candidate(cand):
        after = act.read_bytes() if act.exists() else None
        assert after == before
        return False
    act.parent.mkdir(parents=True, exist_ok=True)
    tmp = act.with_suffix(".tmp.sqlite")
    shutil.copyfile(cand, tmp)
    os.replace(tmp, act)
    return True
