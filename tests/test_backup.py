"""
Tests for resync_claw.backup
"""

import os
import sys
import zipfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from resync_claw.backup import (
    snapshot_name,
    build_rsync_cmd,
    RSYNC_EXCLUDES,
    write_latest_marker,
    get_latest_marker,
    count_files_and_size,
    compress_snapshot,
)
from resync_claw.resync import is_safe_relative_path
from datetime import date


def test_snapshot_name():
    name = snapshot_name(date(2026, 3, 31))
    assert name == "openclaw.bak.20260331"


def test_snapshot_name_today():
    name = snapshot_name()
    assert name == "openclaw.bak." + date.today().strftime("%Y%m%d")


def test_build_rsync_cmd():
    cmd = build_rsync_cmd("/src", "/dest/openclaw.bak.20260331", dry_run=False)
    assert cmd[0] == "rsync"
    assert cmd[1] == "-a"
    assert "--exclude" in cmd
    assert "tmp/" in RSYNC_EXCLUDES
    assert "node_modules/" in RSYNC_EXCLUDES


def test_build_rsync_cmd_dry_run():
    cmd = build_rsync_cmd("/src", "/dest/openclaw.bak.20260331", dry_run=True)
    assert "--dry-run" in cmd


def test_is_safe_relative_path():
    assert is_safe_relative_path("workspace-coding/AGENTS.md") is True
    assert is_safe_relative_path("sub/nested/file.txt") is True
    assert is_safe_relative_path("../etc/passwd") is False
    assert is_safe_relative_path("/etc/passwd") is False
    assert is_safe_relative_path("foo/../../bar") is False


def test_count_files_and_size(tmp_path):
    with open(os.path.join(str(tmp_path), "a.txt"), "w") as f:
        f.write("hello")   # 5 bytes
    os.mkdir(os.path.join(str(tmp_path), "sub"))
    with open(os.path.join(str(tmp_path), "sub", "b.txt"), "w") as f:
        f.write("world!")  # 6 bytes

    files, size = count_files_and_size(str(tmp_path))
    assert files == 2
    assert size == 11


def test_latest_marker(tmp_path):
    marker_path = os.path.join(str(tmp_path), "openclaw.bak.latest.txt")
    write_latest_marker(str(tmp_path), "openclaw.bak.20260331")
    assert os.path.exists(marker_path)
    name = get_latest_marker(str(tmp_path))
    assert name == "openclaw.bak.20260331"


def test_latest_marker_missing():
    # Non-existent dir should return None without crashing
    assert get_latest_marker("/nonexistent/path") is None


def test_compress_snapshot_creates_zip(tmp_path):
    """compress_snapshot creates a zip with the right name and content."""
    # Build a small directory tree to compress
    snap = tmp_path / "openclaw.bak.20260404"
    snap.mkdir()
    (snap / "a.txt").write_text("hello world")  # 11 bytes
    sub = snap / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("zippy")  # 5 bytes

    ok, msg = compress_snapshot(str(snap), remove_original=False)
    assert ok is True
    assert "20260404" in msg
    assert "reduction" in msg

    zip_path = tmp_path / "openclaw.bak.20260404.zip"
    assert zip_path.exists()

    # Verify zip contents (arcname is relative to the snapshot directory)
    with zipfile.ZipFile(str(zip_path), "r") as zf:
        names = zf.namelist()
        assert "a.txt" in names
        assert "sub/b.txt" in names

    # Original directory must still exist
    assert snap.exists()


def test_compress_snapshot_remove_original(tmp_path):
    """compress_snapshot removes the original directory when remove_original=True."""
    snap = tmp_path / "openclaw.bak.20260404"
    snap.mkdir()
    (snap / "file.txt").write_text("content")

    ok, msg = compress_snapshot(str(snap), remove_original=True)
    assert ok is True
    assert "reduction" in msg
    assert not snap.exists(), "Original directory should be deleted"

    zip_path = tmp_path / "openclaw.bak.20260404.zip"
    assert zip_path.exists()


def test_compress_snapshot_nonexistent_path(tmp_path):
    """compress_snapshot returns False without crashing when path doesn't exist."""
    nonexistent = tmp_path / "does_not_exist"
    ok, msg = compress_snapshot(str(nonexistent), remove_original=False)
    assert ok is False
    assert "Compression failed" in msg
    # No partial zip should be left behind
    assert not (tmp_path / "does_not_exist.zip").exists()
