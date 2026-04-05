"""
Compare two backup snapshots and report differences.
Supports both plain directory snapshots and compressed .zip snapshots.
"""

import logging
import os
import shutil
import subprocess
import tempfile
import zipfile

from .backup import DEFAULT_DEST_PARENT, RSYNC_EXCLUDES

logger = logging.getLogger("resync_claw.diff")

# ----------------------------------------------------------------------
# Snapshot type detection
# ----------------------------------------------------------------------


def is_zip_snapshot(snap_path: str) -> bool:
    """Return True if the snapshot path is a .zip archive (not a directory)."""
    return os.path.isfile(snap_path) and snap_path.endswith(".zip")


# ----------------------------------------------------------------------
# Zip extraction helpers
# ----------------------------------------------------------------------


def _safe_extract(zip_path: str, dest_dir: str) -> None:
    """
    Extract a zip file to dest_dir safely.

    Raises ValueError if any entry would traverse outside dest_dir (path traversal).
    Raises zipfile.BadZipFile if the zip is corrupt.
    """
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            member_path = os.path.normpath(os.path.join(dest_dir, member.filename))
            # Ensure the extracted path stays within dest_dir
            if not member_path.startswith(dest_dir + os.sep) and member_path != dest_dir:
                raise ValueError(f"Unsafe zip entry (path traversal attempt): {member.filename!r}")
        # Extract all in one go — members were validated above
        zf.extractall(dest_dir)


def _extract_to_temp(zip_path: str) -> str:
    """
    Extract a zip snapshot to a temporary directory.

    Returns the path to the temporary directory. The caller is responsible
    for deleting it. Uses a named temp dir so cleanup is easier to verify.

    Raises RuntimeError on corruption or disk I/O errors.
    """
    tmp_dir = tempfile.mkdtemp(prefix="resync-claw-compare-")
    try:
        _safe_extract(zip_path, tmp_dir)
        return tmp_dir
    except zipfile.BadZipFile as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError(f"Corrupt zip archive: {os.path.basename(zip_path)}") from exc
    except ValueError as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError(f"Invalid zip entry in {os.path.basename(zip_path)}: {exc}") from exc
    except OSError as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError(f"Disk I/O error extracting {os.path.basename(zip_path)}: {exc}") from exc


# ----------------------------------------------------------------------
# Snapshot path resolution
# ----------------------------------------------------------------------


def _resolve_snap(dest_parent: str, snap_name: str) -> str:
    """
    Return the full path to a snapshot, whether it's a directory or a .zip file.

    Checks for both forms: dest_parent/snap_name/ (dir) and dest_parent/snap_name.zip (zip).
    If neither exists, raises FileNotFoundError.
    """
    dir_path = os.path.join(dest_parent, snap_name)
    zip_path = dir_path + ".zip"

    if os.path.isdir(dir_path):
        return dir_path
    elif os.path.isfile(zip_path):
        return zip_path
    else:
        raise FileNotFoundError(f"Snapshot not found (not a directory or zip): {snap_name}")


# ----------------------------------------------------------------------
# Comparison dispatch
# ----------------------------------------------------------------------


def compare_snapshots(
    dest_parent: str,
    snap_old: str,
    snap_new: str,
) -> tuple[list[str], list[str]]:
    """
    Compare two snapshots and return (changed, deleted) file lists.

    changed:  files that differ (new or modified) — present in snap_new, absent or different in snap_old
    deleted:  files absent in snap_new — present in snap_old, gone in snap_new

    Supports both plain directory snapshots and compressed .zip snapshots.
    Automatically detects the type of each snapshot and routes accordingly.
    """
    snap_old_path = _resolve_snap(dest_parent, snap_old)
    snap_new_path = _resolve_snap(dest_parent, snap_new)

    old_zip = is_zip_snapshot(snap_old_path)
    new_zip = is_zip_snapshot(snap_new_path)

    if old_zip and new_zip:
        return _compare_zip_to_zip(snap_old_path, snap_new_path)
    elif old_zip and not new_zip:
        return _compare_zip_to_dir(snap_old_path, snap_new_path)
    elif not old_zip and new_zip:
        return _compare_zip_to_dir(snap_new_path, snap_old_path)  # reversed: zip is snap_new, dir is snap_old
    else:
        return _compare_dirs(snap_old_path, snap_new_path)


