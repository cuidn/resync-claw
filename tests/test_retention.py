"""
Tests for resync_claw.retention
"""

import os
import tempfile
import shutil
from datetime import datetime, timedelta

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from resync_claw.retention import (
    parse_snapshot_date,
    list_snapshots,
    enforce_retention,
    format_size,
    count_snapshot,
    SNAPSHOT_PREFIX,
)


def test_parse_snapshot_date_valid():
    dt = parse_snapshot_date("openclaw.bak.20260331")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 3
    assert dt.day == 31


def test_parse_snapshot_date_invalid():
    assert parse_snapshot_date("openclaw.bak.2026131") is None   # invalid month
    assert parse_snapshot_date("openclaw.bak.2026033") is None    # too short
    assert parse_snapshot_date("other.bak.20260331") is None        # wrong prefix
    assert parse_snapshot_date("notasnapshot") is None


def test_list_snapshots_empty(tmp_path):
    snaps = list_snapshots(str(tmp_path))
    assert snaps == []


def test_list_snapshots_sorted(tmp_path):
    # Create 5 fake snapshots
    for i, days_ago in enumerate([0, 2, 5, 10, 15]):
        d = datetime.now() - timedelta(days=days_ago)
        name = SNAPSHOT_PREFIX + d.strftime("%Y%m%d")
        os.mkdir(os.path.join(str(tmp_path), name))
        # Put one file in each so count works
        with open(os.path.join(str(tmp_path), name, "marker.txt"), "w") as f:
            f.write(f"file {i}")

    snaps = list_snapshots(str(tmp_path))
    assert len(snaps) == 5
    # Should be sorted newest first
    assert snaps[0]["name"] == SNAPSHOT_PREFIX + datetime.now().strftime("%Y%m%d")
    assert snaps[-1]["name"] == SNAPSHOT_PREFIX + (datetime.now() - timedelta(days=15)).strftime("%Y%m%d")
    # All should have file_count >= 1
    for s in snaps:
        assert s["file_count"] >= 1


def test_enforce_retention_keep_3(tmp_path):
    # Create 5 snapshots
    for i, days_ago in enumerate([0, 2, 4, 6, 8]):
        d = datetime.now() - timedelta(days=days_ago)
        name = SNAPSHOT_PREFIX + d.strftime("%Y%m%d")
        os.mkdir(os.path.join(str(tmp_path), name))
        with open(os.path.join(str(tmp_path), name, "f.txt"), "w") as f:
            f.write("x")

    deleted = enforce_retention(str(tmp_path), keep=3)

    assert len(deleted) == 2
    remaining = list_snapshots(str(tmp_path))
    assert len(remaining) == 3


def test_enforce_retention_keeps_all_when_under_limit(tmp_path):
    for i, days_ago in enumerate([0, 2]):
        d = datetime.now() - timedelta(days=days_ago)
        name = SNAPSHOT_PREFIX + d.strftime("%Y%m%d")
        os.mkdir(os.path.join(str(tmp_path), name))

    deleted = enforce_retention(str(tmp_path), keep=3)
    assert deleted == []
    assert len(list_snapshots(str(tmp_path))) == 2


def test_format_size():
    assert format_size(500) == "500.0B"
    assert format_size(1024) == "1.0KB"
    assert format_size(1024 * 1024) == "1.0MB"
    assert format_size(1024 * 1024 * 1024) == "1.0GB"


def test_count_snapshot(tmp_path):
    # Create a small tree
    os.mkdir(os.path.join(str(tmp_path), "sub"))
    with open(os.path.join(str(tmp_path), "a.txt"), "w") as f:
        f.write("a" * 100)
    with open(os.path.join(str(tmp_path), "sub", "b.txt"), "w") as f:
        f.write("bb")

    files, size = count_snapshot(str(tmp_path))
    assert files == 2
    assert size == 102
