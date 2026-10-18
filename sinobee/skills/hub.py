"""Skill sources and the sinobee.ai Skill Hub client.

Install references
  hub:<publisher>/<name>[@version]     sinobee.ai Skill Hub (also accepted: sinobee:<publisher>/<name>)
  github:<owner>/<repo>[/<path>][@ref] a skill folder inside a GitHub repository
  https://.../SKILL.md                 a single-file skill
  https://.../skill.tar.gz | .zip      an archive containing one skill folder
  /local/path/to/skill                 a folder on disk

Every install is validated against the Agent Skills spec, security-scanned, hashed and recorded in the lock file
with its provenance. Trust is decided here, never by the skill:
  hub + verified publisher + reviewed version + valid ed25519 signature  -> verified
  anything else from outside                                             -> community (unless the user says --trust local)
The hub protocol is specified in docs/SKILL_HUB.md.
"""
from __future__ import annotations

import base64
import io
import json
import re
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path

import httpx

from . import spec
from .manager import SkillError, SkillManager
from .trust import content_hash, file_manifest

MAX_BUNDLE_BYTES = 20 * 1024 * 1024
MAX_FILES = 500
UA = {"User-Agent": "SinoBee-SkillHub/0.1"}


def _safe_extract(data: bytes, dest: Path, kind: str) -> None:
    if len(data) > MAX_BUNDLE_BYTES:
        raise SkillError("bundle too large")
    dest = dest.resolve()

    def check(name: str) -> Path:
        target = (dest / name).resolve()
        if not str(target).startswith(str(dest) + "/") and target != dest:
            raise SkillError(f"unsafe path in archive: {name}")
        return target

    if kind == "zip":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
            if len(infos) > MAX_FILES:
                raise SkillError("too many files in archive")
            for i in infos:
                check(i.filename)
            z.extractall(dest)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as t:
            members = t.getmembers()
            if len(members) > MAX_FILES:
                raise SkillError("too many files in archive")
            for m in members:
                if m.issym() or m.islnk() or m.isdev():
                    raise SkillError(f"links/devices are not allowed in skill bundles: {m.name}")
                check(m.name)
            t.extractall(dest, filter="data") if hasattr(tarfile, "data_filter") else t.extractall(dest)


def _find_skill_dir(root: Path, name_hint: str | None = None) -> Path:
    cands = sorted({p.parent for n in spec.SKILL_FILENAMES for p in root.rglob(n)}, key=lambda p: len(p.parts))
    if name_hint:
        for c in cands:
            if c.name == name_hint:
                return c
    if not cands:
        raise SkillError("no SKILL.md found in source")
    if len(cands) > 1 and not name_hint:
        names = ", ".join(c.name for c in cands[:10])
        raise SkillError(f"source contains several skills ({names}); install one by path")
    return cands[0]


