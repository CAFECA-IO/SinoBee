#!/usr/bin/env bash
# Start SinoBee in Docker, passing model + API keys on the command line (nothing is written to disk on the host).
#
#   scripts/docker-run.sh                                   # no key: configure in the Web UI
#   scripts/docker-run.sh -m anthropic/claude-sonnet-4-5 -k anthropic        # prompts for the key (hidden)
#   scripts/docker-run.sh -m openai/gpt-5.4 -k openai=sk-... -k gemini=AIza...
#   scripts/docker-run.sh --local-llm                       # also run a local model runtime (Ollama), no key needed
#   scripts/docker-run.sh --local-llm -m ollama/qwen3.5:9b  # ...and use that local model
#   scripts/docker-run.sh --public                          # reachable from other devices (token generated)
#
# Options: -m model  -k provider[=key] (repeatable)  -a approval(auto|confirm|readonly)
#          -b monthly budget USD  -t web token  -p port (default 10024)  -n name  -i image
#          -P | --public     listen on all interfaces; an access token is generated unless -t is given
#          -L | --local-llm  start an Ollama container (GPU if available); on a Mac with Ollama
#                            installed natively, that one is used instead (Metal acceleration)
set -euo pipefail
ARGS=(); for a in "$@"; do case $a in --local-llm) ARGS+=(-L) ;; --public) ARGS+=(-P) ;; *) ARGS+=("$a") ;; esac; done
set -- ${ARGS[@]+"${ARGS[@]}"}
LOCAL_LLM=0 PUBLIC=0
MODEL="" APPROVAL="" BUDGET="" TOKEN="" PORT=10024 NAME=sinobee IMAGE=sinobee:latest
ENVS=()
while getopts "m:k:a:b:t:p:n:i:LPh" opt; do
  case $opt in
    m) MODEL=$OPTARG ;;
    k) prov=${OPTARG%%=*}; key=""
       [[ $OPTARG == *=* ]] && key=${OPTARG#*=}
       if [[ -z $key ]]; then read -rsp "${prov} API key: " key; echo; fi
       case $prov in
         qwen|dashscope) var=DASHSCOPE_API_KEY ;;
         google) var=GEMINI_API_KEY ;;
         claude) var=ANTHROPIC_API_KEY ;;
         *) var="$(echo "$prov" | tr '[:lower:]-' '[:upper:]_')_API_KEY" ;;
       esac
       export "$var=$key"; ENVS+=(-e "$var") ;;      # -e VAR (no value): the value never appears in `ps`
    a) APPROVAL=$OPTARG ;;
    b) BUDGET=$OPTARG ;;
    t) TOKEN=$OPTARG ;;
    p) PORT=$OPTARG ;;
    P) PUBLIC=1 ;;
    n) NAME=$OPTARG ;;
    i) IMAGE=$OPTARG ;;
    L) LOCAL_LLM=1 ;;
    h|*) sed -n '2,16p' "$0"; exit 0 ;;
  esac
done
[[ -n $MODEL ]] && { export SINOBEE_MODEL=$MODEL; ENVS+=(-e SINOBEE_MODEL); }
[[ -n $APPROVAL ]] && { export SINOBEE_APPROVAL=$APPROVAL; ENVS+=(-e SINOBEE_APPROVAL); }
[[ -n $BUDGET ]] && { export SINOBEE_MONTHLY_BUDGET=$BUDGET; ENVS+=(-e SINOBEE_MONTHLY_BUDGET); }
if [[ -z $TOKEN ]]; then   # always an access token (security review 2026-10-24): any web page can reach localhost
  TOKEN=$(openssl rand -hex 24 2>/dev/null || python3 -c 'import secrets; print(secrets.token_hex(24))')
fi
BIND=127.0.0.1; [[ $PUBLIC == 1 ]] && BIND=0.0.0.0
[[ -n $TOKEN ]] && { export SINOBEE_SERVER_TOKEN=$TOKEN; ENVS+=(-e SINOBEE_SERVER_TOKEN); }

# host hardware -> model recommendations in the UI (the container can't see the host GPU / Apple memory)
export SINOBEE_HOST_HARDWARE="$("$(dirname "$0")/host-hardware.sh" 2>/dev/null || true)"; ENVS+=(-e SINOBEE_HOST_HARDWARE)
NET=()
if (( LOCAL_LLM )); then
  if curl -fsS -m 2 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    echo "Using the Ollama already running on this machine (reached from the container via host.docker.internal)."
  else
    [[ $(uname -s) == Darwin ]] && echo "Tip: on a Mac, native Ollama (https://ollama.com/download) is much faster than a container (Metal GPU)."
    GPU=(); TAG=${OLLAMA_TAG:-0.40.5}   # a fixed release, not :latest
    if command -v nvidia-smi >/dev/null 2>&1 && docker info 2>/dev/null | grep -qi nvidia; then GPU=(--gpus all)
    elif [[ -e /dev/kfd ]]; then GPU=(--device /dev/kfd --device /dev/dri); TAG="${TAG}-rocm"; fi
    docker network create sinobee-net >/dev/null 2>&1 || true
    docker rm -f "${NAME}-ollama" >/dev/null 2>&1 || true
    docker run -d --name "${NAME}-ollama" --restart unless-stopped --network sinobee-net --network-alias ollama \
      -v ollama-models:/root/.ollama -e OLLAMA_KEEP_ALIVE=30m -e OLLAMA_FLASH_ATTENTION=1 \
      ${GPU[@]+"${GPU[@]}"} "ollama/ollama:${TAG}" >/dev/null
    echo "Started Ollama container ${NAME}-ollama${GPU:+ (GPU)}."
    NET=(--network sinobee-net)
  fi
fi

docker image inspect "$IMAGE" >/dev/null 2>&1 || docker build -t "$IMAGE" "$(dirname "$0")/.."
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --restart unless-stopped --shm-size 1g ${NET[@]+"${NET[@]}"} \
  -p "${BIND}:${PORT}:10024" \
  --add-host host.docker.internal:host-gateway \
  -v sinobee-home:/home/bee/.sinobee -v sinobee-workspace:/home/bee/workspace ${ENVS[@]+"${ENVS[@]}"} "$IMAGE" >/dev/null
echo "SinoBee is starting → http://localhost:${PORT}/?token=${TOKEN}   (open it once; this browser then remembers)"
if [[ $PUBLIC == 1 ]]; then
  ip=$( (ipconfig getifaddr en0 || ipconfig getifaddr en1 || hostname -I | awk '{print $1}') 2>/dev/null | head -1)
  echo "Other devices        → http://${ip:-<this-computer-ip>}:${PORT}/?token=${TOKEN}"
  echo "Keep the token private; use an HTTPS tunnel (Tailscale, Cloudflare Tunnel) across the internet."
fi
