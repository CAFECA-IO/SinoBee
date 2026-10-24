# SinoBee: agent + virtual desktop (Xvfb + fluxbox) + noVNC live view
# base images pinned by digest (security review 2026-10-24); scripts/pin-images.sh refreshes them
FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70

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
      xvfb fluxbox x11vnc supervisor xdotool xclip xterm \
      fonts-noto-cjk fonts-noto-color-emoji fonts-dejavu-core \
      ca-certificates curl git procps tini tzdata nano jq unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/sinobee
COPY pyproject.toml README.md requirements.lock ./
COPY sinobee ./sinobee
# exact versions with hashes, at least 7 days old when locked (scripts/lock.sh): a just-published malicious
# release (LiteLLM 1.107.0, 2026-10-21) does not get in by the next build
RUN pip install --require-hashes -r requirements.lock && pip install --no-deps . \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rx /opt/pw-browsers \
    && ln -sf "$(find /opt/pw-browsers -path '*/chromium-*/chrome-linux*/chrome' -type f | head -n 1)" /usr/bin/chromium
# Node.js for skills that build with node (the bundled `anatomy` skill: Node 20.10+, no packages; Chrome from above)
COPY --from=node:24.22.1-bookworm-slim@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205 /usr/local/bin/node /usr/local/bin/node

COPY docker/supervisord.conf /etc/supervisor/conf.d/sinobee.conf
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh \
    && useradd -m -s /bin/bash -u 1000 bee \
    && mkdir -p /home/bee/.sinobee /home/bee/workspace && chown -R bee:bee /home/bee

USER bee
# ~/.sinobee  : settings, secrets, memory DB, global skills (~/.sinobee/skills), browser profile
# ~/workspace : projects the agent works on (project skills live in <project>/.sinobee/skills)
VOLUME ["/home/bee/.sinobee", "/home/bee/workspace"]
EXPOSE 10024
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD curl -fs http://localhost:10024/api/status >/dev/null || exit 1
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["supervisord", "-n", "-c", "/etc/supervisor/conf.d/sinobee.conf"]
