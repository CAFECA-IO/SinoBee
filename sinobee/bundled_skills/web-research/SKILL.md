---
name: web-research
description: Researches a question on the web, cross-checks at least two sources and reports findings with citations. Use when the user asks to look up, search, compare or verify facts, prices, news or documentation (查詢、搜尋、比較、調查).
license: MIT
metadata:
  version: 1.0.0
  sinobee-tags: research, web, browser
  sinobee-triggers: 查詢, 搜尋, 研究, 比較, 調查, research, look up, find out, compare
  sinobee-requires-tools: web_search
  author: sinobee
---

# When to Use
Any request that needs current or external information: facts, prices, news, product comparisons, documentation.

# Procedure
1. Restate the question and note what a complete answer needs (entities, dates, numbers) in working_memory.
2. web_search with 2-3 differently-phrased queries (include the user's language AND English for technical topics).
3. Pick the most authoritative results (official sites, primary sources, reputable media); open them with
   web_fetch. If the page is empty, JavaScript-heavy, or needs interaction, use `browser` navigate instead.
4. Extract the specific facts; record each with its URL and date in working_memory `facts`.
5. Cross-check every important claim against a second independent source. Note disagreements.
6. Answer concisely in the user's language; cite sources as a list of URLs; state uncertainty and dates.

# Pitfalls
- Search snippets are often outdated or truncated - always open the page before quoting numbers.
- DuckDuckGo may rate-limit; fall back to `browser` with https://duckduckgo.com/?q=... or https://www.bing.com/search?q=...
- Prices and availability vary by region; say which region/currency the source uses.

# Verification
Every number or claim in the answer maps to a fetched source in working_memory.
