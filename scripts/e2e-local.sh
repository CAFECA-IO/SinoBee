#!/usr/bin/env bash
# End-to-end check of packaging a task into a skill and evaluating it, against a throw-away local SinoBee.ai.
#
#   SINOBEE_AI_DIR=../SinoBee.ai scripts/e2e-local.sh                 # scripted models: no model calls, no cost
#
# (With real models use scripts/e2e-real.sh: models through the SinoBee.ai relay need the real sinobee.ai.)
# Creates a fresh SQLite database for SinoBee.ai, two accounts with tokens (scripts/e2e-accounts.ts), starts
# `next dev` on a free port, runs scripts/e2e_skill_eval.py (steps A-D) and stops the server. Nothing is sent to
# the real sinobee.ai. Report: $OUT/report.md (default /tmp/sinobee-e2e-<time>).
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-$( [ -x "$HERE/.venv/bin/python" ] && echo "$HERE/.venv/bin/python" || echo python3)}"   # SinoBee's own environment
SBAI="${SINOBEE_AI_DIR:-$HERE/../SinoBee.ai}"
OUT="${OUT:-/tmp/sinobee-e2e-$(date +%Y%m%d-%H%M%S)}"
PORT="${PORT:-3299}"
ARGS=("$@")
[ -d "$SBAI" ] || { echo "SinoBee.ai not found at $SBAI (set SINOBEE_AI_DIR)"; exit 1; }
mkdir -p "$OUT"
DB="$OUT/sinobee-ai.db"
echo "▶ SinoBee.ai database $DB"
( cd "$SBAI" && SQLITE_PATH="$DB" DATABASE_URL= npx prisma migrate deploy >/dev/null )
TOKENS=$(cd "$SBAI" && SQLITE_PATH="$DB" DATABASE_URL= npx tsx scripts/e2e-accounts.ts)
T1=$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['token'])" "$TOKENS")
T2=$(python3 -c "import json,sys;print(json.loads(sys.argv[1])['token2'])" "$TOKENS")
echo "▶ SinoBee.ai on http://127.0.0.1:$PORT"
( cd "$SBAI" && SQLITE_PATH="$DB" DATABASE_URL= PUBLIC_ORIGIN="http://127.0.0.1:$PORT" npx next dev -p "$PORT" > "$OUT/sinobee-ai.log" 2>&1 ) &
SERVER=$!
trap 'kill $SERVER 2>/dev/null; pkill -f "next dev -p $PORT" 2>/dev/null || true' EXIT
for _ in $(seq 1 90); do curl -sf "http://127.0.0.1:$PORT/api/v1/evals" >/dev/null && break; sleep 1; done
"$PY" "$HERE/scripts/e2e_skill_eval.py" --scripted --hub "http://127.0.0.1:$PORT" --token "$T1" --token2 "$T2" \
  --out "$OUT" --visibility public --publish-skill \
  ${ARGS[@]+"${ARGS[@]}"}
