#!/usr/bin/env bash
# SinoBee sandbox step 0: check what Docker Desktop on this Mac actually does before the sandbox is built on it.
#
#   scripts/sandbox-check-mac.sh            run every check (about 2-4 minutes, pulls ~20 MB the first time)
#   scripts/sandbox-check-mac.sh --keep     leave the test containers and files for a look afterwards
#
# Writes ~/.sinobee/sandbox-check/report.md (and report.tsv) and prints a summary. Touches nothing outside
# ~/.sinobee/sandbox-check and the containers / networks / volume named sbchk-*, which it removes at the end.
# Runs on macOS (the target) and on Linux (to test the script itself); a few expectations differ and are marked.
# Written for the bash 3.2 that macOS ships.
set -u

IMG="python:3.12-alpine"
ROOT="$HOME/.sinobee/sandbox-check"
OUT="$ROOT/report.md"
TSV="$ROOT/report.tsv"
KEEP=0
[ "${1:-}" = "--keep" ] && KEEP=1
OS="$(uname -s)"
MAC=0
[ "$OS" = "Darwin" ] && MAC=1
HOSTSRV_PID=""

now() { perl -MTime::HiRes=time -e 'printf "%.3f", time'; }
ms_since() { perl -e "printf '%d', ($(now) - $1) * 1000"; }
say() { printf '%s\n' "$*"; }
# record <id> <title> <PASS|FAIL|INFO|SKIP> <detail> [<what it means for the plan>]
record() { printf '%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "${5:-}" >> "$TSV"; printf '  %-4s %-5s %s - %s\n' "$1" "$3" "$2" "$4"; }
d() { docker "$@" 2>/dev/null; }

cleanup() {
  [ -n "$HOSTSRV_PID" ] && kill "$HOSTSRV_PID" 2>/dev/null
  [ "$KEEP" = 1 ] && { say "(--keep: containers sbchk-* and $ROOT left in place)"; return; }
  for c in sbchk-web sbchk-int sbchk-side sbchk-stage; do d rm -f "$c" >/dev/null; done
  for n in sbchk-internal sbchk-external; do d network rm "$n" >/dev/null; done
  d volume rm -f sbchk-vol >/dev/null
  rm -rf "$ROOT/ws" "$ROOT/stage" "$ROOT/src" "$ROOT/hostsrv" 2>/dev/null
}
trap cleanup EXIT

# a http fetch from inside a container: prints the HTTP status or "fail"
cfetch() { # <container> <url>
  docker exec "$1" python -c "
import sys, urllib.request
try:
    print(urllib.request.urlopen(sys.argv[1], timeout=5).status)
except Exception as e:
    print('fail', type(e).__name__)" "$2" 2>/dev/null | head -1
}
hfetch() { curl -s -o /dev/null -m 4 -w '%{http_code}' "$1" 2>/dev/null || true; }

mkdir -p "$ROOT"
: > "$TSV"
say "SinoBee sandbox check - $(date '+%Y-%m-%d %H:%M') - $OS $(uname -m)"

# ---------------------------------------------------------------- 0. Docker itself
if ! command -v docker >/dev/null 2>&1; then
  record C0 "Docker" FAIL "docker command not found" "install Docker Desktop first"; exit 1
fi
if ! docker info >/dev/null 2>&1; then
  record C0 "Docker" FAIL "the Docker daemon is not running" "start Docker Desktop and run again"; exit 1
fi
SV="$(d version --format '{{.Server.Version}}')"
NCPU="$(d info --format '{{.NCPU}}')"
MEM="$(d info --format '{{.MemTotal}}')"
OSNAME="$(d info --format '{{.OperatingSystem}}')"
MEMG="$(perl -e "printf '%.1f', $MEM/1024/1024/1024")"
record C0 "Docker" INFO "server $SV, $OSNAME, $NCPU CPU, ${MEMG} GB for containers" \
  "the sandbox default is 4 CPU / 8 GB; Docker Desktop must give its VM at least that"
say "  pulling $IMG ..."
docker pull -q "$IMG" >/dev/null 2>&1 || { record C0 "Image" FAIL "cannot pull $IMG" "check the network"; exit 1; }

# a service on the host (stands in for the SinoBee server and its egress proxy)
HOSTPY=""
for p in "${SINOBEE_PY:-}" "$PWD/.venv/bin/python" "$(command -v python3 2>/dev/null)"; do
  [ -n "$p" ] && [ -x "$p" ] && "$p" -c 1 >/dev/null 2>&1 && { HOSTPY="$p"; break; }
done
HOSTPORT=""
if [ -n "$HOSTPY" ]; then
  mkdir -p "$ROOT/hostsrv" && echo ok > "$ROOT/hostsrv/index.html"
  HOSTPORT="$(perl -MIO::Socket::INET -e '$s=IO::Socket::INET->new(Listen=>1,LocalAddr=>"0.0.0.0",LocalPort=>0); print $s->sockport')"
  (cd "$ROOT/hostsrv" && exec "$HOSTPY" -m http.server "$HOSTPORT" --bind 0.0.0.0 >/dev/null 2>&1) &
  HOSTSRV_PID=$!
  sleep 1
fi
ADDHOST="--add-host=host.docker.internal:host-gateway"   # Docker Desktop has it already; Linux needs this

# ---------------------------------------------------------------- 1. reaching a container from the host
docker run -d --name sbchk-web $ADDHOST -p 127.0.0.1::8000 "$IMG" python -m http.server 8000 >/dev/null
sleep 2
CIP="$(d inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' sbchk-web)"
PUB="$(d port sbchk-web 8000/tcp | head -1)"
code="$(hfetch "http://$CIP:8000/")"
if [ "$code" = "200" ]; then
  record C1a "Host -> container IP $CIP" INFO "reachable (HTTP 200)" \
    "$([ $MAC = 1 ] && echo 'unexpected on Docker Desktop: note it' || echo 'normal on Linux')"
else
  record C1a "Host -> container IP $CIP" INFO "not reachable" \
    "VNC, CDP and /preview cannot use the container's own address: publish to 127.0.0.1 (C1b)"
fi
code="$(hfetch "http://$PUB/")"
case "$PUB" in 127.0.0.1:*) bind=ok;; *) bind=no;; esac
if [ "$code" = "200" ] && [ "$bind" = ok ]; then
  record C1b "Host -> published 127.0.0.1 port" PASS "$PUB answers, bound to 127.0.0.1 only" \
    "plan D1/D3/D4: publish VNC/CDP/preview ports on 127.0.0.1 with a random port and a token"
