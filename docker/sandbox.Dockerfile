# SinoBee sandbox: where the agent's tools run (sandbox plan B1). Not the SinoBee server: models, memory, settings
# and keys stay on your computer. One container per conversation, started by sinobee/sandbox/manager.py:
#   --read-only (writable: /workspace mount, /home/bee volume, /tmp tmpfs), --user 1000, --cap-drop ALL,
#   --security-opt no-new-privileges, an internal network whose only way out is the egress proxy.
#
#   docker build -f docker/sandbox.Dockerfile -t sinobee-sandbox:<version> .          (or: sinobee sandbox build)
#   docker build -f docker/sandbox.Dockerfile --build-arg OFFICE=1 -t sinobee-sandbox-office:<version> .
FROM node:24.22.1-bookworm-slim AS node

FROM python:3.12-slim-bookworm
ARG OFFICE=0
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DISPLAY=:99 \
    SCREEN_GEOMETRY=1440x900x24 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
    LANG=C.UTF-8 LC_ALL=C.UTF-8 TZ=Asia/Taipei \
    HOME=/home/bee \
    SINOBEE_IN_SANDBOX=1

# desktop (Xvfb + fluxbox), live view (x11vnc + websockify), mouse/keyboard/clipboard/windows, fonts, tools
RUN apt-get update && apt-get install -y --no-install-recommends \
      xvfb fluxbox x11vnc websockify xdotool xclip wmctrl xterm pcmanfm \
      fonts-noto-cjk fonts-noto-color-emoji fonts-dejavu-core \
      ca-certificates curl wget git procps tini tzdata nano jq unzip zip ffmpeg imagemagick sqlite3 \
    && if [ "$OFFICE" = "1" ]; then apt-get install -y --no-install-recommends \
         libreoffice-writer libreoffice-calc libreoffice-impress libreoffice-gtk3 fonts-noto-cjk-extra; fi \
    && rm -rf /var/lib/apt/lists/*

# the same Python packages as `sinobee[all]` (skills import them, one imports sinobee itself), and a Chromium
WORKDIR /opt/sinobee
COPY pyproject.toml README.md ./
COPY sinobee ./sinobee
RUN pip install ".[all]" yt-dlp \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rx /opt/pw-browsers \
    && ln -sf "$(find /opt/pw-browsers -path '*/chromium-*/chrome-linux*/chrome' -type f | head -n 1)" /usr/bin/chromium \
    && test -x /usr/bin/chromium \
    && rm -rf /root/.cache

# Node.js with npm / npx
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# the agent's user. /home/bee is a volume per conversation: pip --user, npm -g, caches and node projects
# that need fast small files live there (a bind-mounted /workspace is ~10x slower for that on macOS)
RUN useradd -m -s /bin/bash -u 1000 bee && mkdir -p /workspace && chown bee:bee /workspace
COPY docker/sandbox-init.sh /usr/local/bin/sandbox-init
RUN chmod 755 /usr/local/bin/sandbox-init
ENV PATH=/home/bee/.local/bin:/home/bee/.npm-global/bin:$PATH \
    npm_config_prefix=/home/bee/.npm-global \
    npm_config_cache=/home/bee/.cache/npm \
    PIP_USER=1 PIP_BREAK_SYSTEM_PACKAGES=1 \
    XDG_CACHE_HOME=/home/bee/.cache
LABEL org.sinobee.role="sandbox"
USER bee
WORKDIR /workspace
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/sandbox-init"]
CMD ["sleep", "infinity"]
