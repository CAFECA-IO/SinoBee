#!/usr/bin/env bash
# Print this machine's hardware as one line for SINOBEE_HOST_HARDWARE, so SinoBee running in Docker can recommend
# local models that fit the HOST (the container cannot see the host's GPU / Apple Silicon memory).
#   SINOBEE_HOST_HARDWARE="$(scripts/host-hardware.sh)" docker compose up -d
set -u
clean() { tr ';=' ',:' | tr -s ' ' | sed 's/^ //;s/ $//'; }
os=$(uname -s | tr '[:upper:]' '[:lower:]'); arch=$(uname -m)
cpu="" cores=0 mem=0 gpu="" vram=0 vendor="" unified=0 disk=0
if [[ $os == darwin ]]; then
  cpu=$(sysctl -n machdep.cpu.brand_string 2>/dev/null)
  cores=$(sysctl -n hw.ncpu 2>/dev/null || echo 0)
  mem=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1073741824 ))
  [[ $arch == arm64 ]] && { gpu=$cpu; unified=1; }
  disk=$(df -g "$HOME" 2>/dev/null | awk 'NR==2{print $4}')
else
  cpu=$(awk -F: '/model name/{print $2; exit}' /proc/cpuinfo 2>/dev/null)
  cores=$(nproc 2>/dev/null || echo 0)
  mem=$(( $(awk '/MemTotal/{print $2}' /proc/meminfo 2>/dev/null || echo 0) / 1048576 ))
  if command -v nvidia-smi >/dev/null 2>&1; then
    line=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>/dev/null | head -1)
    gpu=${line%%,*}; vram=$(( ${line##*,} / 1024 )); vendor=nvidia
    n=$(nvidia-smi -L 2>/dev/null | wc -l); (( n > 1 )) && { vram=$(( vram * n )); gpu="$gpu x$n"; }
  elif command -v rocm-smi >/dev/null 2>&1; then
    gpu=$(rocm-smi --showproductname 2>/dev/null | awk -F: '/Card series/{print $3; exit}'); vendor=amd
    vram=$(( $(rocm-smi --showmeminfo vram --csv 2>/dev/null | awk -F, 'NR==2{print $2}') / 1073741824 ))
  fi
  disk=$(df -BG "$HOME" 2>/dev/null | awk 'NR==2{gsub("G","",$4); print $4}')
fi
echo "os=$os;arch=$arch;cpu=$(echo "$cpu" | clean);cores=$cores;mem_gb=$mem;gpu=$(echo "$gpu" | clean);vram_gb=$vram;gpu_vendor=$vendor;unified=$unified;disk_gb=${disk:-0}"
