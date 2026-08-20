#!/bin/bash
# -----------------------------------------------------------------------------
# Automate: MT5 Options -> Expert Advisors -> "Allow WebRequest for listed URL"
# + add an allowed URL. Works on the running MT5 terminal in the mt5-test
# container WITHOUT restarting (the Wine registry holds the setting in memory;
# a terminal restart would wipe it back to the [Experts] WebRequestUrl= from
# setup.ini, which MT5 IGNORES -- that ini key is MT4-only).
#
# Extracted from the interactive session on 2026-08-13.
# Requires: docker; inside the container xdotool, tesseract, python3+PIL.
# Usage:    ./allow-webrequest-allowlist.sh [URL]
#           (default URL: 127.0.0.1:9000)
# -----------------------------------------------------------------------------
set -euo pipefail

source /scripts/02-common.sh
log_message "RUNNING" "10-allow-webrequest-allowlist.sh"


CONTAINER="${CONTAINER:-mt5-test}"
URL="${1:-127.0.0.1}"
DISPLAY=":0"


echo "== 1. focus terminal and open Options (Ctrl+O) =="
xdotool mousemove 400 300 click 1
sleep 0.5
xdotool key ctrl+o
sleep 3

echo "== 2. find the Options window and move it to (0,0) =="
WIN=$(xdotool search --name 'Options' | tail -1)
[ -n "$WIN" ] || { echo "Options window not found"; exit 1; }
echo "   Options window id: $WIN"
xdotool windowmove $WIN 0 0
sleep 1

echo "== 3. click the 'Experts' tab (tab strip is at top; dialog at 0,0) =="
# tab order in this build: Server | Charts | Trade | Experts | GPU | ...
xdotool mousemove 180 16 click 1
sleep 1

echo "== 4. ensure 'Allow WebRequest for listed URL' is CHECKED =="
# checkbox square sits at local (42,300); sample its interior for a check mark
import -window $WIN /tmp/wr_check.png
CHECKED=$(python3 -c "from PIL import Image; im=Image.open('/tmp/wr_check.png').convert('L'); a=im.load(); n=sum(1 for x in range(30,62) for y in range(279,316) if a[x,y]<150); print(1 if n>12 else 0)")
if [ "$CHECKED" = "1" ]; then
   echo "   already checked"
else
   echo "   clicking checkbox"
   xdotool mousemove 42 300 click 1
   sleep 0.5
fi

echo "== 5. add the URL (double-click the '+ add new URL' row, then type) =="
# NOTE: a single click + type does NOT register; the row needs a double-click.
xdotool mousemove 150 333 click --repeat 2 --delay 100 1
sleep 0.5
xdotool type --delay 50 "$URL"
xdotool key Return
xdotool key Tab
xdotool key Return
sleep 1

echo "== 8. cleanup + notes =="
rm -f /tmp/wr_check.png /tmp/wr_url.png
cat <<'EOF'
   Done. The setting is now active in the running terminal (no restart needed).

   Notes:
   - Clicking OK re-initialises attached EAs (journal shows Deinitialization),
     so re-attach the ticks EA afterwards if it was running.
   - Pressing Return while editing the URL row also closes the dialog (= OK).
   - The setting lives in the Wine in-memory registry only; it is NOT written
     back to setup.ini / terminal.ini while the terminal runs. A clean terminal
     exit may flush it to user.reg (verify before relying on it).
EOF