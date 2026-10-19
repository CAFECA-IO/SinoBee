#!/usr/bin/env bash
# Pilot model / skill evaluations for sinobee.ai: every model runs each suite alone (it plans, works and reviews
# itself), gemini-pro grades the rubrics, and each finished run is exported as JSON into _eval_results/.
#   scripts/bench-pilot.sh                     # all suites and models below
#   MODELS="gemini/gemini-flash-latest" scripts/bench-pilot.sh bench-coding
cd "$(dirname "$0")/.." || exit 1
SB="${SINOBEE:-.venv/bin/sinobee}"
OUT="${OUT:-_eval_results}"
JUDGE="${JUDGE:-gemini/gemini-pro-latest}"
MODELS="${MODELS:-gemini/gemini-pro-latest gemini/gemini-flash-latest ollama/gemma4:e4b}"
SUITES="${*:-bench-coding bench-youtube}"
mkdir -p "$OUT"
for suite in $SUITES; do
  for model in $MODELS; do
    echo "== $suite · $model (judge $JUDGE) · $(date '+%F %T')"
    "$SB" eval run "$suite" -m "$model" --judge "$JUDGE"
    "$SB" eval export --out "$OUT"
  done
done
echo "done: $(ls "$OUT" | wc -l | tr -d ' ') runs in $OUT/"
