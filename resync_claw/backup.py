"""
Backup logic for resync-claw.
Handles rsync invocation, FAT32 resilience, verification.
"""

import os
import subprocess
import time
import random
import logging
import zipfile
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger("resync_claw.backup")

# Hardcoded defaults
DEFAULT_SOURCE = "/home/openclaw/.openclaw"
DEFAULT_DEST_PARENT = "/home/openclaw/ClawBackup"

RSYNC_EXCLUDES = [
    "tmp/",
    ".cache/",
    "logs/",
    "__pycache__/",
    "node_modules/",
    "*.pyc",
    "*.pyo",
    ".DS_Store",
    ".Trash-*",
]

# Snapshot naming prefix
SNAPSHOT_PREFIX = "openclaw.bak."

# Latest marker filename (plaintext, no symlink needed on FAT32)
LATEST_MARKER = "openclaw.bak.latest.txt"


def snapshot_name(today: Optional[date] = None) -> str:
    """Return snapshot directory name for a given date."""
    if today is None:
        today = date.today()
    return f"{SNAPSHOT_PREFIX}{today.strftime('%Y%m%d')}"


def build_rsync_cmd(source: str, dest_snapshot: str, dry_run: bool = False) -> list[str]:
    """Build the rsync command as a list of args."""
    cmd = ["rsync", "-a"]
    for exc in RSYNC_EXCLUDES:
        cmd += ["--exclude", exc]
    # Trailing slash on source means "contents of source", so snapshot dir is created correctly
    cmd += [source.rstrip("/") + "/", dest_snapshot + "/"]
    if dry_run:
        cmd.append("--dry-run")
    return cmd


def dest_for_snapshot(dest_parent: str, name: str) -> str:
    """Return full path to a snapshot directory."""
    return os.path.join(dest_parent, name)


def verify_destination(dest_parent: str) -> bool:
    """Check destination parent is writable."""
    if not os.path.exists(dest_parent):
        logger.error("Destination parent does not exist: %s", dest_parent)
        return False
    if not os.access(dest_parent, os.W_OK):
        logger.error("Destination parent is not writable: %s", dest_parent)
        return False
    return True


def count_files_and_size(path: str, follow_symlinks: bool = False, excludes: list[str] | None = None) -> tuple[int, int]:
    """Count files (non-dir) and total size in bytes for a directory tree.

    By default symlinks are not followed (matching rsync's -a behaviour).
    Set follow_symlinks=True to match `find`'s default behavior.
    Pass excludes (e.g. ["node_modules/", "tmp/"]) to skip directories the same way
    rsync's --exclude patterns would, so counts are comparable.
    """
    total_files = 0
    total_bytes = 0
    for root, dirs, files in os.walk(path, followlinks=follow_symlinks):
        if excludes:
            dirs[:] = [d for d in dirs if not _any_match(d, excludes)]
        for f in files:
            fp = os.path.join(root, f)
            try:
                total_bytes += os.path.getsize(fp)
                total_files += 1
            except OSError:
                pass
    return total_files, total_bytes


def _any_match(name: str, patterns: list[str]) -> bool:
    """Return True if name matches any exclude pattern.

    Handles: exact match (node_modules/), wildcard any-level (*/node_modules/),
    and prefix-match (*.pyc, .Trash-*).
    """
    import fnmatch
    for p in patterns:
        p = p.rstrip("/")
        if "*/" in p:
            # */pattern matches pattern at any directory level
            base = p.replace("*/", "")
            if fnmatch.fnmatch(name, base):
                return True
        elif fnmatch.fnmatch(name, p):
            return True
    return False



def write_verify_marker(snapshot_path: str, src_file_count: int, src_total_bytes: int, status: str = "ok") -> None:
    """Write a .verify_marker file inside the snapshot."""
    marker = os.path.join(snapshot_path, ".verify_marker")
    with open(marker, "w") as f:
        f.write(f"timestamp={int(time.time())}\n")
        f.write(f"src_file_count={src_file_count}\n")
        f.write(f"src_total_bytes={src_total_bytes}\n")
        f.write(f"status={status}\n")