else
  record C1b "Host -> published 127.0.0.1 port" FAIL "$PUB -> HTTP $code (bind $bind)" "D1/D3/D4 need another relay (docker exec stdio)"
fi
# ---------------------------------------------------------------- 5. docker exec latency
t0="$(now)"; for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do docker exec sbchk-web true; done
EXEC_MS=$(( $(ms_since "$t0") / 20 ))
if [ "$EXEC_MS" -le 150 ]; then st=PASS; else st=INFO; fi
record C5 "docker exec round trip" "$st" "${EXEC_MS} ms per call (20 calls)" \
  "$([ "$EXEC_MS" -le 150 ] && echo 'per-action docker exec is fine for shell and single clicks' || echo 'hold / drag paths / wait_change should run inside the container (a small daemon), not one exec per step')"

# ---------------------------------------------------------------- 2. internal network and a sidecar proxy
d network create --internal sbchk-internal >/dev/null
d network create sbchk-external >/dev/null
docker run -d --name sbchk-int --network sbchk-internal $ADDHOST "$IMG" sleep 600 >/dev/null
PROXYENV=""   # a machine whose own internet goes through a proxy (not Docker Desktop, which handles that itself)
[ $MAC = 1 ] || { [ -n "${HTTPS_PROXY:-}" ] && PROXYENV="-e HTTPS_PROXY=$HTTPS_PROXY -e HTTP_PROXY=${HTTP_PROXY:-$HTTPS_PROXY} -e NO_PROXY=host.docker.internal"; }
docker run -d --name sbchk-side --network sbchk-external $ADDHOST $PROXYENV "$IMG" python -m http.server 8000 >/dev/null
d network connect sbchk-internal sbchk-side >/dev/null
sleep 2
r="$(cfetch sbchk-int https://pypi.org/simple/)"
case "$r" in 200) record C2a "Internal network -> internet" FAIL "pypi.org answered ($r)" "internal networks are not closed here: the egress design must change";;
  *) record C2a "Internal network -> internet" PASS "blocked ($r)" "a container on an internal network has no way out by itself";; esac
if [ -n "$HOSTPORT" ]; then
  r="$(cfetch sbchk-int "http://host.docker.internal:$HOSTPORT/")"
  case "$r" in 200) record C2b "Internal network -> host service" INFO "reachable ($r)" "the egress proxy could run on the host itself";;
    *) record C2b "Internal network -> host service" INFO "blocked ($r)" "the egress proxy cannot be a plain host process: use a sidecar container on both networks (C2c/C2d)";; esac
else
  record C2b "Internal network -> host service" SKIP "no python on the host to stand in for the SinoBee server" "set SINOBEE_PY=/path/to/python and run again"
fi
r="$(cfetch sbchk-int http://sbchk-side:8000/)"
case "$r" in 200) record C2c "Internal container -> sidecar by name" PASS "HTTP $r" "the sandbox can reach a proxy container on the same internal network";;
  *) record C2c "Internal container -> sidecar by name" FAIL "$r" "the sidecar proxy design does not work as planned";; esac
