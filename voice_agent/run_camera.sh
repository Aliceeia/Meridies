#!/bin/bash
# Default: --rotate 180 (how the cap camera is mounted). Pass other args to override.
# Start the cap camera server in the background (log: camera.log). Safe to run twice.
cd "$(dirname "$0")"
if curl -s -m 1 localhost:8090/health >/dev/null; then echo "camera server already running"; exit 0; fi
nohup setsid .venv/bin/python camera_server.py ${@:---rotate 180} >> camera.log 2>&1 < /dev/null &
echo "camera server starting: http://$(hostname -I | cut -d' ' -f1):8090/"
