"""P0-6: doctor warns on group/other bits of secret env files, never reads content."""

from __future__ import annotations

from pathlib import Path

from pumpgrok.doctor import secret_file_permission_lines


def _file(tmp_path: Path, mode: int) -> Path:
    path = tmp_path / ".env"
    path.write_text("PUMPGROK_X=do-not-print\n", encoding="utf-8")
    path.chmod(mode)
    return path


def test_group_writable_file_warns(tmp_path: Path) -> None:
    path = _file(tmp_path, 0o664)
    (line,) = secret_file_permission_lines((path,))
    assert line.startswith("warn  secret-file")
    assert "mode=664" in line
    assert "chmod 600" in line
    assert "do-not-print" not in line


def test_world_readable_file_warns(tmp_path: Path) -> None:
    (line,) = secret_file_permission_lines((_file(tmp_path, 0o604),))
    assert line.startswith("warn")


def test_owner_only_file_is_ok(tmp_path: Path) -> None:
    (line,) = secret_file_permission_lines((_file(tmp_path, 0o600),))
    assert line.startswith("ok    secret-file")


def test_missing_file_is_silent(tmp_path: Path) -> None:
    assert secret_file_permission_lines((tmp_path / "absent.env",)) == []
