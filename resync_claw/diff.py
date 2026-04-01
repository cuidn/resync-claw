"""
Compare two backup snapshots and report differences.
"""

import os
import subprocess
import logging

from .backup import DEFAULT_DEST_PARENT, RSYNC_EXCLUDES

logger = logging.getLogger("resync_claw.diff")


def build_rsync_exclude_args() -> list[str]:
    """Build rsync --exclude arguments from RSYNC_EXCLUDES."""
    args = []
    for pattern in RSYNC_EXCLUDES:
        args.extend(["--exclude", pattern])
    return args


def compare_snapshots(
    dest_parent: str,
    snap_old: str,
    snap_new: str,
) -> tuple[list[str], list[str]]:
    """
    Compare two snapshots and return (changed, deleted) file lists.

    changed:  files that differ (new or modified) — present in snap_new, absent or different in snap_old
    deleted:  files absent in snap_new — present in snap_old, gone in snap_new
    """
    snap_old_path = os.path.join(dest_parent, snap_old)
    snap_new_path = os.path.join(dest_parent, snap_new)

    if not os.path.isdir(snap_old_path):
        raise FileNotFoundError(f"Snapshot not found: {snap_old}")
    if not os.path.isdir(snap_new_path):
        raise FileNotFoundError(f"Snapshot not found: {snap_new}")

    exclude_args = build_rsync_exclude_args()

    # --- Files added or modified in snap_new vs snap_old ---
    # rsync -n (dry-run) --itemize-changes newsnap/ oldsnap/
    # Items that rsync would COPY from newsnap -> oldsnap are those that exist in
    # newsnap but NOT in oldsnap, or exist in both but differ.
    # -c (checksum) ensures content changes are detected even if size/mtime are same
    added_mod_cmd = [
        "rsync", "-n", "--itemize-changes", "-a", "-c",
        snap_new_path + "/",
        snap_old_path + "/",
    ] + exclude_args

    changed: list[str] = []

    result_am = subprocess.run(
        added_mod_cmd,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result_am.returncode not in (0, 23, 24):
        # 23= vanished files, 24= vanished source — still valid output
        raise RuntimeError(f"rsync compare failed: {result_am.stderr}")

    for line in result_am.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split(maxsplit=1)
        if len(parts) < 2:
            continue
        flags = parts[0]
        path = parts[1]
        if not flags:
            continue
        op = flags[0]
        # Skip hard links and symlinks
        if "h" in flags or "L" in flags:
            continue
        if op == ">":
            # File being copied from new -> old: exists in new, absent or different in old
            if "d" in flags[1:]:
                continue  # skip directories
            changed.append(path)
        elif op == "<":
            # Receiver has file (shouldn't appear in sender output, skip)
            continue

    # --- Files deleted in snap_new (present in snap_old, gone) ---
    # Run rsync the other way: files that exist in snap_old but NOT in snap_new
    # (without --delete, >f means "exists in source but not in dest")
    deleted_cmd = [
        "rsync", "-n", "--itemize-changes", "-a",
        snap_old_path + "/",
        snap_new_path + "/",
    ] + exclude_args

    deleted: list[str] = []

    result_del = subprocess.run(
        deleted_cmd,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result_del.returncode not in (0, 23, 24):
        raise RuntimeError(f"rsync compare failed: {result_del.stderr}")

    for line in result_del.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split(maxsplit=1)
        if len(parts) < 2:
            continue
        flags = parts[0]
        path = parts[1]
        if not flags:
            continue
        op = flags[0]
        if "h" in flags or "L" in flags:
            continue
        if op == ">":
            # File being copied from old -> new: exists in old, not in new
            # = deleted from new's perspective
            # Only "+" in flags means file is NEW in dest (not present there)
            # "s" (size) or "c" (checksum) means file exists in both but differs
            if "d" in flags[1:]:
                continue  # skip directories
            if "+" in flags:
                deleted.append(path)

    return changed, deleted


def format_compare_output(
    snap_old: str,
    snap_new: str,
    changed: list[str],
    deleted: list[str],
    verbose: bool = False,
) -> str:
    """Format the comparison result into a human-readable string."""
    lines: list[str] = []
    lines.append(f"Comparing snapshots: {snap_old}  →  {snap_new}")
    lines.append("=" * 72)

    total_changed = len(changed) + len(deleted)

    if total_changed == 0:
        lines.append("No differences — snapshots are identical.")
        return "\n".join(lines)

    limit = 200 if verbose else 50

    if changed:
        lines.append(f"\n  [~ CHANGED]  {len(changed)} file(s)")
        for f in sorted(changed)[:limit]:
            lines.append(f"    {f}")
        if len(changed) > limit:
            lines.append(f"    ... and {len(changed) - limit} more (use --verbose for full list)")

    if deleted:
        lines.append(f"\n  [- DELETED]  {len(deleted)} file(s) removed")
        for f in sorted(deleted)[:limit]:
            lines.append(f"    {f}")
        if len(deleted) > limit:
            lines.append(f"    ... and {len(deleted) - limit} more (use --verbose for full list)")

    lines.append(f"\n{'─' * 72}")
    lines.append(f"Summary: {len(changed)} changed, {len(deleted)} deleted  |  Total: {total_changed} change(s)")

    return "\n".join(lines)
