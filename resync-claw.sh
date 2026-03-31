#!/usr/bin/env bash
#------------------------------------------------------------------------------
# resync-claw — shell wrapper (no install needed)
#
# Usage:
#   ./resync-claw.sh run --dry-run
#   ./resync-claw.sh list
#   ./resync-claw.sh help
#
# Options:
#   RESYNC_SOURCE   Override source dir  (defaults to $HOME/.openclaw/)
#   RESYNC_DEST    Override dest parent  (default: $HOME/ClawBackup/)
#
# Requirements: Python 3 with the standard library only (no deps needed).
#------------------------------------------------------------------------------

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

export PYTHONPATH="${SCRIPT_DIR}:${PYTHONPATH:-}"

exec python3 -m resync_claw.cli "$@"
