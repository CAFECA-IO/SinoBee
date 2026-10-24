#!/usr/bin/env bash
# Regenerate requirements.lock: exact versions with hashes, none published in the last 7 days (a cooldown, so a
# malicious release that is pulled within days - LiteLLM 1.107.0, 2026-10-21 - never reaches a build).
# Docker images install from it with `pip install --require-hashes -r requirements.lock`.
set -euo pipefail
cd "$(dirname "$0")/.."
DAYS=${COOLDOWN_DAYS:-7}
CUT=$(python3 -c "import datetime as d; print((d.datetime.now(d.timezone.utc)-d.timedelta(days=$DAYS)).strftime('%Y-%m-%dT00:00:00Z'))")
uv pip compile pyproject.toml --extra all --extra media --generate-hashes --python-version 3.12 \
  --python-platform linux --exclude-newer "$CUT" -o requirements.lock
echo "requirements.lock: packages published before $CUT"
