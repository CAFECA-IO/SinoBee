"""Share files on sinobee.ai from a task: check them (share_check), then - only after the user confirms - upload
them (share_files). Procedure: bundled skill share-to-sinobee.

Everything happens on this computer, not in the sandbox: the check reads the real files (a sandbox script's verdict
could be faked by what the task read), the upload uses the user's sinobee.ai sign-in, which the sandbox never sees.
An upload is a copy that leaves the computer, so share_files always asks the user - in every approval mode - and
refuses when nobody is there to ask (a scheduled task cannot share by itself).
"""
from __future__ import annotations

import asyncio
import mimetypes
import os
from pathlib import Path

from ..types import ToolResult
from .registry import ToolContext, tool

PUBLIC, LINK, NO = "公開分享（列在 sinobee.ai/shares）", "只給知道連結的人", "不要分享"
# how long the share stays up, asked right after who may see it: (label, days; 0 = until revoked, description)
DURATIONS = [("7 天", 7, "一週後自動失效"), ("30 天", 30, "一個月後自動失效"), ("不過期", 0, "直到你在 sinobee.ai/shares 撤回")]
WARN_LINK = {"pii_national_id", "pii_card", "pii_phone", "pii_email", "gps", "stored_secret", "api_key", "token",
             "private_key", "password", "connection_string"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".cache", ".venv", "venv"}


def _conv(ctx: ToolContext) -> str | None:
    return (ctx.state or {}).get("sandbox_conversation") or ctx.session_id or None


def _expand(ctx: ToolContext, paths: list[str]) -> list[Path]:
    """The files named (a folder means its files, hidden ones and caches left out), on this computer."""
    from ..fileaccess import FileAccessError, resolve_file
    from ..sandbox import for_config
    out: list[Path] = []
    for raw in paths or []:
        raw = str(raw).strip()
        if not raw:
            continue
        try:
            out.append(resolve_file(ctx.config, raw, _conv(ctx)))
            continue
        except FileAccessError as e:
            if getattr(e, "status", 0) == 403:
                raise ValueError(f"{raw}：{e}") from None
        host = for_config(ctx.config, _conv(ctx)).to_host(raw, ctx.config.workspace).resolve()
        if not host.is_dir():
            raise ValueError(f"{raw}：找不到這個檔案或資料夾")
        for p in sorted(host.rglob("*")):
            rel = p.relative_to(host).parts
            if p.is_file() and not any(x.startswith(".") or x in SKIP_DIRS for x in rel):
                try:
                    out.append(resolve_file(ctx.config, str(p), _conv(ctx)))
                except FileAccessError as e:
                    raise ValueError(f"{p.name}：{e}") from None
            if len(out) > 200:
                break
    seen, uniq = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    if not uniq:
        raise ValueError("沒有指定要分享的檔案")
    return uniq


def _index_md(title: str, description: str, names: list[tuple[str, int, str]]) -> str:
    lines = [f"# {title}", ""]
    if description:
        lines += [description, ""]
    for name, size, mime in names:
        kb = f"{size / 1024:.0f} KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f} MB"
        link = name.replace(" ", "%20")
        if mime.startswith("image/"):
            lines += [f"![{Path(name).name}]({link})", ""]
        lines.append(f"- [{name}]({link})（{kb}）")
    return "\n".join(lines) + "\n"


def build(ctx: ToolContext, paths: list[str], title: str | None = None, description: str | None = None) -> dict:
    """The share body (the same shape as the GUI's document share) and [(file on this computer, uploaded name)]."""
    import base64

    from .. import share, share_doc
    files = _expand(ctx, paths)
    if len(files) == 1 and files[0].suffix.lower() in share_doc.DOC_EXT:
        # one report or page: shared like the 分享 button does - with the files it uses, its text redacted
        b = share.build_doc(ctx.config, str(files[0]), title=title, conversation=_conv(ctx))
        if description:
            b["description"] = description[:300]
        b["doc_path"] = str(files[0])
        return {"body": b, "scan": [(Path(f["path"]), f["name"]) for f in b["_bundle"]]}
    red = share.Redactor(ctx.config)
    root = Path(os.path.commonpath([str(p.parent) for p in files]))
    named = [(p, p.relative_to(root).as_posix()) for p in files]
    if any(n.lower() == "index.md" for _, n in named):
        named = [(p, "files/" + n) for p, n in named]
    title = red(title or (root.name if len(files) > 1 else files[0].name))[:160]
    description = red(description or f"{len(files)} 個檔案")[:300]
    out = []
    for p, n in named:
        f = {"name": n, "path": str(p), "mime": mimetypes.guess_type(n)[0] or "application/octet-stream",
             "size": p.stat().st_size}
        out.append(share._doc_file(f, "", red))
    index = _index_md(title, description, [(f["name"], f["size"], f["mime"]) for f in out]).encode()
    entry = {"name": "index.md", "mime": "text/markdown", "size": len(index), "data": base64.b64encode(index).decode()}
    note = {"role": "assistant", "text": f"**{title}**\n\n{description}\n\n[開啟檔案清單](files/1)"}
    body = {"kind": "doc", "entry": "index.md", "title": title, "description": description, "messages": [note],
            "files": [entry] + out, "redactions": red.summary(), "skills": [], "model": None,
            "_entry_path": None, "_bundle": [{"name": n, "path": str(p)} for p, n in named]}
    return {"body": body, "scan": named}


