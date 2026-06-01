#!/usr/bin/env bash
# Start the personal assistant bot.
set -euo pipefail
cd "$(dirname "$0")"
exec .venv/bin/python -m app.main