class HubClient:
    def __init__(self, base_url: str = "https://sinobee.ai", *, token: str | None = None,
                 public_keys: list[str] | None = None, timeout: float = 30):
        self.base = base_url.rstrip("/")
        self.token = token
        self.public_keys = public_keys or []
        self.timeout = timeout

    def _get(self, path: str, **params) -> dict:
        try:
            r = httpx.get(f"{self.base}{path}", params=params, headers=UA, timeout=self.timeout, follow_redirects=True)
        except httpx.HTTPError as e:
            raise SkillError(f"skill hub unreachable ({self.base}): {e}")
        if r.status_code == 404:
            raise SkillError(f"not found on skill hub: {path}")
        if r.status_code >= 500 or "html" in r.headers.get("content-type", ""):
            raise SkillError(f"the skill hub at {self.base} is not available (HTTP {r.status_code}); meanwhile install "
                             "from github:owner/repo/path, a URL or a local folder")
        if r.status_code >= 400:
            raise SkillError(f"skill hub error {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError:
            raise SkillError(f"skill hub at {self.base} did not return JSON (the hub may not be live yet)")

    def search(self, query: str, page: int = 1) -> list[dict]:
        return self._get("/api/v1/skills", q=query, page=page).get("items", [])

    def info(self, ref: str) -> dict:
        return self._get(f"/api/v1/skills/{ref}")

    def revocations(self) -> list[str]:
        return self._get("/api/v1/revocations").get("revoked", [])

    def download(self, url: str) -> bytes:
        if url.startswith("/"):
            url = self.base + url
        r = httpx.get(url, headers=UA, timeout=60, follow_redirects=True)
        r.raise_for_status()
        return r.content

    def verify(self, content_hash_: str, signature_b64: str | None) -> bool:
        """ed25519 signature over the ASCII content hash ("sha256:<hex>")."""
        if not signature_b64 or not self.public_keys:
            return False
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        except ImportError:
            return False
        sig = base64.b64decode(signature_b64)
        for k in self.public_keys:
            try:
                Ed25519PublicKey.from_public_bytes(base64.b64decode(k)).verify(sig, content_hash_.encode())
                return True
            except Exception:
                continue
        return False

    def publish(self, bundle: Path, manifest: dict) -> dict:
        if not self.token:
            raise SkillError("publishing needs a sinobee.ai sign-in: run `sinobee login`")
        try:
            r = httpx.post(f"{self.base}/api/v1/skills", headers={**UA, "Authorization": f"Bearer {self.token}"},
                           files={"bundle": (bundle.name, bundle.read_bytes(), "application/gzip")},
                           data={"manifest": json.dumps(manifest)}, timeout=120)
        except httpx.HTTPError as e:
            raise SkillError(f"skill hub unreachable: {e}")
        if r.status_code >= 400:
            raise SkillError(f"publish failed {r.status_code}: {r.text[:300]}")
        return r.json()


def hub_client(config) -> HubClient:
    return HubClient(config.get("skills.hub_url", "https://sinobee.ai"),
                     token=config.secrets.get_secret("sinobee-ai:token") or config.secrets.get("sinobee-hub"),
                     public_keys=config.get("skills.hub_keys") or [])


def install(mgr: SkillManager, ref: str, *, config=None, scope: str = "global", trust: str | None = None,
            force: bool = False) -> dict:
    """Install a skill from any supported reference (see module docstring)."""
    ref = ref.strip()
    with tempfile.TemporaryDirectory(prefix="sinobee-skill-") as tmp:
        tmpd = Path(tmp)
        extra: dict = {}
        default_trust = "community"
        m_hub = re.match(r"^(?:hub|sinobee):([\w.-]+/[\w.-]+)(?:@([\w.+-]+))?$", ref)
        m_gh = re.match(r"^github:([\w.-]+)/([\w.-]+)(/[^@]*)?(?:@([\w./-]+))?$", ref)
        if m_hub:
            if config is None:
                raise SkillError("config required for hub installs")
            client = hub_client(config)
            info = client.info(m_hub.group(1))
            versions = info.get("versions") or []
            want = m_hub.group(2)
            ver = next((v for v in versions if v.get("version") == want), None) if want else (versions[0] if versions else None)
            if not ver:
                raise SkillError(f"version {want or 'latest'} not found for {m_hub.group(1)}")
            _safe_extract(client.download(ver["bundle_url"]), tmpd, "zip" if ver["bundle_url"].endswith(".zip") else "tar")
            src = _find_skill_dir(tmpd, info.get("name"))
            h = content_hash(src)
            if ver.get("sha256") and ver["sha256"] != h:
                raise SkillError(f"hash mismatch: hub says {ver['sha256']}, bundle is {h}")
            sig_ok = client.verify(h, ver.get("signature"))
            verified = bool(sig_ok and ver.get("reviewed") and (info.get("publisher") or {}).get("verified"))
            default_trust = "verified" if verified else "community"
            if (ver.get("scan") or {}).get("verdict") == "dangerous":
                raise SkillError("hub security scan marked this version dangerous")
            extra = {"publisher": (info.get("publisher") or {}).get("id"), "version": ver.get("version"),
                     "signature_ok": sig_ok, "reviewed": bool(ver.get("reviewed")), "hub": client.base}
            source = "hub"
        elif m_gh:
            owner, repo, sub, gref = m_gh.groups()
            url = f"https://codeload.github.com/{owner}/{repo}/tar.gz/{gref or 'HEAD'}"
            r = httpx.get(url, headers=UA, timeout=60, follow_redirects=True)
            if r.status_code >= 400:
                raise SkillError(f"GitHub download failed ({r.status_code}) for {owner}/{repo}")
            _safe_extract(r.content, tmpd, "tar")
            top = next(tmpd.iterdir())
            base = top / sub.strip("/") if sub else top
            src = _find_skill_dir(base) if not any((base / n).exists() for n in spec.SKILL_FILENAMES) else base
            source = "github"
        elif ref.startswith(("http://", "https://")):
            r = httpx.get(ref, headers=UA, timeout=60, follow_redirects=True)
            r.raise_for_status()
            if ref.split("?")[0].lower().endswith(".md") or r.headers.get("content-type", "").startswith("text/"):
                parsed = spec.parse(r.text)
                name = str(parsed.frontmatter.get("name") or "").strip() or spec.suggest_name(ref.rsplit("/", 2)[-2])
                src = tmpd / name
                src.mkdir()
                (src / "SKILL.md").write_text(r.text, encoding="utf-8")
            else:
                _safe_extract(r.content, tmpd, "zip" if ref.lower().endswith(".zip") else "tar")
                src = _find_skill_dir(tmpd)
            source = "url"
        else:
            p = Path(ref).expanduser()
            if not p.is_dir():
                raise SkillError(f"unknown skill reference '{ref}'")
            src = p if any((p / n).exists() for n in spec.SKILL_FILENAMES) else _find_skill_dir(p)
            source = "path"
        return mgr.install_dir(src, source=source, ref=ref, trust=trust or default_trust, scope=scope,
                               extra=extra, overwrite=force)


def export(mgr: SkillManager, name: str, out_dir: Path) -> tuple[Path, dict]:
    """Package a skill for sharing / publishing: <name>-<version>.tar.gz + manifest."""
    s = mgr.get(name)
    fm, _ = spec.normalize(s.frontmatter)
    errors = spec.validate(fm, s.path.name, strict_ascii=True)
    if errors:
        raise SkillError("fix these before exporting: " + "; ".join(errors))
    if s.trust == "blocked":
        raise SkillError(f"blocked skill cannot be exported: {s.reason}")
    manifest = {"spec": "agentskills.io", "name": s.name, "version": s.version, "description": s.description,
                "license": fm.get("license"), "content_hash": content_hash(s.path), "files": file_manifest(s.path),
                "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "client": "sinobee"}
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle = out_dir / f"{s.name}-{s.version}.tar.gz"
    with tarfile.open(bundle, "w:gz") as t:
        for rel in manifest["files"]:
            t.add(s.path / rel, arcname=f"{s.name}/{rel}")
        data = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
        info = tarfile.TarInfo(f"{s.name}/.sinobee-manifest.json")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    return bundle, manifest


def publish(mgr: SkillManager, name: str, config) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        bundle, manifest = export(mgr, name, Path(tmp))
        return hub_client(config).publish(bundle, manifest)


def sync_revocations(mgr: SkillManager, config) -> dict:
    revoked = hub_client(config).revocations()
    mgr.lock.set_revoked(revoked)
    mgr.reload()
    hit = [s.name for s in mgr.skills.values() if s.trust == "blocked" and "revoked" in s.reason]
    return {"revoked_hashes": len(revoked), "blocked_installed": hit}


def check_updates(mgr: SkillManager, config) -> list[dict]:
    client = hub_client(config)
    out = []
    for s in mgr.skills.values():
        entry = mgr.lock.get(s.lock_key) or {}
        if entry.get("source") != "hub":
            continue
        m = re.match(r"^(?:hub|sinobee):([\w.-]+/[\w.-]+)", entry.get("ref", ""))
        if not m:
            continue
        try:
            latest = (client.info(m.group(1)).get("versions") or [{}])[0].get("version")
        except SkillError as e:
            out.append({"name": s.name, "error": str(e)})
            continue
        out.append({"name": s.name, "installed": entry.get("version"), "latest": latest,
                    "update_available": bool(latest and latest != entry.get("version")),
                    "locally_modified": s.modified})
    return out


def update(mgr: SkillManager, name: str, config, force: bool = False) -> dict:
    s = mgr.get(name)
    entry = mgr.lock.get(s.lock_key) or {}
    if entry.get("source") != "hub":
        raise SkillError(f"'{name}' was not installed from the hub")
    if s.modified and not force:
        raise SkillError(f"'{name}' was modified locally; use --force to overwrite (old version kept in history)")
    ref = re.sub(r"@[\w.+-]+$", "", entry["ref"])
    return install(mgr, ref, config=config, scope=s.scope, force=True)