def read_verify_marker(snapshot_path: str) -> Optional[dict]:
    """Read and parse a .verify_marker file."""
    marker = os.path.join(snapshot_path, ".verify_marker")
    if not os.path.exists(marker):
        return None
    result = {}
    try:
        with open(marker) as f:
            for line in f:
                line = line.strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    result[k] = v
        return result
    except OSError:
        return None


def run_backup(
    source: str = DEFAULT_SOURCE,
    dest_parent: str = DEFAULT_DEST_PARENT,
    dry_run: bool = False,
    force: bool = False,
    timeout_secs: int = 3600,
    max_retries: int = 2,
    compress: bool = False,
    remove_original: bool = False,
) -> tuple[bool, str]:
    """
    Run the backup from source to dest_parent/openclaw.bak.YYYYMMDD/.

    If compress is True, the snapshot is zipped after a successful backup
    (using zipfile.ZIP_DEFLATED). If remove_original is also True, the
    uncompressed directory is deleted after zipping. Compression errors are
    logged as warnings but do not block the success return.

    Returns (success: bool, message: str).
    """
    if not os.path.exists(source):
        return False, f"ERROR: Source does not exist: {source}"

    if not verify_destination(dest_parent):
        return False, f"ERROR: Destination not writable: {dest_parent}"

    snap_name = snapshot_name()
    dest_snap = dest_for_snapshot(dest_parent, snap_name)

    if os.path.exists(dest_snap) and not force:
        return False, f"ERROR: Snapshot already exists (use --force to overwrite): {dest_snap}"

    # Build rsync command
    rsync_cmd = build_rsync_cmd(source, dest_snap, dry_run=dry_run)

    if dry_run:
        # Just show the command
        return True, "DRY-RUN: " + " ".join(rsync_cmd)

    # Remove incomplete snapshot if it exists (from a failed prior attempt)
    if os.path.exists(dest_snap):
        logger.warning("Removing incomplete snapshot: %s", dest_snap)
        shutil_rmtree(dest_snap)

    # Retry loop
    last_error = ""
    for attempt in range(max_retries + 1):
        try:
            proc = subprocess.run(
                rsync_cmd,
                timeout=timeout_secs,
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired:
            last_error = f"ERROR: rsync timed out after {timeout_secs}s"
            logger.error(last_error)
            # Clean up partial snapshot
            if os.path.exists(dest_snap):
                shutil_rmtree(dest_snap)
            if attempt < max_retries:
                wait = 30 * (attempt + 1)
                logger.info("Retrying in %ds (attempt %d/%d)", wait, attempt + 1, max_retries)
                time.sleep(wait)
            continue
        except Exception as exc:
            last_error = f"ERROR: rsync process error: {exc}"
            logger.error(last_error)
            if os.path.exists(dest_snap):
                shutil_rmtree(dest_snap)
            if attempt < max_retries:
                wait = 30 * (attempt + 1)
                time.sleep(wait)
            continue

        if proc.returncode == 0:
            break
        else:
            last_error = f"ERROR: rsync failed (exit {proc.returncode}): {proc.stderr}"
            logger.error(last_error)
            if os.path.exists(dest_snap):
                shutil_rmtree(dest_snap)
            if attempt < max_retries:
                wait = 30 * (attempt + 1)
                logger.info("Retrying in %ds (attempt %d/%d)", wait, attempt + 1, max_retries)
                time.sleep(wait)
            continue

    else:
        # All retries exhausted
        return False, f"ERROR: rsync failed after {max_retries + 1} attempts. Last error: {last_error}"

    # --- Verification ---
    # Count source and destination with the same exclude patterns rsync uses
    # so the counts are comparable (both skip node_modules/, tmp/, etc.)
    src_files, src_bytes = count_files_and_size(source, excludes=RSYNC_EXCLUDES)
    dst_files, dst_bytes = count_files_and_size(dest_snap)

    logger.info("Backup complete. src: %d files / %d bytes  |  dst: %d files / %d bytes",
                src_files, src_bytes, dst_files, dst_bytes)

    # Allow small discrepancies (e.g. 1-2 files) due to timing between rsync
    # finishing and the count being taken (new marker files, etc.)
    if abs(src_files - dst_files) > 5:
        msg = f"WARNING: File count mismatch — src={src_files}, dst={dst_files}"
        logger.warning(msg)
        # Write a failed verify marker but don't abort
        write_verify_marker(dest_snap, src_files, src_bytes, status="fail")
        return True, msg

    # Write verify marker + update latest marker
    write_verify_marker(dest_snap, src_files, src_bytes)
    write_latest_marker(dest_parent, snap_name)

    # Compression (non-blocking — warns on failure but does not fail the backup)
    if compress:
        ok, msg = compress_snapshot(dest_snap, remove_original=remove_original)
        if ok:
            logger.info("Compression: %s", msg)
        else:
            logger.warning("Compression skipped: %s", msg)

    return True, f"Backup complete: {snap_name} ({dst_files} files, {dst_bytes} bytes)"


def write_latest_marker(dest_parent: str, snap_name: str) -> None:
    """Write the latest snapshot name to a plain text marker file."""
    marker_path = os.path.join(dest_parent, LATEST_MARKER)
    try:
        with open(marker_path, "w") as f:
            f.write(snap_name + "\n")
    except OSError as exc:
        logger.warning("Could not write latest marker: %s", exc)


def get_latest_marker(dest_parent: str) -> Optional[str]:
    """Read the latest snapshot name from the plain text marker file."""
    marker_path = os.path.join(dest_parent, LATEST_MARKER)
    try:
        with open(marker_path) as f:
            return f.read().strip()
    except OSError:
        return None


def shutil_rmtree(path: str) -> None:
    """Remove a directory tree, retrying on minor failures."""
    import shutil
    for attempt in range(3):
        try:
            shutil.rmtree(path)
            return
        except OSError as exc:
            logger.warning("rmtree attempt %d failed: %s", attempt + 1, exc)
            time.sleep(2)
    # Last resort
    subprocess.run(["rm", "-rf", path], capture_output=True)


def compress_snapshot(snapshot_path: str, remove_original: bool = False) -> tuple[bool, str]:
    """
    Compress a snapshot directory into a zip archive.

    Returns (success: bool, message: str).
    If the directory is empty or missing, returns (False, ...) without creating a zip.
    Other compression failures log a warning but leave the original intact.
    """
    if not os.path.isdir(snapshot_path):
        return False, f"Compression failed: snapshot directory not found: {snapshot_path}"

    snap_name = os.path.basename(snapshot_path)
    zip_name = snap_name + ".zip"
    zip_path = os.path.join(os.path.dirname(snapshot_path), zip_name)

    # Compute original size before zipping
    _, orig_bytes = count_files_and_size(snapshot_path)

    if orig_bytes == 0:
        return False, f"Compression failed: snapshot is empty: {snapshot_path}"

    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(snapshot_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, snapshot_path)
                    zf.write(file_path, arcname)

        zip_bytes = os.path.getsize(zip_path)
        ratio = (1 - zip_bytes / orig_bytes) * 100 if orig_bytes > 0 else 0
        logger.info("Compressed %s: %d bytes -> %d bytes (%.1f%% reduction, saved %d bytes)",
                    snap_name, orig_bytes, zip_bytes, ratio, orig_bytes - zip_bytes)

        if remove_original:
            shutil_rmtree(snapshot_path)
            logger.info("Removed original snapshot directory: %s", snap_name)

        return True, f"Compressed {snap_name}: {orig_bytes} bytes -> {zip_bytes} bytes ({ratio:.1f}% reduction)"

    except Exception as exc:
        logger.warning("Compression failed for %s: %s", snap_name, exc)
        # Clean up partial zip if it exists
        if os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except OSError:
                pass
        return False, f"Compression failed: {exc}"
