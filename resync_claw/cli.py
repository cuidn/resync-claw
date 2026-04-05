"""
resync-claw CLI.

Usage:
    resync-claw run [--dry-run] [--force] [--dest <path>] [--source <path>]
    resync-claw status
    resync-claw list
    resync-claw verify <backup_name>
    resync-claw restore --full <backup_name> <target_path> [--force]
    resync-claw restore --file <backup_name> <relative_path> <target_path> [--force]
    resync-claw compare <backup_old> <backup_new> [--verbose]
    resync-claw install-cron
    resync-claw remove-cron
    resync-claw --help
"""

import argparse
import logging
import subprocess
import sys
import os

from .backup import (
    DEFAULT_DEST_PARENT,
    DEFAULT_SOURCE,
    run_backup,
    verify_destination,
    get_latest_marker,
    count_files_and_size,
    read_verify_marker,
)
from .retention import list_snapshots, enforce_retention, format_size, count_snapshot
from .resync import resync_full, resync_file, snapshot_exists
from .diff import format_compare_output

# ----------------------------------------------------------------------
# Logging setup
# ----------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)
logger = logging.getLogger("resync_claw")


# ----------------------------------------------------------------------
# Cron helpers
# ----------------------------------------------------------------------
CRON_SCHEDULE = "0 4 * * *"  # every day at 04:00
CRON_JOB = (
    f"{CRON_SCHEDULE} "
    f"$HOME/.local/bin/resync-claw run --force "
    f">>$HOME/.openclaw/logs/backup-cron.log 2>&1"
)


