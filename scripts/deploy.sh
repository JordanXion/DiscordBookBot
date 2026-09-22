#!/usr/bin/env bash
# Run from a checked-out repository on the VM after pulling the desired code.
set -euo pipefail

app_dir="${APP_DIR:-/opt/discord-book-bot}"
service="${SERVICE_NAME:-bookbot.service}"

cd "$app_dir"
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
sudo systemctl daemon-reload
sudo systemctl restart "$service"
sudo systemctl --no-pager --full status "$service"
