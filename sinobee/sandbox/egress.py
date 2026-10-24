"""The sandbox's only way out (sandbox plan C1): an HTTP(S) proxy in its own container.

A sandbox container sits on an internal Docker network with no route out. This proxy sits on both that network
(as `egress`, port 3128) and an ordinary one, and passes a connection on only when the SinoBee server allowed that
domain for that sandbox. The server keeps the decisions; it pushes each sandbox's policy here through a small
control API that Docker publishes on 127.0.0.1 only, and reads the connection log back the same way:

    PUT    /policy/<sandbox ip>   {"conversation": "...", "domains": ["pypi.org", ...], "all": false,
                                   "until": {"example.com": <time>}, "secrets": [{"name", "value", "domains"}]}
    DELETE /policy/<sandbox ip>
    GET    /log?since=<seq>       [{"seq", "t", "ip", "conversation", "host", "port", "method", "allowed", "reason"}]
    GET    /health
    (every call needs the header X-SinoBee-Token: <the token given at start>)

A domain allows itself and its subdomains ("github.com" also allows "api.github.com"); an IP address only when it is
listed itself; one in "until" only until that time. A refused connection gets HTTP 403 whose body says which domain
to ask for.

What goes out is checked for keys (security plan stage 2): a key given to the task ("secrets") may only go to its
own domains - in a plain-HTTP request (request line, headers, body), or in the host name of any connection (a key
spelled into a subdomain). A plain-HTTP request carrying anything that looks like a key (sk-…, ghp_…, AKIA…, a private
key) is refused unless it goes to a domain of a key given to the task. HTTPS content is encrypted end to end and is
not opened: there the domain list is the limit.

The file runs on its own with the standard library (the manager starts it with `python -c <this file>` in a
python:3.12-alpine container), so it imports nothing from SinoBee.
"""
from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import os
import re
import time
from collections import deque
from urllib.parse import quote, urlsplit

PROXY_PORT = 3128
CONTROL_PORT = 8080
TOKEN = os.environ.get("SINOBEE_EGRESS_TOKEN", "")
policies: dict[str, dict] = {}
log: deque = deque(maxlen=5000)
seq = 0


KEYLIKE = re.compile(r"(sk-[A-Za-z0-9_\-]{20,}|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|"
                     r"github_pat_[A-Za-z0-9_]{40,}|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_\-]{35}|"
                     r"glpat-[A-Za-z0-9_\-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)")
SCAN_MAX = 1 << 20


def _in(host: str, domains) -> str | None:
    host = host.lower().rstrip(".")
    try:
        ipaddress.ip_address(host.strip("[]"))
        is_ip = True
    except ValueError:
        is_ip = False
    for d in domains or []:
        d = str(d).lower().strip().lstrip("*.").rstrip(".")
        if d and (host == d or (not is_ip and host.endswith("." + d))):
            return d
    return None


def allowed(ip: str, host: str) -> tuple[bool, str]:
    pol = policies.get(ip)
    if pol is None:
        return False, "not a known sandbox"
    if pol.get("all"):
        return True, "all network allowed"
    d = _in(host, pol.get("domains"))
    if d:
        return True, f"allowed: {d}"
    now = time.time()
    d = _in(host, [k for k, t in (pol.get("until") or {}).items() if t > now])
    if d:
        return True, f"allowed for now: {d}"
    return False, "domain not allowed"


def _variants(value: str) -> list[bytes]:
    v = value.encode()
    out = {v, quote(value, safe="").encode(), base64.b64encode(v).rstrip(b"="),
           base64.urlsafe_b64encode(v).rstrip(b"=")}
    return [x for x in out if len(x) >= 8]


def leak(ip: str, host: str, data: bytes) -> str | None:
    """Why this outgoing request may not go out (it carries a key), or None."""
    pol = policies.get(ip) or {}
    keys = pol.get("secrets") or []
    low_host = host.lower().encode()
    for k in keys:
        value = str(k.get("value") or "")
        if len(value) < 8 or _in(host, k.get("domains")):
            continue
        for v in _variants(value):
            if v in data or v.lower() in low_host:
                return f"carries the key {k.get('name')}, which may only go to {', '.join(k.get('domains') or [])}"
    m = KEYLIKE.search(data.decode("latin-1"))
    if m and not any(_in(host, k.get("domains")) for k in keys):
        return "carries something that looks like a key or token"
    return None


def masked(ip: str, host: str) -> str:
    """A key spelled into a host name never reaches the log."""
    for k in (policies.get(ip) or {}).get("secrets") or []:
        value = str(k.get("value") or "")
        for v in _variants(value) if len(value) >= 8 else []:
            vs = v.decode()
            for form in (vs, vs.lower()):
                host = host.replace(form, f"[key {k.get('name')}]")
    return host


def record(ip: str, host: str, port: int, method: str, ok: bool, reason: str) -> None:
    global seq
    seq += 1
    host = masked(ip, host)
    pol = policies.get(ip) or {}
    log.append({"seq": seq, "t": round(time.time(), 3), "ip": ip, "conversation": pol.get("conversation"),
                "host": host, "port": port, "method": method, "allowed": ok, "reason": reason})


