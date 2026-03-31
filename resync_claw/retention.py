"""
Retention management for resync-claw.
Lists snapshots, enforces the keep-last-N policy.
"""

import os
import re
import shutil
import logging
from datetime import datetime
from typing import List, Optional

from .backup import SNAPSHOT_PREFIX, DEFAULT_DEST_PARENT

logger = logging.getLogger("resync_claw.retention")

SNAPSHOT_RE = re.compile(r"^" + re.escape(SNAPSHOT_PREFIX) + r"(\d{8})$")


def parse_snapshot_date(name: str) -> Optional[datetime]:
    """Parse YYYYMMDD from snapshot name, return datetime object or None."""
    m = SNAPSHOT_RE.match(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d")
    except ValueError:
        return None


def list_snapshots(dest_parent: str = DEFAULT_DEST_PARENT) -> List[dict]:
    """
    Return all snapshots in dest_parent sorted newest-first.
    Each entry: {name, date, size_bytes, file_count}
    """
    if not os.path.isdir(dest_parent):
        return []

    snapshots = []
    for entry in os.scandir(dest_parent):
        if not entry.is_dir():
            continue
        dt = parse_snapshot_date(entry.name)
        if dt is None:
            continue
        snapshots.append(entry.name)

    # Sort newest first
    snapshots.sort(key=lambda n: parse_snapshot_date(n), reverse=True)

    result = []
    for name in snapshots:
        snap_path = os.path.join(dest_parent, name)
        try:
            file_count, size_bytes = count_snapshot(snap_path)
        except Exception:
            file_count, size_bytes = 0, 0
        result.append({
            "name": name,
            "date": parse_snapshot_date(name).strftime("%Y-%m-%d"),
            "size_bytes": size_bytes,
            "file_count": file_count,
        })

    return result


def count_snapshot(snap_path: str) -> tuple[int, int]:
    """Count files and total bytes in a snapshot directory."""
    total_files = 0
    total_bytes = 0
    for root, dirs, files in os.walk(snap_path):
        for f in files:
            fp = os.path.join(root, f)
            try:
                total_bytes += os.path.getsize(fp)
                total_files += 1
            except OSError:
                pass
    return total_files, total_bytes


def enforce_retention(dest_parent: str = DEFAULT_DEST_PARENT, keep: int = 3) -> List[str]:
    """
    Enforce retention policy: keep the newest `keep` snapshots, delete the rest.

    Returns list of deleted snapshot names.
    """
    snapshots = list_snapshots(dest_parent)
    if len(snapshots) <= keep:
        logger.info("Retention: %d snapshots, keeping all (limit=%d)", len(snapshots), keep)
        return []

    to_delete = snapshots[keep:]  # oldest after the first `keep` newest
    deleted = []

    for snap in to_delete:
        snap_path = os.path.join(dest_parent, snap["name"])
        logger.info("Retention: removing old snapshot %s", snap["name"])
        try:
            shutil.rmtree(snap_path)
            deleted.append(snap["name"])
        except OSError as exc:
            logger.error("Failed to remove %s: %s", snap["name"], exc)

    return deleted


def format_size(size_bytes: int) -> str:
    """Human-readable size."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.1f}{unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f}PB"
