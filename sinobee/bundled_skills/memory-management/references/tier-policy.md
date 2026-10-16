# Tier policy

## Two independent axes
| Axis | Values | Meaning |
|---|---|---|
| Retention (how long) | short, long, permanent (pinned), archive | when a memory may fade or be removed |
| Placement (where) | core / recalled | core is always in the system prompt; the rest is recalled by relevance each turn |

A memory is in exactly one tier (`core`, `short`, `long` or `archive`). `pinned` is a separate flag that makes a
core or long memory permanent.

## Lifecycle
```
task (working_memory) --distill--> short --(reused or importance >= 0.7 at expiry)--> long --(used >= 5x,
importance >= 0.7, preference/profile/lesson, fits)--> core
short --(expired, unused)--> archive
long --(importance < 0.3, used < 2x, idle > 90 days)--> archive
core --(over 1500 chars)--> least important non-permanent entry moves to long
permanent: none of the arrows above apply; only an explicit user request changes or removes it
```
Consolidation runs after reflection and with `sinobee memory consolidate`.

## Choosing a tier
| Signal in the request or the task | Tier | Importance |
|---|---|---|
| "永久記住" / "forever" / "一定要記得" / "never forget" | permanent | 0.9 |
| Standing rule for every answer (language, format, tone) | core | 0.9 |
| Preference, correction, profile fact | long | 0.8 |
| Lesson from a failure ("X fails unless Y") | long | 0.7-0.8 |
| Environment fact (path, version, account name, never secrets) | long | 0.6 |
| Project decision or status for the coming days | short | 0.5 |
| Intermediate result of the current task | task (working_memory) | - |

When unsure between short and long, choose short: a short memory that gets reused is kept automatically,
while a wrong long memory lingers.

## Settings (config `memory.*`)
| Key | Default | Effect |
|---|---|---|
| `short_ttl_days` | 14 | life of a short memory (override per memory with `ttl_days`) |
| `archive_after_days` | 90 | idle days before an unimportant long memory is archived |
| `core_char_limit` | 1500 | size of the always-in-prompt core |
| `half_life_days` | 30 | recency weighting in recall |
| `write_approval` | false | stage every memory_save for the user's approval |

## Safety
- Every write is scanned; text that looks like a secret or a prompt injection is refused.
- Sub-agents cannot save, update, forget or compress memories.
- The learning reflector never creates permanent memories and never edits them.