def install_cron() -> tuple[bool, str]:
    """Install the cron job for the openclaw user."""
    log_dir = os.path.join(os.path.expanduser("~"), ".openclaw", "logs")
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    try:
        # Get existing crontab, remove any existing resync-claw lines, add new one
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        existing = result.stdout if result.returncode == 0 else ""
    except Exception as exc:
        return False, f"ERROR: Could not read crontab: {exc}"

    lines = [l for l in existing.strip().split("\n") if "resync-claw" not in l]
    lines.append(CRON_JOB)
    new_crontab = "\n".join(lines) + "\n"

    try:
        proc = subprocess.run(
            ["crontab", "-"],
            input=new_crontab,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            return False, f"ERROR: crontab install failed: {proc.stderr}"
        return True, f"Cron installed: {CRON_SCHEDULE}"
    except Exception as exc:
        return False, f"ERROR: Could not write crontab: {exc}"


def remove_cron() -> tuple[bool, str]:
    """Remove the resync-claw cron job."""
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        existing = result.stdout if result.returncode == 0 else ""
    except Exception as exc:
        return False, f"ERROR: Could not read crontab: {exc}"

    lines = [l for l in existing.strip().split("\n") if "resync-claw" not in l]
    new_crontab = "\n".join(lines).strip() + "\n"

    try:
        proc = subprocess.run(
            ["crontab", "-"],
            input=new_crontab,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            return False, f"ERROR: crontab removal failed: {proc.stderr}"
        return True, "Cron job removed"
    except Exception as exc:
        return False, f"ERROR: Could not write crontab: {exc}"


# ----------------------------------------------------------------------
# CLI commands
# ----------------------------------------------------------------------

def cmd_run(args) -> int:
    # Resolve compress: CLI flag > env var > default (False)
    compress = getattr(args, "compress", False)
    if not compress:
        compress = os.environ.get("RESYNC_COMPRESS", "false").lower() in ("true", "1", "yes")
    remove_original = getattr(args, "remove_original", False)

    success, msg = run_backup(
        source=args.source or DEFAULT_SOURCE,
        dest_parent=args.dest or DEFAULT_DEST_PARENT,
        dry_run=args.dry_run,
        force=args.force,
        compress=compress,
        remove_original=remove_original,
    )
    print(msg)
    if not success:
        return 1

    # After successful backup, enforce retention
    if not args.dry_run:
        deleted = enforce_retention(dest_parent=args.dest or DEFAULT_DEST_PARENT, keep=7)
        if deleted:
            print(f"Retention cleanup: removed {len(deleted)} old snapshot(s)")
    return 0


def cmd_status(args) -> int:
    dest_parent = args.dest or DEFAULT_DEST_PARENT
    if not verify_destination(dest_parent):
        print("ERROR: Destination not accessible", file=sys.stderr)
        return 1

    # Try latest marker first, then fall back to newest snapshot
    latest = get_latest_marker(dest_parent)
    if latest:
        snap_name = latest
    else:
        snaps = list_snapshots(dest_parent)
        if not snaps:
            print("No backups found")
            return 0
        snap_name = snaps[0]["name"]

    snap_path = os.path.join(dest_parent, snap_name)
    if not os.path.isdir(snap_path):
        print(f"Latest marker points to missing snapshot: {snap_name}")
        return 1

    marker = read_verify_marker(snap_path)
    files, size = count_files_and_size(snap_path)

    print(f"Snapshot:    {snap_name}")
    print(f"Date:        {snap_name.replace('openclaw.bak.', '')}")
    print(f"Files:       {files}")
    print(f"Size:        {format_size(size)}")
    if marker:
        status = marker.get("status", "unknown")
        print(f"Verify:      {status}")
    else:
        print("Verify:      no marker (unverified)")
    return 0


def cmd_list(args) -> int:
    dest_parent = args.dest or DEFAULT_DEST_PARENT
    snapshots = list_snapshots(dest_parent)
    if not snapshots:
        print("No backups found")
        return 0
    print(f"{'Snapshot':<30} {'Date':<12} {'Files':>8} {'Size':>10}")
    print("-" * 62)
    for s in snapshots:
        print(f"{s['name']:<30} {s['date']:<12} {s['file_count']:>8} {format_size(s['size_bytes']):>10}")
    return 0


def cmd_verify(args) -> int:
    dest_parent = args.dest or DEFAULT_DEST_PARENT
    snap_name = args.backup_name
    snap_path = os.path.join(dest_parent, snap_name)
    if not os.path.isdir(snap_path):
        print(f"ERROR: Snapshot not found: {snap_name}", file=sys.stderr)
        return 1

    files, size = count_files_and_size(snap_path)
    marker = read_verify_marker(snap_path)

    print(f"Snapshot:    {snap_name}")
    print(f"Files:       {files}")
    print(f"Size:        {format_size(size)}")
    if marker:
        src_files = marker.get("src_file_count", "?")
        src_bytes = marker.get("src_total_bytes", "?")
        status = marker.get("status", "?")
        print(f"Verify:      status={status}, src_files={src_files}, src_bytes={src_bytes}")
        if str(files) != src_files:
            print("WARNING: File count mismatch vs source — backup may be incomplete")
            return 1
    else:
        print("Verify:      no marker (run resync-claw verify after run)")
    return 0


def cmd_restore(args) -> int:
    dest_parent = args.dest or DEFAULT_DEST_PARENT
    if args.full:
        if not args.target_path:
            print("ERROR: --full restore requires --target-path", file=sys.stderr)
            return 1
        success, msg = resync_full(
            snap_name=args.backup_name,
            target_path=args.target_path,
            dest_parent=dest_parent,
            force=args.force,
        )
    else:
        if not args.relative_path or not args.target_path:
            print("ERROR: --file restore requires --relative-path and --target-path", file=sys.stderr)
            return 1
        success, msg = resync_file(
            snap_name=args.backup_name,
            relative_path=args.relative_path,
            target_path=args.target_path,
            dest_parent=dest_parent,
            force=args.force,
        )
    print(msg)
    return 0 if success else 1


def cmd_install_cron(args) -> int:
    success, msg = install_cron()
    print(msg)
    return 0 if success else 1


def cmd_remove_cron(args) -> int:
    success, msg = remove_cron()
    print(msg)
    return 0 if success else 1


def cmd_compare(args) -> int:
    dest_parent = args.dest or DEFAULT_DEST_PARENT

    # Auto-detect zip comparison: if both args end in .zip, treat as absolute zip paths
    is_zip = args.backup_old.endswith(".zip") and args.backup_new.endswith(".zip")

    try:
        if is_zip:
            from .diff import compare_zips
            snap_old = args.backup_old
            snap_new = args.backup_new
            changed, deleted = compare_zips(snap_old, snap_new)
        else:
            from .diff import compare_snapshots
            changed, deleted = compare_snapshots(
                dest_parent=dest_parent,
                snap_old=args.backup_old,
                snap_new=args.backup_new,
            )
            snap_old = args.backup_old
            snap_new = args.backup_new

        output = format_compare_output(
            snap_old=snap_old,
            snap_new=snap_new,
            changed=changed,
            deleted=deleted,
            verbose=args.verbose,
        )
        print(output)
        return 0
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def cmd_help(args) -> int:
    """Print the full usage docstring (shown as --help but as a subcommand)."""
    print(__doc__)
    return 0


# ----------------------------------------------------------------------
# Argument parser
# ----------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="resync-claw",
        description="Disaster file safe for OpenClaw",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    run_parser = sub.add_parser("run", help="Run a new backup now")
    run_parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    run_parser.add_argument("--force", action="store_true", help="Force overwrite if snapshot exists")
    run_parser.add_argument("--dest", metavar="PATH", help=f"Destination parent (default: {DEFAULT_DEST_PARENT})")
    run_parser.add_argument("--source", metavar="PATH", help=f"Source directory (default: {DEFAULT_SOURCE})")
    run_parser.add_argument("--compress", action="store_true",
                            help="Compress snapshot into a zip archive after backup (see RESYNC_COMPRESS env var)")
    run_parser.add_argument("--remove-original", action="store_true",
                            help="Delete uncompressed snapshot after zipping (only valid with --compress)")
    run_parser.set_defaults(func=cmd_run)

    # status
    status_parser = sub.add_parser("status", help="Show last backup status")
    status_parser.add_argument("--dest", metavar="PATH", help=f"Destination (default: {DEFAULT_DEST_PARENT})")
    status_parser.set_defaults(func=cmd_status)

    # list
    list_parser = sub.add_parser("list", help="List all snapshots")
    list_parser.add_argument("--dest", metavar="PATH", help=f"Destination (default: {DEFAULT_DEST_PARENT})")
    list_parser.set_defaults(func=cmd_list)

    # verify
    verify_parser = sub.add_parser("verify", help="Verify snapshot integrity")
    verify_parser.add_argument("backup_name", help="Snapshot name (e.g. openclaw.bak.20260331)")
    verify_parser.add_argument("--dest", metavar="PATH", help=f"Destination (default: {DEFAULT_DEST_PARENT})")
    verify_parser.set_defaults(func=cmd_verify)

    # restore
    restore_parser = sub.add_parser("restore", help="Restore from a snapshot")
    restore_parser.add_argument("--full", action="store_true", help="Restore entire snapshot")
    restore_parser.add_argument("--file", action="store_true", help="Restore a specific file/directory")
    restore_parser.add_argument("backup_name", help="Snapshot name")
    restore_parser.add_argument("--target-path", metavar="PATH", dest="target_path",
                                 help="Target path for --full restore")
    restore_parser.add_argument("--relative-path", metavar="PATH", dest="relative_path",
                                 help="Relative path within snapshot for --file restore")
    restore_parser.add_argument("--force", action="store_true", help="Overwrite if target exists")
    restore_parser.add_argument("--dest", metavar="PATH", help=f"Destination (default: {DEFAULT_DEST_PARENT})")
    restore_parser.set_defaults(func=cmd_restore)

    # compare
    compare_parser = sub.add_parser("compare", help="Compare two snapshots and show differences")
    compare_parser.add_argument("backup_old", help="Older snapshot name (e.g. openclaw.bak.20260330)")
    compare_parser.add_argument("backup_new", help="Newer snapshot name (e.g. openclaw.bak.20260331)")
    compare_parser.add_argument("--dest", metavar="PATH", help=f"Destination (default: {DEFAULT_DEST_PARENT})")
    compare_parser.add_argument("--verbose", action="store_true", help="Show all changed files (up to 200, default: 50)")
    compare_parser.set_defaults(func=cmd_compare)

    # install-cron
    cron_install_parser = sub.add_parser("install-cron", help="Install cron job (every day at 04:00)")
    cron_install_parser.set_defaults(func=cmd_install_cron)

    # remove-cron
    cron_remove_parser = sub.add_parser("remove-cron", help="Remove cron job")
    cron_remove_parser.set_defaults(func=cmd_remove_cron)

    # help
    help_parser = sub.add_parser("help", help="Print this usage information")
    help_parser.set_defaults(func=cmd_help)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
