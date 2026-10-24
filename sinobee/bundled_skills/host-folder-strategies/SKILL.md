---
name: host-folder-strategies
description: Chooses and uses the right way to work in a folder of the user's computer from the sandbox - review by git (git repositories of any size), review by a light copy (other folders), review by overlay (non-git folders with too many files to copy), or direct writing with checkpoints (fixing a real environment, or a non-git project too big to copy) - and how to work safely in each. Use before asking for a folder, and when the folder is a large project, a monorepo, a git repository or a live environment to repair (大型專案、巨型專案、monorepo、修復環境、本機資料夾、git 專案).
license: MIT
metadata:
  version: 1.0.0
  sinobee-tags: sandbox, folders, git, large project, checkpoint, review
  sinobee-triggers: 大型專案, 巨型專案, monorepo, 修復, 修好, 實際環境, 本機資料夾, 我的專案, git, repo, checkpoint, 檢查點, 直接寫入, 審核後寫回
  sinobee-requires-tools: host_folder_info, request_host_access, folder_checkpoint
  author: sinobee
---

# When to Use
The user wants you to work on files that live on their computer, not in /workspace: develop or fix their project,
repair a configuration, clean up a folder. The sandbox sees a folder only after `request_host_access`, and HOW it is
given decides what is safe and what scales. Choose deliberately; do not ask for more than the task needs.

# The ways
| Way | strategy | Use when | Cost / limits |
|---|---|---|---|
| Review by git | `review` on a git repository (automatic) | Developing or fixing a git project - any size | Ignored files (.env, build output) are never written back |
| Review by copy | `review` on a non-git folder (automatic) | Documents, small and medium non-git projects | Up to 20,000 reviewed files by default (node_modules, .git, caches not counted) |
| Review by overlay | `review` on a bigger non-git folder (automatic, when Docker supports it) | Huge non-git folders: data sets, asset libraries, old projects | No copy: the sandbox sees their folder with its own changes kept apart; do not ask them to edit the folder while you work |
| Direct, with checkpoints | `direct` | A real environment that must change in place (config folders, a running service's data, a dev setup to repair); a non-git project too big to copy | Changes land at once; a checkpoint is taken before every task and on demand |
| Read only | `ro` | Reading, analysing, comparing | Nothing can change |

`auto` (the default) follows the user's 寫入前審核 switch: on = review, off = direct.

# Procedure
1. **Look before asking.** `host_folder_info(path)` says whether the folder exists, can be given at all, is a git
   repository, how many files a review would compare, and which dependency folders it has. It never shows names or
   contents.
2. **Choose:**
   - Only reading → `ro`.
   - git repository → `review` (by git; size does not matter). Prefer this for any code project that has .git.
   - Not git → `review` (by copy, or by overlay when it has many files - the tool picks).
   - Not git, too big and the tool refuses review (this Docker cannot overlay) → suggest the user run `git init` there (then review by git), work on a smaller
     subfolder, or `direct` - say plainly that direct means their files change at once and that checkpoints let
     them go back.
   - Fixing a real environment whose files must change where they are (e.g. a tool's config folder, a project's
     local database) → `direct`, and say why.
3. **Ask** with `request_host_access(path, reason, strategy)`. The reason names the task and the way, in the
   user's language. If they choose read-only, work read-only.
4. **Work** in /host/<name> (see the environment section of your instructions for the exact paths).

# Working in each way
- **Review by git**: the copy is their repository as it is now - uncommitted and untracked work included, history
  included. `git status`, `git log`, branches and commits work; commit as you like (git
  there is already set up: safe.directory and a sandbox identity). What is reviewed is the
  working tree against how it was, not your commits. Ignored files never reach their folder: if a change matters
  (e.g. a new .env value), tell the user in words.
- **Review by copy**: node_modules, virtualenvs and build caches are not copied - install dependencies in the
  sandbox (`npm ci`, `pip install -r ...`). Edits the user makes in their folder meanwhile reach the copy before
  each task unless you changed the same file (then the review shows a conflict).
- **Review by overlay**: /host/<name> is their folder as it is, with your changes on top (kept in Docker, not in
  their folder). Work as in a copy; dependencies you install there count as changes, so install them elsewhere
  (e.g. /workspace) when you can. Ask the user not to change that folder while the task runs.
- **All review ways**: nothing reaches their folder until they apply it. End by telling them to open
  本機檔案變更 / 審核變更, summarise what changed and why, and point out conflicts.
- **Direct**: a checkpoint exists from the start of the task. Before anything risky (migrations, mass renames,
  deleting, rewriting configs) call `folder_checkpoint(folder, action="make", label="…")`. If something breaks,
  `folder_checkpoint(action="diff", id=…)` shows what changed and `action="restore"` goes back (files made since go
  to the Trash; the state before the restore is kept as a checkpoint, so it can be undone). Tell the user they can
  also restore from 對話設定 → 檢查點.

# Pitfalls
- Programs built on their Mac (node_modules, .venv) do not run in the Linux sandbox - never copy them in; install.
- A huge folder given by copy is refused by the tool: do not retry the same request; choose another way.
- Do not ask for the home folder or a whole disk: pick the project folder.
- In direct mode a checkpoint of a non-git folder over the file limit cannot be made - the tool says so; then work
  in small, reversible steps and tell the user there is no safety net.

# Verification
The user's folder ends in the intended state: in review modes the changes are listed for review (or applied by the
user), in direct mode the result works and the checkpoint list shows where to go back to.
