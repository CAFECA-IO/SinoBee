"""Configuration layers (later wins):

  1. built-in defaults
  2. $SINOBEE_HOME/config.yaml        hand-edited file (optional)
  3. $SINOBEE_HOME/settings.json      written by the Web UI settings page
  4. environment variables            given at startup (docker run -e ...); shown read-only ("locked") in the GUI

API keys live separately in $SINOBEE_HOME/secrets.json (see secrets.py).
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "model": {
        "default": None,         # "provider/model"; set in the Web UI or with -e SINOBEE_MODEL=...
        "fallbacks": [],
        "auxiliary": None,       # cheaper model for reflection / compression (None = default)
        "escalate": None,        # stronger model to switch to when a task keeps failing or is too complex
        "embedding": None,       # e.g. openai/text-embedding-3-small ; None = BM25 only
        "temperature": 0.3,
        "max_tokens": 8192,
        "reasoning_max_tokens": 16384,   # used for reasoning/thinking models (gpt-5, o*, gemini-2.5/3, r1 ...)
        "vision": True,          # send screenshots as images
        "context_window": "auto",  # tokens the model accepts; "auto" = known model limits (agent/context.py)
    },
    "providers": {},             # per-provider overrides: {name: {base_url, api_key_env, api_key, transport}}
    "agent": {
        "name": "SinoBee",
        "max_iterations": 60,
        "context_budget": "auto",   # optional cap in tokens; "auto" = the model's context window (see model.context_window)
        "tool_output_limit": 12000, # chars per tool result
        "language": "auto",
        "verify": True,             # check the task is really finished before reporting "done"
        "verify_min_tool_calls": 3, # only verify turns that used at least this many tools
        "escalate_after_errors": 3, # consecutive tool errors before switching to model.escalate
        "escalate_after_steps": 25, # steps in one turn before switching to model.escalate
    },
    "permissions": {
        "toolsets": ["shell", "files", "desktop", "browser", "web", "memory", "skills", "usage", "database", "work"],
        "toolsets_v": 2,
        "approval": "confirm",      # auto | confirm | readonly
        "allowed_paths": [],        # empty = unrestricted
    },
    "memory": {
        "core_char_limit": 1500,
        "recall_k": 6,
        "recall_min_score": 0.15,
        "write_approval": False,
        "half_life_days": 30,
    },
    "skills": {
        "index_mode": "auto",       # auto | all | retrieve
        "index_all_threshold": 20,
        "retrieve_n": 6,
        "auto_create": "auto",      # auto (create as experimental) | suggest (needs user approval) | off
        "revision_threshold": 0.5,  # success rate below -> needs_revision
        "promote_after": 2,         # successful uses before an agent-made skill leaves 'experimental'
        "project_dir": None,        # project whose .sinobee/skills are loaded (default: $SINOBEE_PROJECT or cwd)
        "interop": True,            # also scan .agents/skills (cross-client convention)
        "disabled": [],             # skill names hidden from the agent
        "extra_dirs": [],
        "hub_url": "https://sinobee.ai",
        "hub_keys": [],             # pinned ed25519 public keys of the hub (base64); empty = nothing verified
        "write_approval": False,
    },
    "work": {
        "scheduler": True,          # run scheduled jobs (server only)
        "max_parallel_runs": 2,     # background runs at the same time
        "max_parallel_subagents": 3,
    },
    "learning": {
        "enabled": True,
        "min_tool_calls": 4,        # learning signal thresholds
    },
    "desktop": {
        "backend": "auto",          # auto | xdotool | pyautogui
        "screenshot_max_width": 1280,
    },
    "browser": {
        "headless": None,           # None = headless when no DISPLAY
        "channel": None,
        "viewport": [1280, 800],
    },
    "web": {
        "search_backend": "duckduckgo",  # duckduckgo | brave | tavily
    },
    "usage": {
        "daily_budget_usd": None,
        "monthly_budget_usd": None,
        "enforce_budget": False,    # stop calling models once a budget is reached
        "currency_rate": None,      # optional display conversion, e.g. 32.5 for TWD
        "currency": "USD",
    },
    "local": {                      # locally hosted models (Ollama); selected as model "ollama/<tag>"
        "base_url": None,           # None = auto-discover (localhost, compose sidecar, Docker host)
        "num_ctx": "auto",          # context window requested from the runtime; auto = from hardware
        "keep_alive": "30m",        # how long a model stays in memory after the last request
        "memory_gb": None,          # override detected memory (e.g. runtime on another machine)
        "models_dir": None,         # where a SinoBee-started `ollama serve` stores models
        "think": None,              # None = model default; False disables thinking for faster answers
    },
    "pricing": {},                  # per-model price overrides (USD / 1M tokens), edited in the Web UI
    "server": {"host": "127.0.0.1", "port": 8000, "token": None},
}


def sinobee_home() -> Path:
    home = Path(os.environ.get("SINOBEE_HOME", Path.home() / ".sinobee")).expanduser()
    home.mkdir(parents=True, exist_ok=True)
    return home


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, data: dict | None = None, home: Path | None = None):
        self.home = home or sinobee_home()
        self.data = _deep_merge(DEFAULTS, data or {})
        self.locked: set[str] = set()

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        home = sinobee_home()
        cfg = cls({}, home)
        cfg.config_path = Path(path) if path else home / "config.yaml"
        cfg.reload()
        return cfg

    # ---- layers ----
    @property
    def settings_path(self) -> Path:
        return self.home / "settings.json"

    def gui_settings(self) -> dict:
        try:
            return json.loads(self.settings_path.read_text(encoding="utf-8")) if self.settings_path.exists() else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def reload(self) -> None:
        data: dict = {}
        path = getattr(self, "config_path", self.home / "config.yaml")
        if path.exists():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        merged = _deep_merge(_deep_merge(DEFAULTS, data), self.gui_settings())
        self.data = merged
        self.locked: set[str] = set()
        self._apply_env()

    def save_gui_settings(self, updates: dict, replace: tuple[str, ...] = ("pricing", "providers")) -> list[str]:
        """Merge updates into settings.json; returns dotted keys that were ignored because env locks them."""
        ignored = []

        def strip_locked(d: dict, prefix: str = "") -> dict:
            out = {}
            for k, v in d.items():
                key = f"{prefix}{k}"
                if key in self.locked:
                    ignored.append(key)
                elif isinstance(v, dict) and not key.startswith("providers."):
                    out[k] = strip_locked(v, key + ".")
                else:
                    out[k] = v
            return out

        current = self.gui_settings()
        clean = strip_locked(updates)
        for k in replace:  # whole-dict replacement so entries can be removed from the GUI
            if k in clean:
                current.pop(k, None)
        merged = _deep_merge(current, clean)
        self.settings_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        self.reload()
        return ignored

    def _apply_env(self) -> None:
        env = os.environ

        def lock(dotted: str, value: Any) -> None:
            section, key = dotted.split(".", 1)
            self.data.setdefault(section, {})[key] = value
            self.locked.add(dotted)

        def csv(v: str) -> list[str]:
            return [x.strip() for x in v.split(",") if x.strip()]

        if env.get("SINOBEE_MODEL"):
            lock("model.default", env["SINOBEE_MODEL"])
        if env.get("SINOBEE_FALLBACKS"):
            lock("model.fallbacks", csv(env["SINOBEE_FALLBACKS"]))
        if env.get("SINOBEE_AUX_MODEL"):
            lock("model.auxiliary", env["SINOBEE_AUX_MODEL"])
        if env.get("SINOBEE_ESCALATE_MODEL"):
            lock("model.escalate", env["SINOBEE_ESCALATE_MODEL"])
        if env.get("SINOBEE_EMBEDDING_MODEL"):
            lock("model.embedding", env["SINOBEE_EMBEDDING_MODEL"])
        if env.get("SINOBEE_APPROVAL"):
            lock("permissions.approval", env["SINOBEE_APPROVAL"])
        if env.get("SINOBEE_TOOLSETS"):
            lock("permissions.toolsets", csv(env["SINOBEE_TOOLSETS"]))
        if env.get("SINOBEE_SERVER_TOKEN"):
            lock("server.token", env["SINOBEE_SERVER_TOKEN"])
        if env.get("SINOBEE_PROJECT"):
            lock("skills.project_dir", env["SINOBEE_PROJECT"])
        if env.get("SINOBEE_MONTHLY_BUDGET"):
            lock("usage.monthly_budget_usd", float(env["SINOBEE_MONTHLY_BUDGET"]))
        if env.get("SINOBEE_OLLAMA_URL"):
            lock("local.base_url", env["SINOBEE_OLLAMA_URL"])
        if env.get("SINOBEE_DAILY_BUDGET"):
            lock("usage.daily_budget_usd", float(env["SINOBEE_DAILY_BUDGET"]))

    @property
    def secrets(self):
        from .secrets import SecretStore
        return SecretStore(self.home / "secrets.json")

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    # convenient paths
    @property
    def db_path(self) -> Path:
        return self.home / "sinobee.db"

    @property
    def skills_dir(self) -> Path:
        p = self.home / "skills"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def project_dir(self) -> Path:
        p = os.environ.get("SINOBEE_PROJECT") or self.get("skills.project_dir") or os.getcwd()
        return Path(p).expanduser()

    @property
    def workspace(self) -> Path:
        p = Path(os.environ.get("SINOBEE_WORKSPACE", self.home / "workspace"))
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def browser_profile(self) -> Path:
        p = self.home / "browser-profile"
        p.mkdir(parents=True, exist_ok=True)
        return p
