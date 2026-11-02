#!/usr/bin/env bash
# Mac worker-i launchd ilə qoşur: hər 10 dəqiqədən bir scripts/local_tick.py (LOCAL_ONLY mənbələr).
# Kompüter açılanda/girişdə özü başlayır, yuxudan sonra buraxılan vaxtı tutur (24 saata qədər).
# Lazımdır: ~/.neo-worker.env (DATABASE_URL, OPENAI_API_KEY; chmod 600), .venv (requirements-worker.txt).
#
#   bash scripts/install_mac_worker.sh             # qoş / yenilə
#   bash scripts/install_mac_worker.sh uninstall   # söndür
#   tail -f ~/Library/Logs/neo-worker.log          # jurnal
set -euo pipefail
LABEL="com.jarvis.neo-worker"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$HOME/Library/Logs/neo-worker.log"

if [ "${1:-}" = "uninstall" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Söndürüldü."
  exit 0
fi

[ -f "$HOME/.neo-worker.env" ] || { echo "~/.neo-worker.env yoxdur"; exit 1; }
[ -x "$DIR/.venv/bin/python" ] || { echo "$DIR/.venv yoxdur"; exit 1; }

mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$DIR/.venv/bin/python</string>
    <string>-m</string>
    <string>scripts.local_tick</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>StartInterval</key><integer>600</integer>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Qoşuldu: $LABEL (hər 10 dəq). Jurnal: $LOG"