def check(ctx: ToolContext, paths: list[str], title: str | None = None, description: str | None = None) -> dict:
    from .. import share_scan
    built = build(ctx, paths, title, description)
    secrets = ctx.config.secrets.all_values(min_len=6)
    res = share_scan.scan(built["scan"], secrets)
    built["result"] = res
    return built


def _report(built: dict) -> dict:
    from .. import share_scan
    res, b = built["result"], built["body"]
    return {"ok": res["ok"], "title": b["title"], "description": b.get("description") or "",
            "files": [f["name"] for f in res["files"]], "total_bytes": res["total"], "counts": res["counts"],
            "findings": res["findings"], "will_mask": b.get("redactions") or [], "summary": share_scan.summary(res)}


@tool("share_check", "web",
      "Check files before sharing them on sinobee.ai (nothing is uploaded): security risks (programs, macros, PDF "
      "scripts, phishing or mining pages, unsafe archives, files that are not what their name says, credential "
      "files), secrets and personal data, photo locations, size limits. paths: files or folders (as you see them, "
      "e.g. /workspace/report.html). ok=false means at least one ⛔ finding: fix it (or leave the file out) before "
      "share_files. Use the share-to-sinobee skill.",
      {"properties": {"paths": {"type": "array", "items": {"type": "string"}},
                      "title": {"type": "string"}, "description": {"type": "string"}},
       "required": ["paths"]})
async def share_check(ctx: ToolContext, paths: list, title: str = "", description: str = ""):
    try:
        built = await asyncio.to_thread(check, ctx, list(paths or []), title or None, description or None)
    except Exception as e:  # noqa: BLE001
        return ToolResult.error(f"無法檢查：{e}")
    return _report(built)


def _question(rep: dict, visibility: str) -> str:
    files = rep["files"]
    size = rep["total_bytes"]
    lines = [f"要把這些檔案分享到 sinobee.ai 嗎？「{rep['title']}」",
             f"檔案（{len(files)} 個，{size / 1024 / 1024:.1f} MB）：" + "、".join(files[:10])
             + (f" 等 {len(files)} 個" if len(files) > 10 else ""),
             "", "安全檢查：", rep["summary"]]
    if rep["will_mask"]:
        lines += ["", "上傳前會自動遮蔽：" + "、".join(f"{m['kind']}×{m['count']}" for m in rep["will_mask"])]
    lines += ["", "公開分享會列在 sinobee.ai/shares，任何人都能看到與下載；之後可以在 sinobee.ai/shares 或 SinoBee 的分享管理撤回。"]
    if visibility != "PUBLIC":
        lines.append("（任務建議只給知道連結的人）")
    return "\n".join(lines)[:1500]


@tool("share_files", "web",
      "Upload files to sinobee.ai and return the share link - only after the user confirms in questions SinoBee "
      "shows (who: public, listed at sinobee.ai/shares, or link-only; how long: 7 days, 30 days or until revoked; "
      "or refuse). The files are checked "
      "again first and nothing with a ⛔ finding is uploaded. Run share_check first and fix what it reports. "
      "paths: files or folders. visibility: what you suggest (PUBLIC | LINK); expires_days: what you suggest "
      "(7 | 30 | 0 = never) - the user then chooses who may see it and for how long in two questions. Use the "
      "share-to-sinobee skill.",
      {"properties": {"paths": {"type": "array", "items": {"type": "string"}},
                      "title": {"type": "string"}, "description": {"type": "string"},
                      "visibility": {"type": "string", "enum": ["PUBLIC", "LINK"]},
                      "expires_days": {"type": "integer", "enum": [7, 30, 0]}},
       "required": ["paths"]})
