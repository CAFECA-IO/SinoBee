---
name: skill-authoring
description: Writes and improves Agent Skills (SKILL.md) that follow the agentskills.io specification and SinoBee conventions. Use when the user asks to learn, remember or save a procedure as a skill, or after finishing a reusable non-trivial task (學會、記住這個流程、做成技能).
license: MIT
metadata:
  version: 2.0.0
  author: sinobee
  sinobee-tags: meta, skills, learning
  sinobee-triggers: 技能, skill, 學會, 記住這個流程, 做成技能, remember how, learn
---

# When to Use
The user asks you to learn / remember / save a procedure, sends `/learn ...`, or you just finished a non-trivial
task that is likely to recur and no existing skill covers it.

# Procedure
1. `skill_search` first. If a skill already covers the task, improve it with `skill_patch` (or `skill_fork` when it
   is builtin/verified/community and therefore read-only for you) instead of creating a near-duplicate.
2. Ground the skill in what actually happened: the steps that worked, the user's corrections, exact commands,
   URLs, selectors, file paths and input/output formats. Add what an agent would NOT know; omit general knowledge.
3. Name: lowercase a-z, 0-9 and single hyphens, max 64 chars, describing the capability
   (`einvoice-download-tw`, not `fix-on-tuesday`). It must equal the folder name.
4. Frontmatter - ONLY these top-level fields are allowed by the Agent Skills spec:
   ```
   ---
   name: <name>
   description: <what it does AND when to use it, with trigger keywords; <= 1024 chars>
   license: <optional>
   compatibility: <optional environment needs, <= 500 chars>
   allowed-tools: <optional, space-separated, e.g. Bash(git:*) browser>
   metadata:
     version: "1.0.0"
     sinobee-tags: comma, separated
     sinobee-triggers: keywords in the user's languages
     sinobee-requires-tools: browser, web_search
   ---
   ```
   Metadata values are strings. Never use flow style like `[a, b]`.
   When the skill produces files and a script can verify them, declare it so SinoBee runs it before the task may
   finish: `sinobee-checks: "scripts/check.py {file}"` (`{file}` / `{dir}` are filled in; exit 0 or JSON with an
   empty `problems` list means pass) and `sinobee-checks-on: "reports/*.md"` (which produced files it applies to).
5. Body (< 500 lines, ideally far less): `# When to Use`, `# Procedure` (numbered, with one clear default rather
   than a menu of options), `# Gotchas` (what failed and the fix), `# Verification` (how to confirm success).
6. Put long material in `references/<topic>.md` and say WHEN to read it ("read references/errors.md if the API
   returns 4xx"). Put deterministic helpers in `scripts/` (non-interactive, `--help`, clear errors, structured
   output) via `skill_write_file`. Keep file references one level deep.
7. Choose scope: `project` when the procedure only makes sense in the current project (its repo, its build), else
   `global`.
8. After using any skill, call `skill_feedback`. When it failed, patch the wrong step once you know what works.

# Gotchas
- Incident logs ("on 3/2 the button moved") do not generalize; write the rule ("locate the button by its label").
- Never include secrets, personal data or one-off values.
- Skills you create start as trust `agent` / status `experimental`; they become `active` after succeeding twice or
  when the user approves them.

# Verification
`skill_view <name>` shows the content and the creation result reports no validation errors.
