#!/bin/bash
# Source the OpenRouter key then run the Jev-decision scraper PoC.
set -a; source /root/.hermes/.env; set +a
export JEV_MODEL="${JEV_MODEL:-typesafe/jev-1.13}"
PYBIN=$(ls -d /root/.hermes/tools/python-3.14.7+*/bin/python3)
cd /root/jev-poc
exec "$PYBIN" jev_agent.py "$@"