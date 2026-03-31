# resync-claw

Disaster file safe for OpenClaw — timestamped snapshot backup to local storage.

Backs up `$HOME/.openclaw/` to `$HOME/ClawBackup/` as timestamped snapshots (`openclaw.bak.YYYYMMDD`). Keeps the last 7 snapshots with daily auto-cleanup. Includes full and per-file restore.

## Quick Start

```bash
# Install
cd ~/codes/resync-claw
pip install -e .

# Dry run (see what would happen)
resync-claw run --dry-run

# Run a backup now
resync-claw run --force

# List snapshots
resync-claw list

# Check status of last backup
resync-claw status

# Verify a specific snapshot
resync-claw verify openclaw.bak.20260331

# Restore entire snapshot
resync-claw restore --full openclaw.bak.20260331 /tmp/openclaw_restore/

# Restore a specific file from snapshot
resync-claw restore --file openclaw.bak.20260331 workspace-coding/AGENTS.md /tmp/ag.md

# Install cron (daily at 04:00)
resync-claw install-cron

# Remove cron
resync-claw remove-cron
```

## Backup Behavior

- **Source:** `$HOME/.openclaw/`
- **Destination:** `$HOME/ClawBackup/`
- **Snapshot naming:** `openclaw.bak.YYYYMMDD`
- **Excludes:** `tmp/`, `.cache/`, `logs/`, `__pycache__/`, `node_modules/`, `*.pyc`, `*.pyo`, `.DS_Store`, `.Trash-*`
- **Includes:** all workspaces (`workspace-coding`, `workspace-claude`, etc.)
- **Retention:** last 7 snapshots auto-deleted after each successful backup

## Cron

Runs daily at 04:00 AM. Installed via crontab (not root).

```
0 4 * * * $HOME/.local/bin/resync-claw run --force >> $HOME/.openclaw/logs/backup-cron.log 2>&1
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error (snapshot not found, destination not writable, etc.) |
| 2 | rsync failed (non-zero exit) |
| 3 | rsync timed out |
| 4 | Snapshot not found |
| 5 | Path traversal attempt blocked |
| 6 | Verification failed |
| 7 | Cron install/remove failed |

## Development

```bash
# Run tests
python -m pytest tests/ -v

# Dry run without installing
python -m resync_claw.cli run --dry-run
```
