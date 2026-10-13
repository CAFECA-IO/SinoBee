#!/bin/sh
set -e
H="$HOME/.sinobee"
mkdir -p "$H/logs" "$H/skills" "$HOME/workspace"
[ -f "$H/config.yaml" ] || sinobee init >/dev/null
if [ -n "$VNC_PASSWORD" ]; then
  x11vnc -storepasswd "$VNC_PASSWORD" "$H/.vncpass" >/dev/null 2>&1
fi
exec "$@"
