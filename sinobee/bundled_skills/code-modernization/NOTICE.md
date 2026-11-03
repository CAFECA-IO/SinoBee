# NOTICE

**code-modernization** — Copyright Anthropic.
Source: https://github.com/anthropics/claude-plugins-official/tree/main/plugins/code-modernization
(commit a2e251d9b97b909bf728b3e84fc8d17e6aba0063, plugin version 1.0.0), licensed under the Apache License,
Version 2.0 (see `LICENSE`).

This folder is a **modified** SinoBee edition packaged by CAFECA. It is not an official Anthropic release and is not
endorsed by Anthropic.

## Changes from upstream (Apache-2.0 §4(b))
- `commands/*.md` → `references/<step>.md` (`modernize.md` → `start.md`, `modernize-<step>.md` → `<step>.md`):
  frontmatter replaced by a short header; `/code-modernization:modernize-<step>` → `/code-modernization <step>`;
  `${CLAUDE_PLUGIN_ROOT}` → `<skill>`; `AskUserQuestion` → `ask_user`; the quoted example of a hostile instruction
  reworded so SinoBee's Skill Hub scan does not flag the defensive text itself.
- `references/preflight.md` Check 7 rewritten: SinoBee protects the legacy source with a read-only folder grant
  instead of Claude Code permission rules.
- `agents/*.md` → `references/agent-<name>.md`: frontmatter replaced by a header; run through SinoBee's `delegate`.
- `scripts/build_report.py`, `scripts/proof_pack.py`: next-step commands use `/code-modernization <step>`;
  `scripts/tests/test_report_next.py` updated to match. All other scripts unchanged.
- `README.md` kept as `references/upstream-README.md` (same rewording as above).
- New: `SKILL.md`, this `NOTICE.md`.
- Not included: `workflows/` (Claude Code Workflow scripts), `hooks/` (Claude Code function hooks: live pane, x-ray,
  review deck, sign dialog, fleet view), `scripts/telemetry.py`, `scripts/telemetry.sh`,
  `scripts/tests/test_telemetry.py`, `tests/` (TypeScript), `.claude-plugin/`, `tsconfig.json`, `CHANGELOG.md`,
  `assets/media/`, `assets/topology-viewer-screenshot.jpg`. No usage data is collected by this edition.

## Third-party
- `assets/vendor/mermaid.min.js` — Mermaid, MIT License (`assets/vendor/LICENSE-mermaid.txt`).
