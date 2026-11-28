"""HOST-07: backup round-trips; restore verifies then replaces."""

from __future__ import annotations

from pathlib import Path

import pytest

from pumpgrok.ledger.backup import BackupError, backup_ledger, restore_ledger, verify_candidate
from pumpgrok.ledger.store import LedgerStore


def _make_ledger(path: Path, n: int = 2) -> None:
    store = LedgerStore(path)
    try:
        for i in range(n):
            store.append(
                ts_utc="2026-01-01T00:00:00Z",
                source="paper",
                correlation_id=f"cid-{i}",
                idempotency_key=f"idem-{i}",
                type="JournalNote",
                payload={"source": "paper", "seat": "desk", "note_kind": "note", "text": f"n{i}"},
            )
    finally:
        store._db.close()


def test_backup_round_trips(tmp_path: Path) -> None:
    active = tmp_path / "ledger.sqlite"
    _make_ledger(active)
    state_dir = tmp_path / "state"
    dest = backup_ledger(active, state_dir)
    assert dest.is_file()
    assert verify_candidate(dest) is True


def test_restore_valid_candidate(tmp_path: Path) -> None:
    active = tmp_path / "ledger.sqlite"
    _make_ledger(active, n=2)
    state_dir = tmp_path / "state"
    dest = backup_ledger(active, state_dir)
    # Append one more row to active, then restore the backup over it.
    store = LedgerStore(active)
    try:
        store.append(
            ts_utc="2026-01-01T00:01:00Z",
            source="paper",
            correlation_id="cid-x",
            idempotency_key="idem-x",
            type="JournalNote",
            payload={"source": "paper", "seat": "desk", "note_kind": "note", "text": "x"},
        )
    finally:
        store._db.close()
    assert restore_ledger(dest, active) is True
    assert verify_candidate(active) is True


def test_corrupt_candidate_refused_byte_identical(tmp_path: Path) -> None:
    active = tmp_path / "ledger.sqlite"
    _make_ledger(active, n=1)
    state_dir = tmp_path / "state"
    dest = backup_ledger(active, state_dir)
    raw = dest.read_bytes()
    dest.write_bytes(raw[: len(raw) // 2])
    before = active.read_bytes()
    assert restore_ledger(dest, active) is False
    after = active.read_bytes()
    assert before == after


def test_backup_is_a_consistent_snapshot_of_a_live_wal_ledger(tmp_path: Path) -> None:
    """P1-7: rows still in the WAL (writer open, not checkpointed) are in the snapshot."""
    active = tmp_path / "ledger.sqlite"
    store = LedgerStore(active)
    try:
        for i in range(3):
            store.append(
                ts_utc="2026-01-01T00:00:00Z",
                source="paper",
                correlation_id=f"cid-{i}",
                idempotency_key=f"live-{i}",
                type="JournalNote",
                payload={"source": "paper", "seat": "desk", "note_kind": "note", "text": "x"},
            )
        dest = backup_ledger(active, tmp_path / "state", now=1_769_900_000.0)
    finally:
        store._db.close()
    copy = LedgerStore(dest)
    try:
        assert copy.verify() is True
        assert len(copy.read(limit=None)) == 3
    finally:
        copy._db.close()
    assert dest.name == "ledger-backup-20260131T225320Z.sqlite"


def test_backup_keeps_only_the_newest_snapshots(tmp_path: Path) -> None:
    active = tmp_path / "ledger.sqlite"
    _make_ledger(active)
    state = tmp_path / "state"
    for day in range(5):
        backup_ledger(active, state, keep=3, now=1_769_900_000.0 + day * 86_400)
    names = sorted(p.name for p in state.glob("ledger-backup-*.sqlite"))
    assert names == [
        "ledger-backup-20260202T225320Z.sqlite",
        "ledger-backup-20260203T225320Z.sqlite",
        "ledger-backup-20260204T225320Z.sqlite",
    ]
    assert not list(state.glob("*.partial"))


def test_backup_of_a_missing_ledger_is_refused(tmp_path: Path) -> None:
    with pytest.raises(BackupError, match="not found"):
        backup_ledger(tmp_path / "absent.sqlite", tmp_path / "state")
