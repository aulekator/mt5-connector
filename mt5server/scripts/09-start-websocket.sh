#!/bin/bash

source /scripts/02-common.sh
log_message "RUNNING" "09-start-websocket.sh"

nohup python3 /app/ws_server.py >> /var/log/mt5_setup.log 2>&1 &
echo $! > /var/run/ws_server.pid
log_message "INFO" "WS hub started (pid $(cat /var/run/ws_server.pid), port $ws_port)"
