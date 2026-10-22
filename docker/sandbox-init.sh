#!/bin/sh
# First process of a SinoBee sandbox container (runs as bee).
# - The home folder is a fresh volume the first time: give it the folders the environment points at.
# - The desktop the agent works on (sandbox plan D): Xvfb on :99 with fluxbox, and two VNC servers reachable only
#   from inside the container: 5900 view-only (the live view), 5901 with mouse and keyboard (when you take over).
#   SinoBee reaches them through `docker exec` (sinobee/sandbox/manager.py bridge), never through a published port.
mkdir -p "$HOME/.local/bin" "$HOME/.npm-global/bin" "$HOME/.cache" "$HOME/projects" "$HOME/.fluxbox" 2>/dev/null
[ -f "$HOME/.fluxbox/init" ] || printf 'session.screen0.toolbar.visible: false\nsession.screen0.workspaces: 1\n' > "$HOME/.fluxbox/init"
if [ "${SINOBEE_DESKTOP:-1}" = "1" ]; then
  Xvfb "${DISPLAY:-:99}" -screen 0 "${SCREEN_GEOMETRY:-1440x900x24}" -nolisten tcp -ac >/tmp/xvfb.log 2>&1 &
  i=0; while [ ! -e "/tmp/.X11-unix/X${DISPLAY#:}" ] && [ $i -lt 50 ]; do sleep 0.1; i=$((i+1)); done
  fluxbox >/tmp/fluxbox.log 2>&1 &
  x11vnc -display "${DISPLAY:-:99}" -localhost -rfbport 5900 -viewonly -forever -shared -nopw -quiet -noxdamage \
    -bg -o /tmp/x11vnc-view.log >/dev/null 2>&1
  x11vnc -display "${DISPLAY:-:99}" -localhost -rfbport 5901 -forever -shared -nopw -quiet -noxdamage \
    -bg -o /tmp/x11vnc-control.log >/dev/null 2>&1
fi
exec "$@"