r="$(cfetch sbchk-side https://pypi.org/simple/)"
r2="skipped"; [ -n "$HOSTPORT" ] && r2="$(cfetch sbchk-side "http://host.docker.internal:$HOSTPORT/")"
if [ "$r" = "200" ]; then
  record C2d "Sidecar -> internet / host" PASS "pypi.org $r, host service $r2" \
    "C1 = sidecar proxy on both networks; it asks the SinoBee server (host.docker.internal) which domains are allowed"
else
  record C2d "Sidecar -> internet / host" FAIL "pypi.org $r, host service $r2" "check Docker Desktop proxy / firewall settings"
fi

# ---------------------------------------------------------------- 3. workspace mount: speed and file owners
mkdir -p "$ROOT/ws"
[ $MAC = 1 ] || chmod 777 "$ROOT/ws"     # Linux test runs: let uid 1000 write (Docker Desktop maps owners itself)
echo "from the host" > "$ROOT/ws/hostfile.txt"
[ $MAC = 1 ] || chmod 666 "$ROOT/ws/hostfile.txt"
d volume create sbchk-vol >/dev/null
docker run --rm -v sbchk-vol:/vol "$IMG" chmod 777 /vol >/dev/null 2>&1
BENCH='import os, sys, time
d = sys.argv[1]; os.makedirs(d, exist_ok=True)
t = time.time()
for i in range(5000):
    sub = os.path.join(d, "p%02d" % (i % 50)); os.makedirs(sub, exist_ok=True)
    with open(os.path.join(sub, "f%05d.js" % i), "w") as f: f.write("x" * 1024)
w = time.time() - t; t = time.time(); n = 0
for root, dirs, files in os.walk(d):
    for fn in files:
        with open(os.path.join(root, fn)) as f: f.read(); n += 1
r = time.time() - t
print("%d %d %d" % (w * 1000, r * 1000, n))'
read -r MW MR MN <<EOF
$(docker run --rm --user 1000:1000 -v "$ROOT/ws:/ws" "$IMG" python -c "$BENCH" /ws/many 2>/dev/null)
EOF
read -r VW VR VN <<EOF
$(docker run --rm --user 1000:1000 -v sbchk-vol:/vol "$IMG" python -c "$BENCH" /vol/many 2>/dev/null)
EOF
if [ -n "${MW:-}" ] && [ -n "${VW:-}" ]; then
  ratio="$(perl -e "printf '%.1f', ($MW+$MR) / (($VW+$VR) || 1)")"
  record C3a "5,000 small files: bind mount vs volume" INFO "mount write ${MW} ms + read ${MR} ms; volume write ${VW} ms + read ${VR} ms (x$ratio)" \
    "$(perl -e "print(($MW+$MR) > 3*($VW+$VR) ? 'node_modules / site-packages belong in the per-conversation volume, as planned' : 'the mount is fast enough; the volume is still the safer default')")"
else
  record C3a "5,000 small files" FAIL "benchmark did not run" ""
fi
docker run --rm --user 1000:1000 -v "$ROOT/ws:/ws" "$IMG" sh -c 'echo "from the sandbox" > /ws/sandbox.txt; echo appended >> /ws/hostfile.txt' >/dev/null 2>&1
if [ $MAC = 1 ]; then owner="$(stat -f '%Su %Sp' "$ROOT/ws/sandbox.txt" 2>/dev/null)"; else owner="$(stat -c '%U %A' "$ROOT/ws/sandbox.txt" 2>/dev/null)"; fi
canedit=no; { echo "host edit" >> "$ROOT/ws/sandbox.txt" && rm "$ROOT/ws/sandbox.txt"; } 2>/dev/null && canedit=yes
hostappend=no; grep -q appended "$ROOT/ws/hostfile.txt" 2>/dev/null && hostappend=yes
if [ "$canedit" = yes ] && [ "$hostappend" = yes ]; then
  record C3b "File owners across the mount (container uid 1000)" PASS "sandbox file on the host: $owner; host can edit/delete it; sandbox can append to a host file" \
    "no uid mapping work needed for the workspace"
else
  record C3b "File owners across the mount (container uid 1000)" FAIL "owner $owner; host edit $canedit; sandbox append $hostappend" \
    "B1 must match the container user to your uid, or fix permissions after each run"
fi