def compare_zips(
    snap_old_zip: str,
    snap_new_zip: str,
    verbose: bool = False,
) -> tuple[list[str], list[str]]:
    """
    Compare two zip archives by extracting them to temp directories and running rsync.

    Returns (changed, deleted) file lists.

    Raises FileNotFoundError if either zip file does not exist.
    Raises RuntimeError if extraction or comparison fails.
    """
    logger.info(" Comparing compressed archives: %s, %s", snap_old_zip, snap_new_zip)
    return _compare_zip_to_zip(snap_old_zip, snap_new_zip)


def _compare_dirs(snap_old_path: str, snap_new_path: str) -> tuple[list[str], list[str]]:
    """Compare two directory snapshots using rsync."""
    return _rsync_compare(snap_old_path, snap_new_path)


def _compare_zip_to_zip(zip_old: str, zip_new: str) -> tuple[list[str], list[str]]:
    """
    Compare two zip snapshots.

    Extracts both zips to temporary directories, then delegates to rsync comparison.
    Temp directories are cleaned up via try/finally.
    """
    tmp_old = None
    tmp_new = None
    try:
        logger.info("Extracting zip snapshot: %s", os.path.basename(zip_old))
        tmp_old = _extract_to_temp(zip_old)
        logger.info("Extracting zip snapshot: %s", os.path.basename(zip_new))
        tmp_new = _extract_to_temp(zip_new)
        return _rsync_compare(tmp_old, tmp_new)
    finally:
        if tmp_old:
            shutil.rmtree(tmp_old, ignore_errors=True)
        if tmp_new:
            shutil.rmtree(tmp_new, ignore_errors=True)


def _compare_zip_to_dir(zip_path: str, dir_path: str) -> tuple[list[str], list[str]]:
    """
    Compare a zip snapshot to a directory snapshot.

    Extracts the zip to a temporary directory, then delegates to rsync comparison.
    Temp directory is cleaned up via try/finally.
    """
    tmp_dir = None
    try:
        logger.info("Extracting zip snapshot: %s", os.path.basename(zip_path))
        tmp_dir = _extract_to_temp(zip_path)
        return _rsync_compare(dir_path, tmp_dir)
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ----------------------------------------------------------------------
# Core rsync comparison
# ----------------------------------------------------------------------


def build_rsync_exclude_args() -> list[str]:
    """Build rsync --exclude arguments from RSYNC_EXCLUDES."""
    args = []
    for pattern in RSYNC_EXCLUDES:
        args.extend(["--exclude", pattern])
    return args


def _rsync_compare(snap_old_path: str, snap_new_path: str) -> tuple[list[str], list[str]]:
    """
    Core rsync-based comparison of two directory trees.

    Returns (changed, deleted) file lists.
    """
    exclude_args = build_rsync_exclude_args()

    # --- Files added or modified in snap_new vs snap_old ---
    # rsync -n (dry-run) --itemize-changes newsnap/ oldsnap/
    # Items that rsync would COPY from newsnap -> oldsnap are those that exist in
    # newsnap but NOT in oldsnap, or exist in both but differ.
    # -c (checksum) ensures content changes are detected even if size/mtime are same
    added_mod_cmd = [
        "rsync", "-n", "--itemize-changes", "-a", "-c",
        snap_new_path.rstrip("/") + "/",
        snap_old_path.rstrip("/") + "/",
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
        snap_old_path.rstrip("/") + "/",
        snap_new_path.rstrip("/") + "/",
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


# ----------------------------------------------------------------------
# Output formatting
# ----------------------------------------------------------------------


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
