#!/bin/bash

# Source common variables and functions
source /scripts/02-common.sh

# Run installation scripts
/scripts/03-install-mono.sh
/scripts/04-install-mt5.sh
/scripts/05-install-python.sh
/scripts/06-install-libraries.sh

# Start servers
/scripts/07-start-wine-flask.sh
/scripts/09-start-websocket.sh

# Configure mt5 to allow sending events to 127.0.0.1
/scripts/10-allow-webrequest-allowlist.sh

sleep 5
curl -sf http://localhost:5000/health >/dev/null \
  && log_message "INFO" "health check OK" \
  || log_message "ERROR" "health check FAILED"

# Keep the script running
tail -f /dev/null