async def pipe(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await r.read(65536)
            if not data:
                break
            w.write(data)
            await w.drain()
    except (ConnectionError, asyncio.CancelledError, OSError):
        pass
    finally:
        try:
            w.close()
        except Exception:  # noqa: BLE001
            pass


def refuse(host: str, why: str | None = None) -> bytes:
    body = (f"SinoBee: this request to {host} was blocked: it {why}. Do not send keys or tokens to other sites; "
            f"tell the user what you were doing.\n" if why else
            f"SinoBee: network access to {host} is not allowed in this sandbox. "
            f"Ask the user for access to {host} (request_network), or work without it.\n").encode()
    return (b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain; charset=utf-8\r\nX-SinoBee-Blocked: " +
            host.encode() + b"\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)


async def handle_proxy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    ip = (writer.get_extra_info("peername") or ("?",))[0]
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ConnectionError):
        writer.close()
        return
    lines = head.decode("latin-1").split("\r\n")
    try:
        method, target, version = lines[0].split(" ", 2)
    except ValueError:
        writer.close()
        return
    if method.upper() == "CONNECT":
        host, _, port_s = target.rpartition(":")
        port = int(port_s or 443)
    else:
        if not target.lower().startswith("http://"):
            writer.write(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
            await writer.drain()
            writer.close()
            return
        u = urlsplit(target)
        host, port = u.hostname or "", u.port or 80
        path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    host = host.strip("[]")
    ok, reason = allowed(ip, host)
    body = b""
    if ok:
        scan = head
        if method.upper() != "CONNECT":
            n = 0
            for l in lines[1:]:
                if l.lower().startswith("content-length:"):
                    try:
                        n = int(l.split(":", 1)[1])
                    except ValueError:
                        n = 0
            if 0 < n <= SCAN_MAX:
                try:
                    body = await asyncio.wait_for(reader.readexactly(n), 30)
                except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError):
                    writer.close()
                    return
            scan = head + body
        why = leak(ip, host, scan)
        if why:
            ok, reason = False, "key: " + why
    record(ip, host, port, method.upper(), ok, reason)
    if not ok:
        writer.write(refuse(host, reason[5:] if reason.startswith("key: ") else None))
        await writer.drain()
        writer.close()
        return
    try:
        up_r, up_w = await asyncio.wait_for(asyncio.open_connection(host, port), 20)
    except (OSError, asyncio.TimeoutError) as e:
        msg = f"SinoBee egress: cannot reach {host}:{port} ({type(e).__name__})\n".encode()
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: " + str(len(msg)).encode() +
                     b"\r\nConnection: close\r\n\r\n" + msg)
        await writer.drain()
        writer.close()
        return
    if method.upper() == "CONNECT":
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
    else:
        headers = [l for l in lines[1:] if l and not l.lower().startswith(("proxy-", "connection:", "keep-alive:"))]
        req = f"{method} {path} {version}\r\n" + "\r\n".join(headers + ["Connection: close"]) + "\r\n\r\n"
        up_w.write(req.encode("latin-1") + body)
        await up_w.drain()
    await asyncio.gather(pipe(reader, up_w), pipe(up_r, writer))


async def handle_control(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    async def answer(code: int, obj) -> None:
        body = json.dumps(obj).encode()
        writer.write(f"HTTP/1.1 {code} X\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n"
                     f"Connection: close\r\n\r\n".encode() + body)
        await writer.drain()
        writer.close()
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
    except Exception:  # noqa: BLE001
        writer.close()
        return
    lines = head.decode("latin-1").split("\r\n")
    method, path = (lines[0].split(" ") + ["", ""])[:2]
    hdr = {l.split(":", 1)[0].strip().lower(): l.split(":", 1)[1].strip() for l in lines[1:] if ":" in l}
    if not TOKEN or hdr.get("x-sinobee-token") != TOKEN:
        return await answer(403, {"error": "token"})
    body = b""
    n = int(hdr.get("content-length") or 0)
    if n:
        body = await reader.readexactly(n)
    route, _, query = path.partition("?")
    if route == "/health":
        return await answer(200, {"ok": True, "sandboxes": len(policies), "seq": seq})
    if route.startswith("/policy/"):
        ip = route[len("/policy/"):]
        if method == "PUT":
            data = json.loads(body or b"{}")
            policies[ip] = {"conversation": data.get("conversation"), "domains": list(data.get("domains") or []),
                            "all": bool(data.get("all")), "until": dict(data.get("until") or {}),
                            "secrets": list(data.get("secrets") or [])}
            return await answer(200, {k: v for k, v in policies[ip].items() if k != "secrets"})
        if method == "DELETE":
            policies.pop(ip, None)
            return await answer(200, {"ok": True})
        if method == "GET":
            return await answer(200, {k: v for k, v in (policies.get(ip) or {}).items() if k != "secrets"})
    if route == "/log":
        since = 0
        for part in query.split("&"):
            if part.startswith("since="):
                try:
                    since = int(part[6:])
                except ValueError:
                    pass
        return await answer(200, [e for e in log if e["seq"] > since])
    return await answer(404, {"error": "not found"})


async def start(host: str = "0.0.0.0", proxy_port: int = PROXY_PORT, control_port: int = CONTROL_PORT):
    proxy = await asyncio.start_server(handle_proxy, host, proxy_port)
    control = await asyncio.start_server(handle_control, host, control_port)
    return proxy, control


async def main() -> None:
    proxy, control = await start()
    async with proxy, control:
        await asyncio.gather(proxy.serve_forever(), control.serve_forever())


if __name__ == "__main__":
    asyncio.run(main())
