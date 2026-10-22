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
        "thinking": None,        # THINKING model: plans before a task, advises when it is stuck, judges the result and
                                 # diagnoses failures; never runs a step (None: the execution model also thinks).
                                 # model.default is the EXECUTION model: it runs every step; SinoBee never switches
                                 # models in a task (model.escalate is read as model.thinking for old settings)
        "reviewer": None,        # model for the acceptance review and the independent reviewer; another family than
                                 # the execution model catches what it misses (None: thinking, then default)
        "embedding": None,       # e.g. openai/text-embedding-3-small ; None = BM25 only
        "preference": "balanced",  # sinobee/auto and the fallback pool: speed | quality | cost | balanced (routing.py)
        "pin": None,             # a model locked by the user: used for every role, overrides preference and allocation
        "temperature": 0.3,
        "max_output_tokens": "auto",  # output limit per step: auto = per model (providers/limits.py); a number is cut
                                      # to the model's max. SINOBEE_MAX_OUTPUT_TOKENS overrides. Every token here is
                                      # one less for the conversation in the context window.
        "max_tokens": 8192,      # fallback output limit for a model whose limit is unknown
        "reasoning_max_tokens": 16384,   # used for reasoning/thinking models (gpt-5, o*, gemini-2.5/3, r1 ...)
        "stream": True,          # stream answers from OpenAI-compatible services (providers.<name>.stream overrides)
        "vision": True,          # send screenshots as images
        "context_window": "auto",  # tokens the model accepts; "auto" = known model limits (agent/context.py)
    },
    "providers": {},             # per-provider overrides: {name: {base_url, api_key_env, api_key, transport}}
    "agent": {
        "name": "SinoBee",
        "playtest": "auto",         # sandbox: an interactive page or game is played (playtest) before the task ends; off
        # task budgets (agent/budget.py): baselines by task type, soft checkpoints, per-task cap, partial delivery
        "budget": {
            "enabled": True,
            "checkpoints": [0.5, 0.8],      # shares of the reference budget where the thinking model checks progress
            "min_samples": 8,               # runs of a type before its p50/p90 are used (else the synthetic baseline)
            "synthetic_factor": 2.0,        # synthetic baseline = estimated steps x average per step x this
            "default_step_tokens": 8000,    # per-step average before there is any history
            "default_step_cost": 0.02,
            "initial_max_task_usd": 10.0,   # per-task cap before there are 8 large tasks to compute it from
            "initial_max_task_tokens": 4000000,
            "cap_extend": 1.5,              # "追加" multiplies the cap by this
        },
        # no fixed step limit: a task stops when it stops making progress (agent/progress.py)
        "progress": {
            "k": 15,                # steps in a row without progress before the thinking model is consulted
            "n_code": 8,            # steps in a row that only change code files and still count as progress
            "n_document": 3,        # ... that only reword documents (no structural change)
            "n_other": 5,           # ... other files (data, config, unknown)
            "health_steps": 40,     # every this many steps with the checklist still open: thinking-model check
            "safety_max_steps": 300,  # safety valve per agent - reaching it is a system anomaly (config.yaml only)
            "window": 8,            # steps looked at for loops
            "stall_window": 20,     # steps without any file change in which ...
            "stall_ratio": 0.5,     # ... this share of repeated reads / commands is a stall (exploration_stall)
            "wait_limit": 30,       # polls of a running job in a row (output says still running) before long_wait
            "semantic_check": True,  # the auxiliary model reads the recent steps before the thinking model is asked
        },
        "model_deadline": 240,          # s a thinking / review call may take; after that it is skipped and recorded
        "plan": "auto",                 # auto | always | off: the thinking model plans before the first step (auto:
                                        # skills picked or multi-stage, or a long request)
        "consult_max": 2,
        "run_reports": True,            # every task's cost and output, finished or not (Settings > Quality)
        "failure_reports": True,        # save a report (work log, plan, advice, checks) when a task does not finish
        "failure_diagnosis": True,      # ... and have the thinking model diagnose it (in the background)               # times per task the thinking model is asked for advice when execution is stuck
        "context_budget": "auto",   # optional cap in tokens; "auto" = the model's context window (see model.context_window)
        "tool_output_limit": 12000, # chars per tool result
        "language": "auto",
        "verify": True,             # check the task is really finished before reporting "done"
        "verify_min_tool_calls": 3, # only verify turns that used at least this many tools
        "quality": "auto",          # auto | fast | standard | strict (how hard the result is checked before delivery)
        "reviewer": True,           # strict level: an independent read-only reviewer sub-agent re-checks the result
        "reviewer_iterations": 15,
        "qa": True,                 # check the files a task made (pages, formulas, links …) before it may finish
        "quality_rounds": 2,
        "skill_checks": True,       # run the checks a skill declares (sinobee-checks) on the files it produced
        "language_check": True,    # deliverables and the answer must be in the language the user wrote (or asked for)
        "visual_check": True,      # screenshot every page, drawing and image made, and have a vision model judge them
        "snapshots": True,          # keep a copy of files before write_file/edit_file so a turn can be undone
        "snapshot_days": 14,
        "checklist_after_tool_calls": 4,  # remind the agent to write an acceptance checklist after this many tools        # times an open checklist / failed file check is sent back to be fixed
        "consult_after_errors": 3,  # consecutive tool errors before the thinking model is consulted
        "inline_aux": "auto",       # model for compaction a running task waits on: auto | auxiliary | main
    },
    "permissions": {
        "toolsets": ["shell", "files", "desktop", "browser", "web", "memory", "skills", "usage", "database", "work"],
        "toolsets_v": 2,
        "approval": "confirm",      # sandbox | auto | confirm | readonly
        "allowed_paths": [],        # empty = unrestricted
    },
    "memory": {
        "core_char_limit": 1500,
        "recall_k": 6,
        "recall_min_score": 0.15,
        "recall_min_rel": 0.3,       # relevance alone needed to be recalled (importance and recency cannot carry it)
        "write_approval": False,
        "half_life_days": 30,
        "short_ttl_days": 14,       # short-term memories expire after this; useful ones are kept as long-term
        "archive_after_days": 90,   # unused low-importance long-term memories are archived after this
        "auto_maintain": True,      # tidy memory in the background after turns (rate-limited)
        "maintain_interval_hours": 6,
        "auto_compress": True,      # automatic maintenance may merge related memories (reversible)
        "auto_compress_chars": 12000,   # ... when short + long memory is larger than this
        "auto_compress_min_age_days": 1,  # ... and only memories unchanged for this long
        "schedule": True,           # create the daily "記憶整理" job in the work tab (once)
        "schedule_time": "03:17",
        "flush_on_compact": True,   # context near the limit: save key facts of the summarised part
        "working_char_limit": 3000,     # working memory above half of this is condensed under context pressure
    },
    "skills": {
        "index_mode": "auto",       # auto | all | retrieve
        "auto_suggest": True,       # name the matching skill (trigger words) with its quick start in the context
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
        "auto_resume": True,        # every minute: resume tasks interrupted by a restart / crash (work/recovery.py)
        "auto_resume_max": 2,       # automatic resumes per conversation per day
        "auto_resume_model_max": 3,  # resumes after a temporary model error per conversation per day (1/5/15 min)
        "auto_resume_hours": 24,    # only tasks interrupted within this many hours
    },
    "learning": {
        "enabled": True,
        "min_tool_calls": 4,        # learning signal thresholds
        "quality_rules": True,      # a 👎 may become a quality rule (checked on every task) + a regression eval
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
    "sandbox": {                    # where the tools run (sinobee/sandbox; the sandbox plan)
        "executor": "auto",         # auto = the Docker sandbox when permissions.approval is "sandbox"; host | sandbox
        "image": "standard",        # standard | office (with LibreOffice)
        "cpus": "auto",             # auto = up to 4
        "memory": "auto",           # auto = 60% of Docker's memory, at most 8 GB; or e.g. "6g"
        "idle_minutes": 30,         # a sandbox not used this long is stopped
        "prune_days": 14,           # a stopped sandbox (and its home volume) is removed after this many days
        "network": {
            "mode": "ask",          # ask = only the domains below (and ones you allow); open = everything
            "remembered": ["pypi.org", "files.pythonhosted.org", "registry.npmjs.org"],
        },
        "folders": [],              # authorised folders of this computer: [{"path", "name", "mode": rw|ro|review}]
    },
    "evidence": {                   # pictures SinoBee captures while the agent checks its work (agent/evidence.py)
        "keep_days": 30,            # a conversation's evidence is removed this many days after its last picture
        "max_mb": 1000,             # then the oldest conversations go until all evidence fits in this
    },
    "billing": {                    # what happens when the SinoBee.ai subscription allowance runs out (billing.py)
        "on_exhausted": "ask",      # ask | pause | fallback (your own keys and local models)
        "auto_points": False,       # spend platform points without asking first
        "switch_back_pct": 95,      # after a switch to points / your models, back to the subscription below this use %
        "share_stats": False,       # send anonymous task outcomes to SinoBee.ai (model allocation, P5); off = nothing
    },
    "usage": {
        "daily_budget_usd": None,
        "monthly_budget_usd": None,
        "enforce_budget": False,    # stop calling models once a budget is reached
        "task_max_usd": None,       # per-task cap; None = automatic (agent/budget.py: large-task p95 x 1.5, else initial)
        "task_max_tokens": None,    # billed tokens of own-key calls (a cached read counts at its price share)
        "task_max_quota_pct": None, # SinoBee.ai subscription: share of the 6-hour window one task may use (None = 50)
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
    "server": {"host": "127.0.0.1", "port": 10024, "token": None},
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
        m, a = self.data.setdefault("model", {}), self.data.setdefault("agent", {})
        if not m.get("thinking") and m.get("escalate"):      # settings from before the thinking / execution split
            m["thinking"] = m["escalate"]
        for old, new in (("escalate_after_errors", "consult_after_errors"), ("escalate_after_steps", "consult_after_steps")):
            if old in (data.get("agent") or {}) and new not in (data.get("agent") or {}):
                a[new] = data["agent"][old]

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
        if env.get("SINOBEE_THINKING_MODEL") or env.get("SINOBEE_ESCALATE_MODEL"):
            lock("model.thinking", env.get("SINOBEE_THINKING_MODEL") or env["SINOBEE_ESCALATE_MODEL"])
        if env.get("SINOBEE_REVIEWER_MODEL"):
            lock("model.reviewer", env["SINOBEE_REVIEWER_MODEL"])
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

    def server_token(self) -> str | None:
        """Web UI / API access token: SINOBEE_SERVER_TOKEN or server.token, else the one `sinobee serve --public`
        generated and kept in secrets.json."""
        return self.get("server.token") or self.secrets.get_secret("server_token")

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
