#!/usr/bin/env bash
# Evaluations for sinobee.ai (/benchmarks/evals), scored with SFS v1 (sinobee/evals/sfs.py).
#   model evaluation  bench-coding: each model does everything alone
#   skill evaluation  bench-youtube: each "thinking + execution" combination uses the skill
# Trials are interleaved (trial 1 of every subject, then trial 2, …) so network and API load fall on all alike.
# Afterwards the combinations of each suite are compared for creativity (blind, pairwise), the runs are exported
# to _eval_results/, and the publish commands are printed (one report per combination).
#   scripts/bench-pilot.sh                         # both suites
#   K=1 scripts/bench-pilot.sh bench-youtube       # one quick trial
cd "$(dirname "$0")/.." || exit 1
SB="${SINOBEE:-.venv/bin/sinobee}"
OUT="${OUT:-_eval_results}"
K="${K:-3}"
JUDGE="${JUDGE:-gemini/gemini-pro-latest}"
JUDGE2="${JUDGE2:-}"                    # a second judge of another family, e.g. anthropic/claude-sonnet-4-5
MODELS="${MODELS:-gemini/gemini-pro-latest gemini/gemini-flash-latest ollama/gemma4:e4b}"
# thinking>execution
COMBOS="${COMBOS:-gemini/gemini-pro-latest>gemini/gemini-flash-latest gemini/gemini-pro-latest>gemini/gemini-pro-latest gemini/gemini-flash-latest>gemini/gemini-flash-latest gemini/gemini-pro-latest>ollama/gemma4:e4b}"
SUITES="${*:-bench-coding bench-youtube}"
mkdir -p "$OUT"
j2=(); [ -n "$JUDGE2" ] && j2=(--judge2 "$JUDGE2")
for suite in $SUITES; do
  ids=()
  if grep -q '"kind": "skill"' "sinobee/evals/suites/$suite.json" 2>/dev/null; then subjects=$COMBOS; else subjects=$MODELS; fi
  for t in $(seq 1 "$K"); do
    for s in $subjects; do
      if [[ "$s" == *">"* ]]; then th="${s%%>*}"; ex="${s##*>}"; args=(-m "$ex" --thinking "$th"); else args=(-m "$s"); fi
      echo "== $suite · trial $t/$K · ${s} · judge $JUDGE · $(date '+%F %T')"
      id=$("$SB" eval run "$suite" -k 1 "${args[@]}" --judge "$JUDGE" "${j2[@]}" --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
      [ -n "$id" ] && ids+=("$id")
    done
  done
  [ ${#ids[@]} -gt 1 ] && "$SB" eval compare "${ids[@]}" --judge "$JUDGE"
  "$SB" eval export "${ids[@]}" --out "$OUT/$suite"
  echo "publish $suite:  $SB eval publish ${ids[*]}"
done
echo "done: runs in $OUT/"
