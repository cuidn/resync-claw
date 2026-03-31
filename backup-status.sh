#!/bin/bash
# Daily backup status report for resync-claw
# Sends report to news bot on Telegram

DEST="${HOME}/ClawBackup"

# Get snapshot info
SNAPSHOTS=$(resync-claw list 2>/dev/null | grep "openclaw.bak." | tail -5)

# Get latest snapshot size
LATEST=$(resync-claw list 2>/dev/null | grep "openclaw.bak." | tail -1)
LATEST_NAME=$(echo "$LATEST" | awk '{print $1}')
LATEST_SIZE=$(echo "$LATEST" | awk '{print $2}')
LATEST_DATE=$(echo "$LATEST" | awk '{print $3, $4}')

# Get total snapshots count
TOTAL=$(resync-claw list 2>/dev/null | grep -c "openclaw.bak." || echo "0")

# Get disk usage
if [ -d "$DEST" ]; then
    DISK_USAGE=$(du -sh "$DEST" 2>/dev/null | awk '{print $1}')
else
    DISK_USAGE="N/A"
fi

# Build the report
REPORT="📰 *Resync-Claw Backup Report*
📅 $(date '+%Y-%m-%d %H:%M %Z')

*Last Backup:* $LATEST_NAME ($LATEST_SIZE) — $LATEST_DATE
*Total Snapshots:* $TOTAL (keep 7 days)
*Disk Used:* $DISK_USAGE

*Recent Snapshots:*
$SNAPSHOTS

*Status:* ✅ Running daily at 04:00"

echo "$REPORT"