async def share_files(ctx: ToolContext, paths: list, title: str = "", description: str = "",
                      visibility: str = "PUBLIC", expires_days: int = 30):
    from .. import account, share
    if not account.token(ctx.config):
        return ToolResult.error("沒有登入 sinobee.ai：請使用者到「設定 → SinoBee.ai 帳號」登入後再分享。")
    try:
        built = await asyncio.to_thread(check, ctx, list(paths or []), title or None, description or None)
    except Exception as e:  # noqa: BLE001
        return ToolResult.error(f"無法準備分享：{e}")
    rep = _report(built)
    if not rep["ok"]:
        return ToolResult.error("安全檢查沒有通過，沒有上傳、也沒有詢問使用者。先處理這些 ⛔ 項目（移除該檔，或用 "
                                "share-to-sinobee 技能做乾淨的副本）再試：\n" + rep["summary"])
    if ctx.ask is None:
        return ToolResult.error("現在沒有人可以確認分享（排程或無人值守的任務不能自行上傳）。把檔案留著，告訴使用者"
                                "可以在結果卡按「分享」，或之後再請你分享。")
    risky = any(f["kind"] in WARN_LINK and f["severity"] == "warn" for f in rep["findings"])
    suggest = "LINK" if risky or str(visibility).upper() == "LINK" else "PUBLIC"
    opts = [{"label": PUBLIC, "description": "任何人都能看，列在 sinobee.ai/shares", "recommended": suggest == "PUBLIC"},
            {"label": LINK, "description": "有連結才能看，不會被列出或搜尋", "recommended": suggest == "LINK"},
            {"label": NO, "description": "不上傳"}]
    ans = await ctx.ask(_question(rep, suggest), opts, False, False)
    choice = ((ans or {}).get("choices") or [NO])[0]
    if choice not in (PUBLIC, LINK):
        return "使用者選擇不分享，沒有上傳任何檔案。"
    vis = "PUBLIC" if choice == PUBLIC else "LINK"
    try:
        want = int(expires_days)
    except (TypeError, ValueError):
        want = 30
    want = want if want in (7, 30, 0) else 30
    where = "公開，列在 sinobee.ai/shares" if vis == "PUBLIC" else "只給知道連結的人"
    ans = await ctx.ask(f"「{rep['title']}」（{where}）要分享多久？到期後連結自動失效，也可以隨時在 sinobee.ai/shares 撤回。",
                        [{"label": label, "description": desc, "recommended": days == want}
                         for label, days, desc in DURATIONS] + [{"label": NO, "description": "不上傳"}],
                        False, False)
    picked = ((ans or {}).get("choices") or [NO])[0]
    days = next((d for label, d, _ in DURATIONS if label == picked), None)
    if days is None:
        return "使用者選擇不分享（沒有選分享時間），沒有上傳任何檔案。"
    expires_days = days
    # the files may have changed while the question waited: check once more, upload exactly what was checked
    try:
        built = await asyncio.to_thread(check, ctx, list(paths or []), title or None, description or None)
    except Exception as e:  # noqa: BLE001
        return ToolResult.error(f"無法準備分享：{e}")
    if not built["result"]["ok"]:
        return ToolResult.error("檔案在等待確認時被改動，重新檢查沒有通過，沒有上傳：\n" + _report(built)["summary"])
    b = built["body"]
    if session := ctx.session_id:
        b["session_id"] = session

    def go():
        png = None
        try:
            if b.get("_entry_path"):
                png = share.make_thumbnail(b, title=b["title"], description=b.get("description") or "")
            else:
                from .. import share_doc
                img = next(((Path(f["path"]).read_bytes(), f["mime"]) for f in b["files"]
                            if f.get("path") and f["mime"] in ("image/png", "image/jpeg", "image/webp")
                            and f["size"] <= 8 * 1024 * 1024), None)
                png = share_doc.thumbnail(title=b["title"], description=b.get("description") or "", label="檔案",
                                          image=img)
        except Exception:  # noqa: BLE001 - a share without a preview picture is still a share
            png = None
        return share.upload(ctx.config, b, visibility=vis, expires_days=int(expires_days) or None,
                            description=b.get("description"), thumbnail=png)
    try:
        res = await asyncio.to_thread(go)
    except (share.ShareError, account.AccountError) as e:
        return ToolResult.error(str(e))
    base = account.base_url(ctx.config)
    out = {"url": res.get("url"), "id": res.get("id"), "visibility": vis,
           "expires_days": int(expires_days) or None, "files": rep["files"], "masked": rep["will_mask"],
           "warnings_accepted": [f["detail"] for f in rep["findings"] if f["severity"] == "warn"],
           "manage": f"{base}/shares"}
    if vis == "PUBLIC":
        out["listed_at"] = f"{base}/shares"
    if ctx.emit:
        try:
            ctx.emit("shared", {"url": out["url"], "id": out["id"], "title": b["title"], "visibility": vis})
        except Exception:  # noqa: BLE001
            pass
    return out
