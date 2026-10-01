#!/bin/bash
# Source the OpenRouter key then run the Jev-decision scraper PoC.
set -a; source /root/.hermes/.env; set +a
export JEV_MODEL="${JEV_MODEL:-typesafe/jev-1.13}"
# Playwright + chromium live in the x_image_daily venv (this host).
PYBIN=/root/x_image_daily/venv/bin/python
cd /root/jev-poc
exec "$PYBIN" jev_agent.py "$@"