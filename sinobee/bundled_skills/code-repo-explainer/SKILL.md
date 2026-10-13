---
name: code-repo-explainer
description: Explains a source-code repository (GitHub URL or local folder) - what it is for, architecture, tech stack, how to run it and its current state - grounded in files actually read. Use when the user asks to look into, explain, summarize or review a repo, project or codebase (專案、repo、程式碼、解說、介紹這個專案).
license: MIT
metadata:
  version: 1.0.0
  author: sinobee
  sinobee-tags: code, github, repository, research
  sinobee-triggers: github.com, repo, 專案, 程式碼, 解說, 介紹, codebase, repository
  sinobee-requires-tools: web_fetch
---

# When to Use
The user gives a repository URL (github.com/owner/repo) or a local project folder and wants to know what it is,
how it works or how to use it.

# Procedure
1. Overview first: `web_fetch` the repository URL. For GitHub it returns repo facts, the top-level file list and
   the full README (via the GitHub API, without page noise). For a local folder use `list_dir` (depth 2) and
   `read_file` on README*.
2. If the README answers the question at the level asked, go straight to step 5.
3. For deeper questions (architecture, how a feature works, code quality), get the code:
   `shell`: `mkdir -p ~/workspace/_repos && cd ~/workspace/_repos && (git -C <repo> pull --ff-only || git clone --depth 1 <url>)`.
   If `shell` is unavailable, `web_fetch` individual files: `https://github.com/<owner>/<repo>/blob/<branch>/<path>`.
4. Read in this order and stop when you can explain the system: manifests (package.json, pyproject.toml, go.mod,
   Cargo.toml, foundry.toml, docker-compose.yml) -> docs/ -> entry points (main/app/index, src/ top level) ->
   3-6 core modules. Use `search_files` to locate definitions instead of reading everything.
5. Answer in the user's language with this structure:
   - 一句話定位：what it is and for whom
   - 主要功能
   - 架構：components and how data flows between them (cite file paths)
   - 技術棧
   - 如何執行／部署 (from README or scripts)
   - 目前狀態與限制 (phase, TODOs, simulated parts, missing tests)
   - 值得注意的地方 (risks, interesting design choices)
6. Always finish with an answer, even if partial: say what you could not read and why.

# Gotchas
- Never run the repository's own scripts (setup.sh, make, npm install ...) just to explain it; ask first.
- Private repositories need GITHUB_TOKEN; if the API is refused, fall back to the browser tool and say so.
- Very large READMEs are truncated: fetch the section you need via the file URL instead of guessing.
- Do not invent features from file names alone; say "推測" when you infer.

# Verification
Every factual claim in the answer can be traced to a file or README section you actually read.
