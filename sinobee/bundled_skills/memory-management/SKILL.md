---
name: memory-management
description: Manages the agent's layered memory - distills the key points of a task into task, short-term, long-term or permanent memories, compresses memory when it grows, and adds or removes memories on the user's instruction. Use when the user asks to remember, forget, pin, review, clean up or compress memories (記住、永久記住、忘記、刪除記憶、整理記憶、壓縮記憶、你記得什麼), or at the end of a long task to keep what matters.
license: MIT
metadata:
  version: 1.0.0
  author: sinobee
  sinobee-tags: memory, recall, compression, knowledge
  sinobee-triggers: 記住, 記得, 永久記住, 不要忘記, 忘記, 忘掉, 刪除記憶, 移除記憶, 整理記憶, 壓縮記憶, 記憶, 你記得什麼, remember, forget, memory, pin, compress memory
  sinobee-requires-tools: memory_save, memory_search, memory_update, memory_forget, memory_compress, memory_stats, working_memory
  sinobee-quickstart: "remember -> memory_search first, then memory_save(tier=short|long|core; permanent only if the user says forever). forget -> memory_forget(query=...) preview, show it, then memory_forget(ids=[...], confirm=true). compress -> memory_stats, memory_compress(dry_run=true), show the merges, then dry_run=false."
---

# When to Use
- The user tells you to remember something ("記住我用 zsh", "永久記住公司統編是 …").
- The user tells you to forget or correct something ("忘掉舊地址", "刪除跟 A 專案有關的記憶").
- The user asks what you remember, or asks you to clean up or compress memory.
- A long or multi-step task has just finished and produced facts worth keeping (distill step below).

Do not use it for the scratch notes of the task you are doing right now: keep those in `working_memory`.

# The four layers
| Layer | Where it lives | Lifetime | Use it for |
|---|---|---|---|
| Task | `working_memory` (goal, plan, done, facts, next) | this task only | progress notes, intermediate results |
| Short | `memory_save(tier="short")` | ~14 days (`ttl_days`); kept as long-term if it gets reused | decisions, file locations and status of the current project |
| Long | `memory_save(tier="long")` | months; fades when unused and unimportant | stable facts, preferences, lessons, environment |
| Permanent | `memory_save(tier="permanent")` | forever; never decays, merges away or expires | only what the user explicitly says to keep forever |

`core` is not a lifetime but a placement: a tiny set (≤ 1500 chars) of standing rules shown in every prompt.
Use `tier="core"` only for rules that apply to nearly every task (language, answer format, who the user is).
A permanent memory can also be core (`memory_update(id, tier="core")` after pinning).

Details and edge cases: `references/tier-policy.md`.

# Procedures

## A. Distill a finished task into memory
1. Read your `working_memory` and the outcome of the task.
2. Pick at most 5 key points. Each is ONE self-contained statement with its subject, e.g.
   "SinoBee 專案的影片輸出在 videos/<slug>/out/final.mp4", not "輸出在 out/".
3. For each point, choose the layer:
   - a user preference, correction or lesson -> `long`, importance 0.8+;
   - a fact about this project that matters for the next days -> `short`;
   - anything the user said to keep forever -> `permanent`.
4. Call `memory_search` with the point first. If a memory already says it, do nothing. If one says something
   outdated, use `memory_update(id, content=...)`. Never save a contradiction next to the old memory.
5. `memory_save` the rest. Never store passwords, keys, tokens or personal IDs.
6. `working_memory(action="clear")` when the task is closed.

## B. Add a memory on request
1. `memory_search` for the topic (find duplicates and conflicts).
2. Choose the layer from the table. Say "永久 / forever / 一定要記得" -> `permanent`; otherwise `long` for
   stable facts and `short` for things tied to the current work.
3. Save or update, then tell the user in one line what you stored and in which layer.

## C. Remove or correct memories on request
1. Find them: `memory_forget(query="<topic>")` returns a PREVIEW and changes nothing. You can narrow it with
   `tier`, `kind` or `tag`.
2. Show the user the preview (id + content) and ask which to remove, unless they already named exactly one.
3. Remove: `memory_forget(ids=[...], confirm=true)`. This archives them (still findable with
   `include_archive`). Use `hard=true` only when the user says to delete for good.
4. Permanent memories are skipped. Pass `confirm_permanent=true` only when the user named that permanent
   memory and confirmed it.
5. Corrections are not removals: use `memory_update(id, content=...)`, which keeps the history.

## D. Compress memory
1. `memory_stats` to see the size per layer and the core usage.
2. `memory_compress(dry_run=true)`, optionally narrowed by `query`, `tier`, `kind` or `tag` and with a
   `target_chars` budget.
3. Show the user the proposed merges (each `merged` text and its sources) and the characters saved.
4. Apply with the same arguments and `dry_run=false`.
5. If the result is still over `target_chars`, the report lists `forget_candidates`. Offer them to the user;
   never remove them without a yes.

How merging works and what it refuses to do: `references/compression.md`.

# Rules
- Permanent memories change only when the user asks. You never create one on your own judgement.
- One statement per memory, in the user's language, with the subject named.
- Never store secrets or one-off details (a single temporary path, a running process id).
- Before removing anything except a single memory the user named exactly, show what will be removed.

# Verification
- After saving, `memory_search` for the point finds it in the intended layer.
- After forgetting, `memory_search` for it no longer finds it (unless it was permanent and skipped).
- After compressing, `memory_stats` shows fewer characters and no number, path or name was lost (the tool
  refuses merges that drop them).
