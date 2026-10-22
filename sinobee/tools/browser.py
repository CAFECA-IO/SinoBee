"""Browser automation with Playwright (persistent profile, element-ref snapshots)."""
from __future__ import annotations

import asyncio
import os
import platform

from ..types import ImagePart, TextPart
from .registry import ToolContext, tool

SNAPSHOT_JS = r"""
(maxEls) => {
  document.querySelectorAll('[data-sb-ref]').forEach(e => e.removeAttribute('data-sb-ref'));
  const sel = 'a[href],button,input,textarea,select,summary,[role=button],[role=link],[role=tab],[role=menuitem],' +
              '[role=checkbox],[role=radio],[role=option],[role=switch],[role=combobox],[role=textbox],' +
              '[contenteditable=""],[contenteditable=true],[onclick],[tabindex]:not([tabindex="-1"])';
  const vh = window.innerHeight, vw = window.innerWidth;
  const items = [];
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (r.width < 2 || r.height < 2 || st.visibility === 'hidden' || st.display === 'none' || st.opacity === '0') continue;
    if (el.disabled) continue;
    const inView = r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw;
    items.push({el, r, inView});
  }
  // in-viewport first, then reading order (row bucket, then left-to-right)
  items.sort((a, b) => (b.inView - a.inView) || (Math.round(a.r.top / 12) - Math.round(b.r.top / 12)) || (a.r.left - b.r.left));
  const out = [];
  let n = 0;
  for (const {el, r, inView} of items.slice(0, maxEls)) {
    n += 1;
    el.setAttribute('data-sb-ref', String(n));
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role') || '';
    const type = el.getAttribute('type') || '';
    let label = (el.getAttribute('aria-label') || el.innerText || el.value || el.getAttribute('placeholder') ||
                 el.getAttribute('title') || el.getAttribute('alt') || el.getAttribute('name') || '').trim();
    if (tag === 'select') label = 'selected: ' + ((el.options[el.selectedIndex] || {}).text || '');
    label = label.replace(/\s+/g, ' ').slice(0, 80);
    let extra = '';
    if (tag === 'a') extra = ' -> ' + (el.getAttribute('href') || '').slice(0, 80);
    if (tag === 'input' || tag === 'textarea') {
      if (el.value && type !== 'password') extra += ' value="' + String(el.value).slice(0, 40) + '"';
      if (el.checked) extra += ' [checked]';
      if (el.getAttribute('placeholder')) extra += ' placeholder="' + el.getAttribute('placeholder').slice(0, 40) + '"';
    }
    if (tag === 'select') extra = ' options=[' + Array.from(el.options).slice(0, 8).map(o => o.text.trim()).join('|') + ']';
    out.push(`[${n}] ${tag}${role ? ' role=' + role : ''}${type ? ' type=' + type : ''} "${label}"${extra}${inView ? '' : ' (offscreen)'}`);
  }
  const text = (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n');
  return {title: document.title, url: location.href, elements: out, total: items.length, text,
          scrollY: Math.round(scrollY), scrollH: document.documentElement.scrollHeight, vh};
}
"""

_lock = asyncio.Lock()
_states: dict[str, dict] = {}    # "host" (this computer's browser) or "sandbox:<conversation>" -> pw, context, tab


def available(config=None) -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def _key(ctx: ToolContext) -> str:
    from ..sandbox import executor_of
    ex = executor_of(ctx)
    return f"sandbox:{ex.conversation}" if ex.kind == "sandbox" else "host"


def _st(ctx: ToolContext) -> dict:
    return _states.setdefault(_key(ctx), {})


SANDBOX_CHROME = ("chromium --remote-debugging-port=9222 --user-data-dir=/home/bee/.config/sinobee-chromium "
                  "--no-first-run --no-default-browser-check --no-sandbox --disable-dev-shm-usage "
                  "--start-maximized --disable-blink-features=AutomationControlled --lang=zh-TW "
                  "--disable-background-networking --disable-component-update --disable-sync --no-pings "
                  "--disable-default-apps --disable-domain-reliability --metrics-recording-only "
                  "--disable-features=OptimizationHints,MediaRouter,AutofillServerCommunication "
                  "--proxy-server=http://egress:3128 --proxy-bypass-list='localhost;127.0.0.1;[::1]' "
                  "about:blank")


