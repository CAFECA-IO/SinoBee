---
name: code-review
description: Reviews code changes (a branch, a diff, a pull request or a set of files) for real defects - wrong logic, broken edge cases, security holes, missing tests, performance traps - verifies each finding by reading the code and running tests, and reports them ranked with file, line and a concrete failure scenario. Use when the user asks to review, audit, check or security-review code, a PR, a commit or a diff (程式碼審查、code review、PR 審查、資安檢查).
license: MIT
metadata:
  version: 1.0.0
  sinobee-tags: code, review, security, git, pull request
  sinobee-triggers: code review, review, 審查, 程式碼審查, PR, pull request, diff, 資安, 安全性檢查, security review, audit
  sinobee-requires-tools: shell, read_file, search_files
  sinobee-entry: scripts/run.py
  sinobee-quickstart: "1) skill_run(code-review, \"scope <repo> [base]\") for the changed files and hunks; 2) read every changed file in full plus its callers; 3) run the tests/linters it lists; 4) for each suspected defect, prove it (trace, test, or a failing input) or drop it; 5) \"secrets <repo> [base]\"; 6) report findings ranked, file:line, failure scenario, fix."
  author: sinobee
---

# When to Use
The user wants an opinion on code quality or safety: "review this PR", "幫我看這個分支", "any security
issues?", "check my diff before I merge". For explaining a repository use code-repo-explainer; for writing
the fix, do the review first, then fix only what the user asks.

# Procedure
1. **Scope.** `skill_run(code-review, "scope <repo> [base]")` lists the changed files, the diff stat, the
   commits and the hunks (base defaults to the merge base with main/master). For a GitHub PR, fetch the
   branch or use `gh pr diff <n>`. For loose files, review them whole.
2. **Intent.** Read the commit messages / PR description / the user's words: what is this change supposed to
   do? Every finding is judged against that intent.
3. **Read, do not skim.** For each changed file read the whole file (not just the hunk), and search for the
   callers of every changed function (`search_files`). Most real bugs are in how new code meets old code.
4. **Look for defects, in this order:**
   - Correctness: wrong condition or operator, off-by-one, inverted logic, unhandled `None`/empty/zero,
     wrong units, integer/float and time-zone mistakes, state left half-updated on error, async misuse
     (missing await, shared mutable state), resource leaks.
   - Contract breaks: changed signatures, return types or error behaviour that callers still assume; DB
     migrations without backfill; config keys renamed without fallback.
   - Security (`references/security.md`): injection (SQL, shell, template, path), missing authorisation,
     secrets in code or logs, unsafe deserialisation, SSRF, XSS, weak crypto, permissive CORS.
   - Tests: is the new behaviour tested? would the tests fail if the change were wrong?
   - Performance only where it matters: N+1 queries, quadratic loops on user-sized input, unbounded memory.
5. **Run things.** Run the project's tests and linters (`scope` suggests commands it finds). A failing test
   or a reproduced bug is the strongest finding. Write a small test or script to prove a suspected bug when
   you can; never change the user's code during a review unless asked.
6. **Verify every finding** before reporting it: re-read the code path, look for a guard elsewhere that
   already handles it, and drop anything you cannot back with a concrete input → wrong result. Mark the
   rest CONFIRMED (reproduced) or LIKELY (traced, not run).
7. **Secrets.** `skill_run(code-review, "secrets <repo> [base]")` scans the added lines for keys, tokens
   and private keys.

# Report
Most severe first. For each finding:
- **file:line** - one-sentence defect
- Failure scenario: concrete input/state → what goes wrong
- Fix: the smallest change that fixes it
- Status: CONFIRMED / LIKELY
Then a short list of what you checked and found fine, tests run and their result. No style nits unless the
user asked; no praise padding. If nothing survived verification, say so plainly.

# Pitfalls
- Reviewing only the diff hunk: the bug is often in the unchanged line next to it.
- Reporting "potential" issues you did not trace: they waste the author's time and hide the real ones.
- Assuming a test suite passes: run it, quote the summary line.
