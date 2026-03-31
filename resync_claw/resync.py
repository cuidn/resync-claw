"""
Restore logic for resync-claw.
Supports full snapshot restore and single-file/directory restore.
"""

import os
import shutil
import logging
from pathlib import Path

from .backup import DEFAULT_DEST_PARENT, SNAPSHOT_PREFIX
from .retention import parse_snapshot_date

logger = logging.getLogger("resync_claw.resync")


def snapshot_exists(dest_parent: str, snap_name: str) -> bool:
    """Check if a snapshot directory exists."""
    if not snap_name.startswith(SNAPSHOT_PREFIX) or not snap_name.endswith("/"):
        # Ensure it looks like a snapshot name
        pass
    snap_path = os.path.join(dest_parent, snap_name)
    return os.path.isdir(snap_path)


def is_safe_relative_path(path: str) -> bool:
    """
    Return True if path is safe (no path traversal).
    Disallows: absolute paths, paths containing .., paths starting with /
    """
    normalized = os.path.normpath(path)
    # Must not escape the restore root
    if normalized.startswith(".."):
        return False
    if os.path.isabs(normalized):
        return False
    return True


def resync_full(
    snap_name: str,
    target_path: str,
    dest_parent: str = DEFAULT_DEST_PARENT,
    force: bool = False,
) -> tuple[bool, str]:
    """
    Resync an entire snapshot to target_path.

    Returns (success, message).
    """
    snap_path = os.path.join(dest_parent, snap_name)
    if not os.path.isdir(snap_path):
        return False, f"ERROR: Snapshot not found: {snap_name}"

    if os.path.exists(target_path) and not force:
        return False, f"ERROR: Target path already exists (use --force to overwrite): {target_path}"

    # Create parent dir if needed
    target_parent = os.path.dirname(target_path.rstrip("/"))
    if target_parent and not os.path.exists(target_parent):
        os.makedirs(target_parent, exist_ok=True)

    # Copy tree
    try:
        if os.path.exists(target_path):
            shutil.rmtree(target_path)
        shutil.copytree(snap_path, target_path)
    except Exception as exc:
        return False, f"ERROR: Copy failed: {exc}"

    # Verify
    src_files, _ = count_snapshot(snap_path)
    dst_files, _ = count_snapshot(target_path)
    if src_files != dst_files:
        return True, f"WARNING: File count mismatch — src={src_files}, dst={dst_files}"

    return True, f"Full resync complete: {snap_name} → {target_path} ({dst_files} files)"


def resync_file(
    snap_name: str,
    relative_path: str,
    target_path: str,
    dest_parent: str = DEFAULT_DEST_PARENT,
    force: bool = False,
) -> tuple[bool, str]:
    """
    Resync a single file or directory from a snapshot.

    relative_path: path relative to snapshot root (e.g., "workspace-coding/AGENTS.md")
    target_path: destination path for the resynced file/dir

    Returns (success, message).
    """
    if not is_safe_relative_path(relative_path):
        return False, "ERROR: Invalid relative path (path traversal detected): " + relative_path

    snap_path = os.path.join(dest_parent, snap_name)
    if not os.path.isdir(snap_path):
        return False, f"ERROR: Snapshot not found: {snap_name}"

    src_item = os.path.join(snap_path, relative_path)
    if not os.path.exists(src_item):
        return False, f"ERROR: Path not found in snapshot: {relative_path}"

    if os.path.exists(target_path) and not force:
        return False, f"ERROR: Target path already exists (use --force to overwrite): {target_path}"

    # Create parent dir of target if needed
    target_parent = os.path.dirname(target_path.rstrip("/"))
    if target_parent and not os.path.exists(target_parent):
        os.makedirs(target_parent, exist_ok=True)

    try:
        if os.path.isdir(src_item):
            if os.path.exists(target_path):
                shutil.rmtree(target_path)
            shutil.copytree(src_item, target_path)
        else:
            if os.path.exists(target_path):
                os.remove(target_path)
            shutil.copy2(src_item, target_path)
    except Exception as exc:
        return False, f"ERROR: Restore failed: {exc}"

    return True, f"Resynced {relative_path} → {target_path}"


def count_snapshot(path: str) -> tuple[int, int]:
    """Count files and total bytes in a directory tree."""
    total_files = 0
    total_bytes = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            try:
                total_bytes += os.path.getsize(fp)
                total_files += 1
            except OSError:
                pass
    return total_files, total_bytes