async def _sandbox_context(ctx: ToolContext, st: dict):
    """The Chromium on the sandbox's desktop (you see it in the live view), driven over DevTools (plan D3)."""
    import json as _json
    import urllib.request
    from ..sandbox import executor_of
    ex = executor_of(ctx)
    m, conv = ex.manager, ex.conversation
    name = m.name_of(conv)
    proc = await asyncio.create_subprocess_exec("docker", "exec", name, "pgrep", "-f", "remote-debugging-port=9222",
                                                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    if await proc.wait() != 0:
        await (await asyncio.create_subprocess_exec(
            "docker", "exec", "-d", "-e", "DISPLAY=:99", name, "bash", "-lc",
            f"exec {SANDBOX_CHROME} >/tmp/chromium.log 2>&1")).wait()
    port = await m.bridge(conv, 9222)

    def version():
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open(f"http://127.0.0.1:{port}/json/version", timeout=3) as r:
            return _json.loads(r.read())
    info = None
    for _ in range(60):
        try:
            info = await asyncio.to_thread(version)
            break
        except Exception:  # noqa: BLE001
            await asyncio.sleep(0.25)
    if not info:
        raise RuntimeError("the sandbox browser did not start (see /tmp/chromium.log in the sandbox)")
    ws = info["webSocketDebuggerUrl"]
    ws = "ws://127.0.0.1:%d/%s" % (port, ws.split("/", 3)[3])
    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    browser = await pw.chromium.connect_over_cdp(ws)
    bctx = browser.contexts[0] if browser.contexts else await browser.new_context()
    st.update(pw=pw, browser=browser, context=bctx, current=0, loop=asyncio.get_running_loop())
    return bctx


async def _context(ctx: ToolContext):
    async with _lock:
        st = _st(ctx)
        if st.get("loop") is not None and st["loop"] is not asyncio.get_running_loop():
            st.clear()                       # made by another event loop (a CLI run, a test): start again
        bctx = st.get("context")
        if bctx is not None:
            try:
                _ = bctx.pages
                if st.get("browser") is None or st["browser"].is_connected():
                    return bctx
            except Exception:
                pass
            st.clear()
        if _key(ctx) != "host":
            return await _sandbox_context(ctx, st)
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        headless = ctx.config.get("browser.headless")
        if headless is None:
            headless = platform.system() == "Linux" and not os.environ.get("DISPLAY")
        vw, vh = ctx.config.get("browser.viewport", [1280, 800])
        args = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]
        kwargs = dict(headless=headless, locale=ctx.config.get("browser.locale", "zh-TW"),
                      ignore_default_args=["--enable-automation"], args=args)
        if headless:
            kwargs["viewport"] = {"width": vw, "height": vh}
        else:  # headed on a (virtual) desktop: real window, maximised, shares the screen with desktop tools
            kwargs["no_viewport"] = True
            args += ["--start-maximized"]
        if ctx.config.get("browser.channel"):
            kwargs["channel"] = ctx.config.get("browser.channel")
        if os.environ.get("SINOBEE_CHROMIUM"):
            kwargs["executable_path"] = os.environ["SINOBEE_CHROMIUM"]
        bctx = await pw.chromium.launch_persistent_context(str(ctx.config.browser_profile), **kwargs)
        st.update(pw=pw, context=bctx, current=0, loop=asyncio.get_running_loop())
        return bctx


async def _page(ctx: ToolContext, new: bool = False):
    bctx = await _context(ctx)
    _state = _st(ctx)
    pages = bctx.pages
    if new or not pages:
        page = await bctx.new_page()
        pages = bctx.pages
        _state["current"] = len(pages) - 1
        return page
    idx = min(_state.get("current", 0), len(pages) - 1)
    return pages[idx]


async def _snapshot(page, max_elements: int = 120, text_chars: int = 2500) -> str:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=8000)
    except Exception:
        pass
    data = await page.evaluate(SNAPSHOT_JS, max_elements)
    text = data["text"].strip()
    if len(text) > text_chars:
        text = text[:text_chars] + f"\n...[{len(data['text']) - text_chars} more chars; use action=get_text]"
    more = data["total"] - len(data["elements"])
    return (f"Page: {data['title']}\nURL: {data['url']}\n"
            f"Scroll: {data['scrollY']}/{data['scrollH']} (viewport {data['vh']})\n\n"
            f"Interactive elements (use ref numbers):\n" + "\n".join(data["elements"]) +
            (f"\n... {more} more elements (scroll to reach them)" if more > 0 else "") +
            f"\n\nVisible text:\n{text}")


