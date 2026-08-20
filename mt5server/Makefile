#MT5_SYMBOLS := $(shell grep -E '^MT5_SYMBOLS=' ../.env 2>/dev/null | cut -d= -f2-)
#MT5_WS_URL  := $(shell grep -E '^MT5_WS_URL='  ../.env 2>/dev/null | cut -d= -f2-)

run:
	docker build -t mt5test .
	docker rm -f mt5-test || true
	docker run --name mt5-test \
		-p 127.0.0.1:5000:5000 -p 127.0.0.1:9000:9000 -p 127.0.0.1:3001:3001 \
		-e MT5_SYMBOLS="${MT5_SYMBOLS}" \
		-e MT5_WS_URL="${MT5_WS_URL}" \
		-e MT5_SERVER="${MT5_SERVER}" \
		-e MT5_PASSWORD="${MT5_PASSWORD}" \
		-e MT5_ACCOUNT="${MT5_ACCOUNT}" \
		-d mt5test:latest

test_server: run
	docker exec mt5-test tail -f /var/log/mt5_setup.log

live_example:
	../.venv/bin/python ../examples/live_remote.py

smoke: 
	../.venv/bin/python smoke_test.py

compile:
	docker  exec -e DISPLAY=:1 --user abc   mt5-test  /bin/bash -c " \"/config/.wine/drive_c/Program Files/MetaTrader 5/MetaEditor64.exe\"  /compile:C:\\mt5build\\ticks.mq5"
