# Memory compression

## Pipeline
```
select -> cluster -> merge -> safety check -> preview (dry_run) -> apply
```
1. **Select:** all current memories, or the ones matching `query` / `ids` / `tier` / `kind` / `tag`. Archived
   memories are excluded. Permanent memories are excluded unless `include_pinned=true` (or `tier="permanent"`).
2. **Cluster:** greedy grouping. Memories join a group when they have the same `kind` and share enough words
   (Jaccard ≥ 0.2), or share a tag and half that overlap. A single memory longer than 400 characters is a group
   of its own, so it gets condensed.
3. **Merge:** the auxiliary model rewrites each group as one statement in the input language. It keeps every
   fact, number, path, name, date and condition, keeps the newest version of a contradiction, and adds nothing.
   Without a model (or if the model fails) an extractive merge is used: most detailed first, statements whose words are
   ≥ 80% covered by kept ones are dropped.
4. **Safety check:** a merge is rejected when:
   - any number, path, URL or `code` span from the sources is missing from it;
   - or it is not shorter than the sources.

   Then the extractive merge is tried. If that also fails, the group is left as is.
5. **Apply:**
   - one new memory is saved with the group's kind, the highest tier, the highest importance and the union of
     tags; it is pinned if any source was pinned;
   - the sources are marked `superseded_by` the new memory, so they leave recall but stay in the history.

## Reading the report
| Field | Meaning |
|---|---|
| `selected` | memories considered |
| `chars_before` / `chars_after` | size of the selection before and after |
| `clusters[]` | one entry per merge: `sources`, `merged`, `method` (model / extractive), `saved_chars`, `tier`, `pinned` |
| `skipped_pinned` | permanent memories that were not touched |
| `over_target_by`, `forget_candidates` | only with `target_chars`: still too big; lowest-value memories to offer for removal |
| `applied` | false for a preview |

## When to compress
- `memory_stats` shows core over its limit, or many long/short memories on one topic.
- The user asks to tidy up ("整理一下記憶", "記憶太多了").
- Before a big new project, compress the old project's short memories by its tag.

Never compress a single user-named permanent memory without asking. Compression never deletes: removal is
always memory_forget, after a yes.