def _loc(page, ref=None, selector=None, text=None):
    if ref is not None and str(ref).strip():
        return page.locator(f'[data-sb-ref="{str(ref).strip()}"]').first
    if selector:
        return page.locator(selector).first
    if text:
        return page.get_by_text(text, exact=False).first
    raise ValueError("provide ref (from snapshot), selector, or text")


def _danger(args: dict) -> str | None:
    if args.get("action") == "evaluate":
        return "runs arbitrary JavaScript in the page"
    return None


@tool("browser", "browser",
      "Control a real Chromium browser (persistent profile keeps logins). Typical flow: navigate -> read the "
      "snapshot -> click/type using [ref] numbers -> the new snapshot is returned automatically. "
      "actions: navigate(url) | snapshot | click(ref) | type(ref, text, submit?) | press(key) | hover(ref) | "
      "select(ref, value) | scroll(direction, amount) | back | forward | reload | get_text(selector?) | "
      "screenshot(full_page?) | wait(seconds | selector) | tabs | new_tab(url) | switch_tab(index) | close_tab | "
      "evaluate(script) | upload(ref, path)",
      {"properties": {
          "action": {"type": "string"},
          "url": {"type": "string"},
          "ref": {"type": "string", "description": "element ref number from the latest snapshot"},
          "selector": {"type": "string", "description": "CSS/Playwright selector (alternative to ref)"},
          "text": {"type": "string", "description": "text to type, or visible text to click when no ref"},
          "submit": {"type": "boolean", "description": "press Enter after typing"},
          "key": {"type": "string", "description": "e.g. Enter, Escape, Control+A"},
          "value": {"type": "string"},
          "direction": {"type": "string", "enum": ["up", "down"]},
          "amount": {"type": "number", "description": "scroll amount in viewport heights (default 0.8)"},
          "full_page": {"type": "boolean"},
          "seconds": {"type": "number"},
          "index": {"type": "integer"},
          "script": {"type": "string"},
          "path": {"type": "string"},
      }, "required": ["action"]},
      writes=True, dangerous=_danger, check=available)