# ---------------------------------------------------------------- 4. a copy made after the container started
mkdir -p "$ROOT/stage" "$ROOT/src/docs"
for i in $(seq 1 2000); do echo "doc $i" > "$ROOT/src/docs/n$i.txt"; done
dd if=/dev/zero of="$ROOT/src/big.bin" bs=1048576 count=200 2>/dev/null
docker run -d --name sbchk-stage --user 1000:1000 -v "$ROOT/stage:/stage:ro" "$IMG" sleep 600 >/dev/null
sleep 1
t0="$(now)"
if [ $MAC = 1 ]; then cp -c -R "$ROOT/src" "$ROOT/stage/c1" 2>/dev/null; cprc=$?; how="cp -c (APFS clone)"
else cp -R --reflink=auto "$ROOT/src" "$ROOT/stage/c1" 2>/dev/null; cprc=$?; how="cp --reflink=auto"; fi
CP_MS="$(ms_since "$t0")"
seen="$(docker exec sbchk-stage sh -c 'ls /stage/c1/docs | wc -l' 2>/dev/null | tr -d ' ')"
bigok="$(docker exec sbchk-stage sh -c 'test -s /stage/c1/big.bin && echo yes' 2>/dev/null)"
ro="$(docker exec sbchk-stage sh -c 'echo x > /stage/c1/w.txt 2>&1 && echo writable || echo readonly' 2>/dev/null)"
if [ "$cprc" = 0 ] && [ "$seen" = 2000 ] && [ "$bigok" = yes ]; then
  record C4a "Copy into an already-mounted folder" PASS "$how of 2,001 files + 200 MB took ${CP_MS} ms; the running container sees all of it at once ($ro)" \
    "plan E2: pre-mount ~/.sinobee/sandbox/<conversation>/ro and /rw; granting a folder = clone into it, no container rebuild"
else
  record C4a "Copy into an already-mounted folder" FAIL "copy rc $cprc, seen $seen files, big file ${bigok:-no}" "E2 keeps the rebuild-on-grant design"
fi
if [ $MAC = 1 ]; then
  echo "changed in the copy" >> "$ROOT/stage/c1/docs/n1.txt"
  if grep -q "changed in the copy" "$ROOT/src/docs/n1.txt"; then
    record C4b "Clone is independent of the original" FAIL "editing the copy changed the original" "E3 must not use cp -c"
  else
    used="$(du -sk "$ROOT/stage/c1" | cut -f1)"
    record C4b "Clone is independent of the original" PASS "editing the copy left the original as it was (du of the copy ${used} KB; APFS shares unchanged blocks)" \
      "E3 copy-on-write staging works as planned"
  fi
else
  record C4b "Clone is independent of the original" SKIP "APFS only - run on the Mac" ""
fi

# ---------------------------------------------------------------- 6. limits and hardening the sandbox will use
CPUS=4; [ "$NCPU" -lt 4 ] && CPUS="$NCPU"
lim="$(docker run --rm --cpus "$CPUS" --memory 8g --pids-limit 512 "$IMG" sh -c 'cat /sys/fs/cgroup/memory.max 2>/dev/null || cat /sys/fs/cgroup/memory/memory.limit_in_bytes; cat /sys/fs/cgroup/pids.max 2>/dev/null || echo ?' 2>&1 | tr '\n' ' ')"
case "$lim" in *8589934592*) record C6 "CPU / memory / process limits" PASS "memory.max 8 GB, pids $(echo $lim | awk '{print $2}')" "B2 resource limits work";;
  *) record C6 "CPU / memory / process limits" INFO "$lim" "check that Docker Desktop's VM has at least 8 GB; lower the default otherwise";; esac
hard="$(docker run --rm --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --user 1000:1000 "$IMG" \
  sh -c 'echo ok > /tmp/a && echo tmp-ok; (echo x > /etc/x) 2>/dev/null && echo etc-WRITABLE || echo etc-readonly; id -u' 2>&1 | tr '\n' ' ')"
case "$hard" in *tmp-ok*etc-readonly*1000*) record C7 "Read-only root, no capabilities, uid 1000" PASS "$hard" "B1 hardening runs on this Docker";;
  *) record C7 "Read-only root, no capabilities, uid 1000" FAIL "$hard" "B1 needs a different hardening set";; esac

# ---------------------------------------------------------------- report
{
  echo "# SinoBee 沙盒檢查報告"
  echo
  echo "$(date '+%Y-%m-%d %H:%M') · $OS $(uname -m) · Docker $SV · $OSNAME"
  echo
  echo "| 項目 | 結果 | 實測 | 對計畫的影響 |"
  echo "|---|---|---|---|"
  while IFS="$(printf '\t')" read -r id title st detail impact; do
    echo "| $id $title | $st | $detail | $impact |"
  done < "$TSV"
  echo
  echo "請把這份報告貼回對話（或附上 $OUT）。"
} > "$OUT"
FAILS="$(awk -F'\t' '$3=="FAIL"' "$TSV" | wc -l | tr -d ' ')"
say ""
say "Report: $OUT  ($FAILS failed)"
