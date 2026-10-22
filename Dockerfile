# SinoBee: agent + virtual desktop (Xvfb + fluxbox) + noVNC live view
FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DISPLAY=:99 \
    SCREEN_GEOMETRY=1440x900x24 \
    SINOBEE_WORKSPACE=/home/bee/workspace \
    SINOBEE_IN_DOCKER=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
    LANG=zh_TW.UTF-8 LC_ALL=C.UTF-8 TZ=Asia/Taipei

RUN apt-get update && apt-get install -y --no-install-recommends \
      xvfb fluxbox x11vnc novnc websockify supervisor xdotool xclip xterm \
      fonts-noto-cjk fonts-noto-color-emoji fonts-dejavu-core \
      ca-certificates curl git procps tini tzdata nano jq unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/sinobee
COPY pyproject.toml README.md ./
COPY sinobee ./sinobee
RUN pip install ".[all]" yt-dlp \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rx /opt/pw-browsers \
    && ln -sf "$(find /opt/pw-browsers -path '*/chromium-*/chrome-linux*/chrome' -type f | head -n 1)" /usr/bin/chromium
# Node.js for skills that build with node (the bundled `anatomy` skill: Node 20.10+, no packages; Chrome from above)
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node

COPY docker/supervisord.conf /etc/supervisor/conf.d/sinobee.conf
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh \
    && useradd -m -s /bin/bash -u 1000 bee \
    && mkdir -p /home/bee/.sinobee /home/bee/workspace && chown -R bee:bee /home/bee

USER bee
# ~/.sinobee  : settings, secrets, memory DB, global skills (~/.sinobee/skills), browser profile
# ~/workspace : projects the agent works on (project skills live in <project>/.sinobee/skills)
VOLUME ["/home/bee/.sinobee", "/home/bee/workspace"]
EXPOSE 10024 6080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD curl -fs http://localhost:10024/api/status >/dev/null || exit 1
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["supervisord", "-n", "-c", "/etc/supervisor/conf.d/sinobee.conf"]
