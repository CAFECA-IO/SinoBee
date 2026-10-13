"""API-key storage for keys entered through the GUI or `sinobee keys set`.

Stored in $SINOBEE_HOME/secrets.json with 0600 permissions. Keys are never returned in full by the API.
Resolution order for a provider key (highest first):
  1. environment variable given at startup (e.g. `docker run -e OPENAI_API_KEY=...`) - read-only in the GUI
  2. key saved here (GUI / CLI)
  3. providers.<name>.api_key in config.yaml (discouraged)
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path


def mask(key: str | None) -> str:
    if not key:
        return ""
    return ("•" * 6 + key[-4:]) if len(key) > 8 else "•" * len(key)


class SecretStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8")) or {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)

    def api_keys(self) -> dict[str, str]:
        return dict(self._read().get("api_keys") or {})

    def get(self, provider: str) -> str | None:
        return self.api_keys().get(provider) or None

    def set(self, provider: str, key: str | None) -> None:
        with self._lock:
            data = self._read()
            keys = data.setdefault("api_keys", {})
            if key:
                keys[provider] = key.strip()
            else:
                keys.pop(provider, None)
            self._write(data)
