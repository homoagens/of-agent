#!/bin/bash
# Launch the OF-Agent web UI (port 7862) and open the browser.
cd "$(dirname "$0")"
( sleep 2 && { xdg-open http://localhost:7862 2>/dev/null || open http://localhost:7862 2>/dev/null; } ) &
./venv/bin/python app.py