async def browser(ctx: ToolContext, action: str, url: str | None = None, ref: str | None = None,
                  selector: str | None = None, text: str | None = None, submit: bool = False, key: str | None = None,
                  value: str | None = None, direction: str = "down", amount: float = 0.8, full_page: bool = False,
                  seconds: float | None = None, index: int | None = None, script: str | None = None,
                  path: str | None = None):
    action = action.lower().strip()
    from ..sandbox import executor_of
    ex = executor_of(ctx)
    if ex.kind == "sandbox" and ex.conversation in ex.manager.takeover:
        return f"ERROR: {ex.TAKEN}"
    _state = _st(ctx)
    if action == "new_tab":
        page = await _page(ctx, new=True)
        if url:
            await page.goto(url if "://" in url else "https://" + url, wait_until="domcontentloaded", timeout=45000)
        return await _snapshot(page)
    page = await _page(ctx)
    bctx = _state["context"]

    if action == "navigate":
        if not url:
            return "ERROR: url required"
        target = url if "://" in url or url.startswith("about:") else "https://" + url
        try:
            await page.goto(target, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:  # noqa: BLE001
            if ex.kind == "sandbox" and ("ERR_TUNNEL_CONNECTION_FAILED" in str(e) or "ERR_PROXY" in str(e)):
                from urllib.parse import urlsplit
                host = urlsplit(target).hostname or target
                return (f"ERROR: the sandbox network does not allow {host}. If the task needs it, call "
                        f"request_network(domains=['{host}'], reason=...); otherwise work without it.")
            raise
        await asyncio.sleep(0.8)
        return await _snapshot(page)
    if action == "snapshot":
        return await _snapshot(page)
    if action == "click":
        loc = _loc(page, ref, selector, text)
        await loc.scroll_into_view_if_needed(timeout=5000)
        n_pages = len(bctx.pages)
        await loc.click(timeout=10000)
        await asyncio.sleep(1.0)
        if len(bctx.pages) > n_pages:  # click opened a new tab -> follow it
            _state["current"] = len(bctx.pages) - 1
            page = bctx.pages[-1]
        return "clicked\n\n" + await _snapshot(page)
    if action == "type":
        loc = _loc(page, ref, selector)
        await loc.click(timeout=10000)
        if await loc.evaluate("e => 'value' in e"):
            await loc.fill("", timeout=5000)
        else:  # contenteditable
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Delete")
        await loc.type(text or "", delay=15)
        if submit:
            await loc.press("Enter")
            await asyncio.sleep(1.5)
        return f"typed into [{ref or selector}]\n\n" + await _snapshot(page)
    if action == "press":
        await page.keyboard.press(key or "Enter")
        await asyncio.sleep(0.8)
        return f"pressed {key}\n\n" + await _snapshot(page)
    if action == "hover":
        await _loc(page, ref, selector, text).hover(timeout=10000)
        return "hovered\n\n" + await _snapshot(page)
    if action == "select":
        if not value:
            return "ERROR: value required"
        loc = _loc(page, ref, selector)
        try:
            await loc.select_option(label=value, timeout=5000)
        except Exception:
            await loc.select_option(value=value, timeout=5000)
        return f"selected {value}\n\n" + await _snapshot(page)
    if action == "scroll":
        sign = -1 if direction == "up" else 1
        await page.evaluate("(d) => window.scrollBy(0, d * window.innerHeight)", sign * float(amount or 0.8))
        await asyncio.sleep(0.5)
        return await _snapshot(page)
    if action in ("back", "forward", "reload"):
        await getattr(page, {"back": "go_back", "forward": "go_forward", "reload": "reload"}[action])()
        await asyncio.sleep(0.8)
        return await _snapshot(page)
    if action == "get_text":
        if selector:
            content = await page.locator(selector).first.inner_text(timeout=10000)
        else:
            import html2text
            h = html2text.HTML2Text()
            h.body_width = 0
            h.ignore_images = True
            content = h.handle(await page.content())
        return f"URL: {page.url}\n\n{content}"
    if action == "screenshot":
        from ..agent.evidence import MASK_JS, capture, secret_snippets
        try:                                 # passwords and stored secrets are blurred before a picture is kept
            await page.evaluate(MASK_JS, secret_snippets(ctx.config))
        except Exception:  # noqa: BLE001
            pass
        png = await page.screenshot(full_page=full_page)
        ev = capture(ctx, png, f"瀏覽器截圖：{page.url}", "browser")
        if not ctx.config.get("model.vision", True):
            return "screenshot taken, but the model has no vision; use snapshot instead" + \
                (f" (evidence {ev['id']})" if ev else "")
        return [TextPart(f"Browser screenshot of {page.url}" + (f". Evidence {ev['id']}" if ev else "")),
                ImagePart.from_bytes(png)]
    if action == "wait":
        if selector:
            await page.wait_for_selector(selector, timeout=int((seconds or 15) * 1000))
        else:
            await asyncio.sleep(min(float(seconds or 2), 60))
        return await _snapshot(page)
    if action == "tabs":
        lines = []
        for i, p in enumerate(bctx.pages):
            mark = "*" if i == _state.get("current", 0) else " "
            lines.append(f"{mark}[{i}] {await p.title()} - {p.url}")
        return "\n".join(lines) or "no tabs"
    if action == "switch_tab":
        if index is None or not 0 <= index < len(bctx.pages):
            return "ERROR: valid index required (see action=tabs)"
        _state["current"] = index
        await bctx.pages[index].bring_to_front()
        return await _snapshot(bctx.pages[index])
    if action == "close_tab":
        await page.close()
        _state["current"] = max(0, len(bctx.pages) - 1)
        return "closed tab"
    if action == "evaluate":
        result = await page.evaluate(script or "document.title")
        return f"{result}"
    if action == "upload":
        if not path:
            return "ERROR: path required"
        host = ex.paths.to_host(path, ctx.config.workspace).resolve()
        ex.check(host)
        import mimetypes                   # sent as content: the browser may be in the sandbox
        await _loc(page, ref, selector).set_input_files(
            {"name": host.name, "mimeType": mimetypes.guess_type(host.name)[0] or "application/octet-stream",
             "buffer": host.read_bytes()})
        return f"uploaded {path}"
    return f"ERROR: unknown action '{action}'"


async def shutdown():
    loop = asyncio.get_running_loop()
    for st in list(_states.values()):
        if st.get("loop") is not None and st["loop"] is not loop:
            continue                         # its event loop is gone: nothing left to close from here
        try:
            if st.get("browser") is not None:        # the sandbox's browser keeps running in its container
                await st["browser"].close()
            elif st.get("context") is not None:
                await st["context"].close()
            if st.get("pw") is not None:
                await st["pw"].stop()
        except Exception:
            pass
    _states.clear()
