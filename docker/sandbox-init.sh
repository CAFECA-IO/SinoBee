#!/bin/sh
# First process of a SinoBee sandbox container. The home folder is a fresh volume the first time: give it the
# folders the environment points at. The desktop (Xvfb, fluxbox, x11vnc) starts here in plan D.
mkdir -p "$HOME/.local/bin" "$HOME/.npm-global/bin" "$HOME/.cache" "$HOME/projects" 2>/dev/null
exec "$@"
