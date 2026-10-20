#!/usr/bin/env bash
# Package a task into a skill and evaluate it with YOUR models (steps A and B of scripts/e2e_skill_eval.py).
#
#   scripts/e2e-real.sh --combo sinobee/anthropic/claude-opus-5-5+sinobee/gemini/gemini-3.8-flash \
#                       --judge sinobee/gemini/gemini-3-pro -k 3 [--budget 6] [--visibility private]
#
# Uses a copy of ~/.sinobee (settings.json and secrets.json; the real home is not changed) in $OUT, so the models,
# keys and SinoBee.ai sign-in are yours and the model calls count against your plan or keys. The task's own
# combination (model.thinking + model.default) is always the baseline. Nothing is published unless you pass
# --visibility private (kept private on sinobee.ai; refused by a sinobee.ai that cannot keep it private) or public.
# Report: $OUT/report.md (default /tmp/sinobee-e2e-real-<time>).
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-$( [ -x "$HERE/.venv/bin/python" ] && echo "$HERE/.venv/bin/python" || echo python3)}"   # SinoBee's own environment
OUT="${OUT:-/tmp/sinobee-e2e-real-$(date +%Y%m%d-%H%M%S)}"
HAS_VIS=0
for a in "$@"; do [ "$a" = "--visibility" ] && HAS_VIS=1; done
EXTRA=()
[ "$HAS_VIS" = 0 ] && EXTRA=(--visibility none)
"$PY" "$HERE/scripts/e2e_skill_eval.py" --from-home "${SINOBEE_FROM_HOME:-$HOME/.sinobee}" --steps AB --out "$OUT" \
  ${EXTRA[@]+"${EXTRA[@]}"} "$@"
