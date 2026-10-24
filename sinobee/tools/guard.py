"""What the tools may not touch, and what happens once a secret has been seen (security review 2026-10-24, item 3).

The lethal trifecta - private data, outside content, a way out - was open in the default "confirm" mode: read_file
could read ~/.sinobee/secrets.json or ~/.ssh without asking, and web_fetch / the browser could carry it out in a URL.

- **Never reachable by the file tools** (`blocked`): SinoBee's own data - everything in its data folder except the
  workspace, the skills folder and the review copies of your folders (settings, API keys, memory, conversation files
  that list the folders a conversation was given) - and credential stores in your home: ~/.ssh, ~/.aws, ~/.gnupg,
  ~/.kube, cloud CLIs, keychains, browser profiles, ~/.netrc, ~/.git-credentials. `permissions.allow_sensitive_paths`
  opens a path on purpose.
- **Secret seen** (`note_secret`): reading a file that usually holds secrets (.env, *.pem, *.key, id_*, credentials
  *.json …), a tool output that contains a key or token, or a key the task was given (request_secret). From then
  on in this conversation, anything that can send data out asks first, also where it would not (`outbound`): web
  search and fetch, the browser, a shell command on this computer.
- **Outside content is marked** (`wrap_untrusted`): web and browser output reaches the model inside
  <untrusted_content>, which the system prompt says never to follow.
- SinoBee's own server is not a web site for the tools (`own_server`): web_fetch / the browser on this computer never
  open http://localhost:<SinoBee's port>.
"""
from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from urllib.parse import urlsplit

CREDENTIAL_DIRS = (".ssh", ".aws", ".gnupg", ".kube", ".azure", ".oci", ".password-store", ".docker",
                   ".config/gcloud", ".config/gh", ".config/op", ".config/google-chrome", ".config/chromium",
                   ".config/BraveSoftware", ".mozilla", "Library/Keychains", "Library/Cookies",
                   "Library/Application Support/Google/Chrome", "Library/Application Support/Firefox",
                   "Library/Application Support/BraveSoftware", "Library/Application Support/Microsoft Edge",
                   "Library/Application Support/1Password", "Library/Group Containers/2BUA8C4S2C.com.1password")
CREDENTIAL_FILES = (".netrc", ".git-credentials", ".pypirc", ".npmrc", ".pgpass", ".my.cnf", ".vault-token")
SECRETISH = (".env", ".env.*", "*.pem", "*.key", "id_rsa*", "id_ed25519*", "id_ecdsa*", "*.p12", "*.pfx",
             "*.keystore", "*.jks", "credentials*.json", "*credentials*", "service-account*.json", "*.kdbx",
             "secrets.*", "*.tfstate")
SECRET_TEXT = re.compile(r"(sk-[A-Za-z0-9_\-]{20,}|sk-ant-[A-Za-z0-9_\-]{20,}|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|"
                         r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|xox[baprs]-[A-Za-z0-9-]{10,}|"
                         r"AIza[0-9A-Za-z_\-]{35}|glpat-[A-Za-z0-9_\-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
                         r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})")
READ_ONLY_BROWSER = {"snapshot", "screenshot", "get_text", "tabs", "wait", "scroll", "switch_tab", "close_tab"}
UNTRUSTED_OPEN, UNTRUSTED_CLOSE = "<untrusted_content", "</untrusted_content>"


def _under(p: Path, root: Path) -> bool:
    return p == root or root in p.parents


def _resolve(x) -> Path | None:
    try:
        return Path(x).expanduser().resolve()
    except (OSError, RuntimeError):
        return None


def blocked(config, p: Path) -> str | None:
    """Why the file tools may never reach this path, or None."""
    p = Path(p)
    for a in config.get("permissions.allow_sensitive_paths") or []:
        r = _resolve(a)
        if r is not None and _under(p, r):
            return None
    from ..profiles import base_dir
    roots = {r for r in (_resolve(base_dir()), _resolve(config.home)) if r is not None}
    for base in roots:
        if _under(p, base):
            ok = [_resolve(config.workspace), _resolve(config.skills_dir)]
            copies = base / "sandbox"
            if _under(p, copies):                       # sandbox/<conversation>/host/<folder>/...: review copies
                rel = p.relative_to(copies).parts
                if len(rel) >= 3 and rel[1] in ("host", "base"):
                    return None
            rel = p.relative_to(base).parts
            in_skills = (rel[:1] == ("skills",)) or (len(rel) > 2 and rel[0] == "profiles" and rel[2] == "skills")
            if in_skills or any(r is not None and _under(p, r) for r in ok):
                from ..fileaccess import PRIVATE_NAMES
                return "SinoBee's private settings file" if p.name in PRIVATE_NAMES else None
            return "SinoBee's own data (settings, API keys, memory, conversation files)"
    home = _resolve(Path.home())
    if home is not None and _under(p, home):
        for d in CREDENTIAL_DIRS:
            if _under(p, home / d):
                return f"a credential store (~/{d})"
        if p.parent == home and p.name in CREDENTIAL_FILES:
            return f"a credential file (~/{p.name})"
    return None


def secretish(p: Path) -> bool:
    name = Path(p).name.lower()
    return any(fnmatch.fnmatch(name, pat) for pat in SECRETISH) and name not in (".env.example", ".env.sample",
                                                                                  ".env.template")


def note_secret(ctx, why: str) -> None:
    """A secret was seen in this conversation: outbound tools ask from now on."""
    if ctx is not None and not ctx.state.get("secret_seen"):
        ctx.state["secret_seen"] = why


def has_secret(text: str) -> bool:
    return bool(text) and bool(SECRET_TEXT.search(text))


def outbound(tool, args: dict, ctx) -> str | None:
    """After a secret was seen: why this call needs your approval (it could carry the secret out), or None."""
    seen = (ctx.state or {}).get("secret_seen")
    if not seen:
        return None
    from ..sandbox import executor_of
    if tool.toolset == "web":
        pass
    elif tool.toolset == "browser":
        if str(args.get("action", "")).lower().strip() in READ_ONLY_BROWSER:
            return None
    elif tool.name in ("shell", "skill_run"):
        if executor_of(ctx).kind == "sandbox":        # its network is the egress proxy's (scanned, per domain)
            return None
    else:
        return None
    return f"this conversation has seen a secret ({seen}); this call could send it out"


def own_server(config, url: str) -> bool:
    """Is this URL SinoBee's own server (the tools on this computer must not drive its API or page)?"""
    try:
        u = urlsplit(url if "://" in url else "http://" + url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    port = u.port or (443 if u.scheme == "https" else 80)
    if host not in ("localhost", "127.0.0.1", "::1", "0.0.0.0") and not host.endswith(".localhost"):
        return False
    return port == int(config.get("server.port", 10024) or 10024)


def wrap_untrusted(text: str, source: str) -> str:
    """Outside content as data: a closing tag inside it cannot end the block."""
    body = re.sub(r"</?\s*untrusted_content[^>]*>", "[tag removed]", text, flags=re.IGNORECASE)
    src = re.sub(r"[\"<>\n]", "", str(source))[:200]
    return f'{UNTRUSTED_OPEN} source="{src}">\n{body}\n{UNTRUSTED_CLOSE}'
