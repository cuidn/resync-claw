"""
Tests for resync_claw.resync
"""

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from resync_claw.resync import (
    is_safe_relative_path,
    resync_full,
    resync_file,
    count_snapshot,
)


def test_resync_full_smoke(tmp_path):
    # Create a source snapshot dir
    snap = os.path.join(str(tmp_path), "openclaw.bak.20260331")
    os.mkdir(snap)
    with open(os.path.join(snap, "file.txt"), "w") as f:
        f.write("content")

    target = os.path.join(str(tmp_path), "restored")
    success, msg = resync_full("openclaw.bak.20260331", target, dest_parent=str(tmp_path))
    assert success
    assert os.path.isdir(target)
    assert os.path.exists(os.path.join(target, "file.txt"))


def test_resync_full_not_found(tmp_path):
    success, msg = resync_full("openclaw.bak.99999999", "/tmp/target", dest_parent=str(tmp_path))
    assert not success
    assert "not found" in msg.lower()


def test_resync_full_no_force_overwrite(tmp_path):
    snap = os.path.join(str(tmp_path), "openclaw.bak.20260331")
    os.mkdir(snap)
    target = os.path.join(str(tmp_path), "target")
    os.makedirs(target)

    success, msg = resync_full("openclaw.bak.20260331", target, dest_parent=str(tmp_path), force=False)
    assert not success
    assert "already exists" in msg


def test_resync_full_force_overwrite(tmp_path):
    snap = os.path.join(str(tmp_path), "openclaw.bak.20260331")
    os.mkdir(snap)
    with open(os.path.join(snap, "new.txt"), "w") as f:
        f.write("new")

    target = os.path.join(str(tmp_path), "target")
    os.makedirs(target)
    with open(os.path.join(target, "old.txt"), "w") as f:
        f.write("old")

    success, msg = resync_full("openclaw.bak.20260331", target, dest_parent=str(tmp_path), force=True)
    assert success
    assert os.path.exists(os.path.join(target, "new.txt"))


def test_resync_file_single_file(tmp_path):
    snap = os.path.join(str(tmp_path), "openclaw.bak.20260331")
    os.makedirs(os.path.join(snap, "workspace-coding"))
    with open(os.path.join(snap, "workspace-coding", "AGENTS.md"), "w") as f:
        f.write("# Test")

    target = os.path.join(str(tmp_path), "ag.md")
    success, msg = resync_file(
        "openclaw.bak.20260331",
        "workspace-coding/AGENTS.md",
        target,
        dest_parent=str(tmp_path),
    )
    assert success
    assert os.path.exists(target)


def test_resync_file_blocked_traversal(tmp_path):
    snap = os.path.join(str(tmp_path), "openclaw.bak.20260331")
    os.mkdir(snap)
    success, msg = resync_file(
        "openclaw.bak.20260331",
        "../../../etc/passwd",
        "/tmp/out",
        dest_parent=str(tmp_path),
    )
    assert not success
    assert "traversal" in msg.lower()


def test_resync_file_not_found_in_snapshot(tmp_path):
    snap = os.path.join(str(tmp_path), "openclaw.bak.20260331")
    os.mkdir(snap)
    success, msg = resync_file(
        "openclaw.bak.20260331",
        "nonexistent/file.txt",
        "/tmp/out",
        dest_parent=str(tmp_path),
    )
    assert not success
    assert "not found" in msg.lower()
