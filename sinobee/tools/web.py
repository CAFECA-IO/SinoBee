"""Web search and fetch (no browser needed)."""
from __future__ import annotations

import os
import re
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from .registry import ToolContext, tool

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0 Safari/537.36 SinoBee/0.1")


async def _ddg(query: str, n: int) -> list[dict]:
    from bs4 import BeautifulSoup
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}, follow_redirects=True) as c:
        r = await c.post("https://html.duckduckgo.com/html/", data={"q": query})
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for res in soup.select(".result"):
        a = res.select_one(".result__a")
        if not a:
            continue
        href = a.get("href", "")
        if "uddg=" in href:
            href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        snip = res.select_one(".result__snippet")
        out.append({"title": a.get_text(" ", strip=True), "url": href,
                    "snippet": snip.get_text(" ", strip=True) if snip else ""})
        if len(out) >= n:
            break
    return out


async def _brave(query: str, n: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": n},
                        headers={"X-Subscription-Token": os.environ["BRAVE_API_KEY"], "Accept": "application/json"})
    r.raise_for_status()
    return [{"title": x.get("title"), "url": x.get("url"), "snippet": x.get("description", "")}
            for x in (r.json().get("web") or {}).get("results", [])[:n]]


async def _tavily(query: str, n: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post("https://api.tavily.com/search",
                         json={"api_key": os.environ["TAVILY_API_KEY"], "query": query, "max_results": n})
    r.raise_for_status()
    return [{"title": x.get("title"), "url": x.get("url"), "snippet": x.get("content", "")[:400]}
            for x in r.json().get("results", [])]


@tool("web_search", "web", "Search the web. Returns titles, URLs and snippets. Follow up with web_fetch or the browser.",
      {"properties": {"query": {"type": "string"}, "max_results": {"type": "integer", "description": "default 8"}},
       "required": ["query"]})
async def web_search(ctx: ToolContext, query: str, max_results: int = 8):
    backend = ctx.config.get("web.search_backend", "duckduckgo")
    if backend == "brave" and os.environ.get("BRAVE_API_KEY"):
        results = await _brave(query, max_results)
    elif backend == "tavily" and os.environ.get("TAVILY_API_KEY"):
        results = await _tavily(query, max_results)
    else:
        results = await _ddg(query, max_results)
    if not results:
        return "no results (search backend may be rate-limited; try the browser tool with a search engine)"
    return "\n\n".join(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}" for i, r in enumerate(results, 1))


NOISE_TAGS = ("script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg", "iframe", "template")
GITHUB_RE = re.compile(r"^https?://(?:www\.)?github\.com/([^/#?]+)/([^/#?]+?)(?:\.git)?(?:/(tree|blob)/([^/#?]+)(?:/([^#?]*))?)?/?(?:[#?].*)?$")


def main_content(html: str) -> str:
    """Readable main content: drop navigation/boilerplate, prefer <main>/<article>, convert to markdown."""
    import html2text
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(NOISE_TAGS):
        t.decompose()
    for t in soup.select('[role=navigation], [role=banner], [role=contentinfo], [aria-hidden=true], .sr-only'):
        t.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    best = None
    for sel in ("article", "main", "[role=main]", "#content", ".content", "#readme"):
        for el in soup.select(sel):
            if len(el.get_text(" ", strip=True)) > 400 and (best is None or len(el.get_text()) > len(best.get_text())):
                best = el
        if best is not None:
            break
    h = html2text.HTML2Text()
    h.ignore_images = True
    h.body_width = 0
    body = h.handle(str(best if best is not None else (soup.body or soup)))
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return (f"# {title}\n\n" if title else "") + body


async def _github(url: str, max_chars: int) -> str | None:
    """github.com repo/tree/blob URLs -> repository facts, file list and README via the GitHub API (no HTML noise)."""
    m = GITHUB_RE.match(url)
    if not m or m.group(1) in ("orgs", "topics", "search", "settings", "features", "marketplace"):
        return None
    owner, repo, kind, ref, path = m.groups()
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    api = f"https://api.github.com/repos/{owner}/{repo}"
    async with httpx.AsyncClient(timeout=30, headers=headers, follow_redirects=True) as c:
        info = await c.get(api)
        if info.status_code != 200:
            return None  # private / rate-limited -> fall back to HTML
        d = info.json()
        ref = ref or d.get("default_branch", "main")
        if kind == "blob" and path:
            raw = await c.get(f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}")
            body = raw.text if raw.status_code == 200 else f"(could not read file: HTTP {raw.status_code})"
            return f"# {owner}/{repo} - {path} @ {ref}\n\n```\n{body[:max_chars]}\n```"
        listing = await c.get(f"{api}/contents/{path or ''}", params={"ref": ref})
        files = listing.json() if listing.status_code == 200 and isinstance(listing.json(), list) else []
        readme_url = f"{api}/readme/{path}" if path else f"{api}/readme"
        rd = await c.get(readme_url, params={"ref": ref}, headers={"Accept": "application/vnd.github.raw"})
        readme = rd.text if rd.status_code == 200 else ""
        langs = await c.get(f"{api}/languages")
        lang_txt = ", ".join(list(langs.json())[:6]) if langs.status_code == 200 else (d.get("language") or "")
    facts = [f"description: {d.get('description') or '-'}", f"languages: {lang_txt or '-'}",
             f"default branch: {d.get('default_branch')}", f"stars: {d.get('stargazers_count')}, forks: {d.get('forks_count')}",
             f"last push: {d.get('pushed_at')}", f"license: {(d.get('license') or {}).get('spdx_id') or '-'}",
             f"topics: {', '.join(d.get('topics') or []) or '-'}", f"homepage: {d.get('homepage') or '-'}"]
    tree = "\n".join(f"- {f['name']}{'/' if f.get('type') == 'dir' else ''}" for f in files[:200])
    out = (f"# GitHub repository {owner}/{repo}" + (f" /{path}" if path else "") + f" @ {ref}\n\n"
           + "\n".join(facts) + f"\n\n## Files\n{tree or '(none listed)'}\n\n## README\n{readme or '(no README)'}"
           + f"\n\n---\nTo analyze the code itself: shell `git clone --depth 1 https://github.com/{owner}/{repo}` "
           "into the workspace, then read files; or web_fetch a file URL (github.com/<owner>/<repo>/blob/<ref>/<path>).")
    return out


@tool("web_fetch", "web", "Fetch a URL and return its main content as markdown/text, with navigation and boilerplate "
      "removed. GitHub repository/file URLs return repo facts, the file list and the README via the GitHub API. "
      "Use the browser tool for JavaScript-heavy pages or interaction.",
      {"properties": {"url": {"type": "string"}, "max_chars": {"type": "integer", "description": "default 20000"}},
       "required": ["url"]})
async def web_fetch(ctx: ToolContext, url: str, max_chars: int = 20000):
    try:
        gh = await _github(url, max_chars)
    except httpx.HTTPError:
        gh = None
    if gh:
        text, status, final = gh, 200, url
    else:
        async with httpx.AsyncClient(timeout=30, headers={"User-Agent": UA}, follow_redirects=True) as c:
            r = await c.get(url)
        ctype = r.headers.get("content-type", "")
        status, final = r.status_code, r.url
        if "html" in ctype:
            text = main_content(r.text)
        elif ctype.startswith("text/") or "json" in ctype or "xml" in ctype:
            text = r.text
        else:
            return f"[{r.status_code}] {url} returned non-text content ({ctype}, {len(r.content)} bytes)"
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n...[truncated, {len(text) - max_chars} more chars; ask for a narrower URL]"
    return f"[{status}] {final}\n\n{text}"
