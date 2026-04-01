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
    show_content_diff: bool = False,
) -> tuple[list[str], list[str], list[str]]:
    """
    Compare two snapshots and return lists of (added, modified, deleted) files.

    added:     files present in snap_new but not in snap_old
    modified:  files present in both but content differs
    deleted:   files present in snap_old but not in snap_new
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
    added_mod_cmd = [
        "rsync", "-n", "--itemize-changes", "-a",
        snap_new_path + "/",
        snap_old_path + "/",
    ] + exclude_args

    added: list[str] = []
    modified: list[str] = []

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
        # Line format: `<flags> filename`
        parts = line.split(maxsplit=1)
        if len(parts) < 2:
            continue
        flags = parts[0]
        path = parts[1]
        # 'h' flag = hard link (skip), 'c' = local change, 'L' = symlink
        if "h" in flags:
            continue
        if flags.startswith("<") or ". .d" in flags or "deleting" in flags:
            continue
        # Item is being copied from new -> old means it exists in new, not old or differs
        if ">" in flags or (flags and flags[0] in ("c", "f", "d", "s", "o", "p", "i")):
            # Check if it's a directory (trailing /) or just a file
            if flags.endswith("/") or "f" in flags or "c" in flags or "s" in flags:
                modified.append(path)
            else:
                added.append(path)

    # --- Files deleted in snap_new (present in snap_old, gone) ---
    # Run rsync the other way: what would be deleted from snap_new to match snap_old
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
        if "h" in flags:
            continue
        # '>' flag means item would be transferred from old -> new (so it exists
        # in old but not in new, i.e. deleted from new's perspective)
        if ">" in flags:
            deleted.append(path)
        elif "deleting" in flags:
            deleted.append(path)

    return added, modified, deleted


def format_compare_output(
    snap_old: str,
    snap_new: str,
    added: list[str],
    modified: list[str],
    deleted: list[str],
) -> str:
    """Format the comparison result into a human-readable string."""
    from .retention import format_size
    from .backup import count_files_and_size, SNAPSHOT_PREFIX

    lines: list[str] = []
    lines.append(f"Comparing snapshots: {snap_old}  →  {snap_new}")
    lines.append("=" * 72)

    total_changed = len(added) + len(modified) + len(deleted)

    if total_changed == 0:
        lines.append("No differences — snapshots are identical.")
        return "\n".join(lines)

    if added:
        lines.append(f"\n  [+ NEW]  {len(added)} file(s) added")
        for f in sorted(added)[:50]:
            lines.append(f"    {f}")
        if len(added) > 50:
            lines.append(f"    ... and {len(added) - 50} more (run with --verbose for full list)")

    if modified:
        lines.append(f"\n  [~ MODIFIED]  {len(modified)} file(s) changed")
        for f in sorted(modified)[:50]:
            lines.append(f"    {f}")
        if len(modified) > 50:
            lines.append(f"    ... and {len(modified) - 50} more")

    if deleted:
        lines.append(f"\n  [- DELETED]  {len(deleted)} file(s) removed")
        for f in sorted(deleted)[:50]:
            lines.append(f"    {f}")
        if len(deleted) > 50:
            lines.append(f"    ... and {len(deleted) - 50} more")

    lines.append(f"\n{'─' * 72}")
    lines.append(f"Summary: {len(added)} added, {len(modified)} modified, {len(deleted)} deleted  |  Total: {total_changed} change(s)")

    return "\n".join(lines)
