#!/bin/bash
# Install OF-Agent: Python venv + dependencies.
set -e
cd "$(dirname "$0")"
echo "Creating Python virtual environment (venv)..."
[ -d venv ] || python3 -m venv venv
echo "Installing Python dependencies..."
./venv/bin/pip install -r requirements.txt
echo ""
echo "Done. Configure with ./configure.sh, then launch the web UI with ./start.sh"
