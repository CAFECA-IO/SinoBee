"""Check files before they are uploaded to sinobee.ai (share_files tool, share-to-sinobee skill).

A share is an outbound copy that anyone with the link (or, made public, anyone at all through sinobee.ai/shares)
can download. This module decides, from the files alone and on this computer (never in the sandbox, whose results
the agent could fake), whether that is safe:

  block   the file must not be shared as it is: a program or macro, a PDF that runs code, an archive that escapes
          its folder or explodes, a file that is not what its name says, a page that collects passwords or mines
          coins, a credential file, a secret in a file whose text cannot be masked, a file over the size limits;
  warn    the user decides, after reading it in the confirmation: a secret in text (it will be masked), personal
          data (national ID, card, phone, e-mail), the place a photo was taken, a page that loads code or sends
          data elsewhere, hidden text-direction characters, text that tries to instruct an AI, author metadata,
          an encrypted archive that cannot be looked into;
  info    noted only.

scan(paths) -> {"ok": bool, "files": [...], "findings": [{file, severity, kind, detail}], "counts": {...}}
"""
from __future__ import annotations

import mimetypes
import re
import struct
import zipfile
import zlib
from pathlib import Path

MAX_FILES = 40
MAX_FILE = 25 * 1024 * 1024
MAX_TOTAL = 100 * 1024 * 1024
TEXT_LIMIT = 4 * 1024 * 1024               # bytes of a text file that are read
ZIP_MAX_MEMBERS = 5000
ZIP_MAX_RATIO = 100
ZIP_MAX_UNPACKED = 1024 * 1024 * 1024

TEXT_EXT = {".md", ".markdown", ".txt", ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml", ".html", ".htm", ".xhtml",
            ".svg", ".css", ".js", ".mjs", ".ts", ".py", ".sql", ".log", ".ini", ".toml", ".cfg", ".conf", ".ipynb",
            ".tex", ".rst", ".srt", ".vtt"}
PAGE_EXT = {".html", ".htm", ".xhtml", ".svg"}
OOXML_EXT = {".docx", ".xlsx", ".pptx", ".docm", ".xlsm", ".pptm", ".dotm", ".xltm", ".potm", ".ppsm"}
MACRO_EXT = {".docm", ".xlsm", ".pptm", ".dotm", ".xltm", ".potm", ".ppsm", ".xlam", ".ppam"}
OLE_EXT = {".doc", ".xls", ".ppt", ".msg", ".pub"}
EXEC_EXT = {".exe", ".dll", ".scr", ".com", ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".js_", ".jse", ".wsf",
            ".wsh", ".hta", ".msi", ".msp", ".jar", ".apk", ".ipa", ".dmg", ".pkg", ".app", ".deb", ".rpm", ".sh",
            ".command", ".bin", ".so", ".dylib", ".sys", ".cpl", ".lnk", ".url", ".reg", ".iso", ".img", ".vhd",
            ".scpt", ".workflow", ".xll", ".appx", ".msix"}
ARCHIVE_EXT = {".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst"}
CREDENTIAL_NAMES = re.compile(
    r"^(\.env(\..*)?|\.netrc|\.npmrc|\.pypirc|\.pgpass|\.git-credentials|\.htpasswd|id_(rsa|dsa|ecdsa|ed25519)(\.pub)?|"
    r"known_hosts|authorized_keys|credentials(\.json|\.csv)?|client_secrets?\.json|service[-_]account.*\.json|"
    r"secrets?\.(json|ya?ml|toml)|.*\.(pem|key|p12|pfx|jks|keystore|kdbx|ovpn|ppk|gpg|asc|mobileprovision|keychain(-db)?))$",
    re.I)

# ---------------------------------------------------------------- secrets and personal data in text
SECRETS = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("api_key", re.compile(r"\b(sk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|"
                           r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|xox[abprs]-[A-Za-z0-9-]{10,}|"
                           r"glpat-[A-Za-z0-9_-]{20}|sbh_[A-Za-z0-9_-]{20,}|sk_live_[A-Za-z0-9]{20,}|"
                           r"hf_[A-Za-z0-9]{30,}|AccountKey=[A-Za-z0-9+/=]{40,})")),
    ("token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("connection_string", re.compile(r"\b[a-z][a-z0-9+.-]{1,20}://[^\s:/@]{1,64}:[^\s@/]{3,128}@[\w.-]+", re.I)),
    ("password", re.compile(r"(?i)\b(?:pass(?:word|wd)?|pwd|secret|api[_-]?key|access[_-]?token)['\"]?\s*[:=]\s*['\"]"
                            r"([^\s'\"]{6,})['\"]")),
]
TW_ID = re.compile(r"\b([A-Z])([1289])(\d{8})\b")
CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
CARD_IIN = re.compile(r"^(4|5[1-5]|2[2-7]|3[47]|6(011|5|4[4-9]|22)|35)")
CARD_WORD = re.compile(r"(?i)卡號|信用卡|card|visa|master|amex|jcb")
TW_MOBILE = re.compile(r"(?<!\d)(?:\+886[- ]?9|09)\d{2}[- ]?\d{3}[- ]?\d{3}(?!\d)")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
BIDI = re.compile("[\u202a-\u202e\u2066-\u2069\u200b\u200c\u200d\u2060\ufeff]")
INJECTION = re.compile(r"(?i)(ignore (all |any )?(previous|prior|above) (instructions|prompts)|disregard (the )?(system|previous)"
                       r" (prompt|instructions)|you are now (in )?(developer|dan) mode|忽略(之前|先前|以上|上面)的?(所有)?(指示|指令|提示)|"
                       r"<\s*/?\s*(system|assistant)\s*>|\[\s*system\s*\])")
NOREPLY = re.compile(r"(?i)(noreply|no-reply|example\.(com|org)|test\.com|localhost)")

# ---------------------------------------------------------------- active content in pages
SCRIPT_SRC = re.compile(r"""<script\b[^>]*\bsrc\s*=\s*["']?(https?:)?//([^/"'\s>]+)""", re.I)
IFRAME = re.compile(r"""<(?:iframe|frame|embed|object)\b[^>]*\b(?:src|data)\s*=\s*["']?(?:https?:)?//([^/"'\s>]+)""", re.I)
FORM_EXT = re.compile(r"""<form\b[^>]*\baction\s*=\s*["']?(?:https?:)?//([^/"'\s>]+)""", re.I)
PASSWORD_INPUT = re.compile(r"""<input\b[^>]*\btype\s*=\s*["']?password""", re.I)
JS_URL = re.compile(r"""\b(?:href|src|action)\s*=\s*["']?\s*javascript:""", re.I)
META_REFRESH = re.compile(r"""<meta\b[^>]*http-equiv\s*=\s*["']?refresh[^>]*url\s*=\s*(?:https?:)?//([^"'\s>;]+)""", re.I)
SEND_OUT = re.compile(r"""\b(?:fetch|navigator\.sendBeacon|new\s+WebSocket|new\s+EventSource|\.open)\s*\(\s*["'`](?:https?:|wss?:)?//([^/"'`\s]+)""", re.I)
XHR_POST = re.compile(r"""XMLHttpRequest[\s\S]{0,200}?\.open\s*\(\s*["'](?:POST|PUT)""", re.I)
COOKIE = re.compile(r"""document\.cookie|localStorage\.getItem\s*\(\s*["'][^"']*(?:token|auth|session|key)""", re.I)
OBFUSCATION = re.compile(r"""\beval\s*\(\s*(?:atob|unescape|decodeURIComponent|String\.fromCharCode)|"""
                         r"""\bnew\s+Function\s*\(\s*atob|(?:\\x[0-9a-f]{2}){40,}|[A-Za-z0-9+/]{4000,}={0,2}""", re.I)
MINER = re.compile(r"(?i)coinhive|cryptonight|coin-?imp|crypto-?loot|webminepool|minero\.cc|monero[-_ ]?miner|"
                   r"stratum\+tcp://|CoinHive\.Anonymous|wasm[-_]?miner")
TRACKERS = re.compile(r"(?i)google-analytics\.com|googletagmanager\.com|connect\.facebook\.net|hotjar\.com|"
                      r"clarity\.ms|mixpanel\.com|segment\.(com|io)|doubleclick\.net")
# shared pages may load code from these (what SinoBee's own pages and skills use); anything else is noted
CDN_OK = {"cdn.jsdelivr.net", "cdnjs.cloudflare.com", "unpkg.com", "esm.sh", "cdn.skypack.dev", "fonts.googleapis.com",
          "fonts.gstatic.com", "cdn.plot.ly", "d3js.org", "code.jquery.com", "ajax.googleapis.com", "cdn.tailwindcss.com",
          "threejs.org", "www.gstatic.com"}

MAGIC = [  # (prefix, what it really is)
    (b"MZ", "windows_exe"), (b"\x7fELF", "elf"), (b"\xcf\xfa\xed\xfe", "macho"), (b"\xce\xfa\xed\xfe", "macho"),
    (b"\xca\xfe\xba\xbe", "macho_or_java"), (b"\xfe\xed\xfa\xcf", "macho"), (b"#!", "script"),
    (b"%PDF", "pdf"), (b"PK\x03\x04", "zip"), (b"PK\x05\x06", "zip"), (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "ole"),
    (b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpeg"), (b"GIF8", "gif"), (b"RIFF", "riff"),
    (b"7z\xbc\xaf\x27\x1c", "7z"), (b"Rar!", "rar"), (b"\x1f\x8b", "gzip"), (b"BZh", "bzip2"), (b"\xfd7zXZ", "xz"),
    (b"\x00\x00\x00", "iso_bmff"), (b"ID3", "mp3"), (b"OggS", "ogg"), (b"fLaC", "flac"), (b"\x1aE\xdf\xa3", "webm"),
    (b"wOFF", "woff"), (b"wOF2", "woff2"), (b"\x00asm", "wasm"),
]
EXPECT = {  # extension -> the magic kinds it may have (others are a mismatch)
    ".png": {"png"}, ".jpg": {"jpeg"}, ".jpeg": {"jpeg"}, ".gif": {"gif"}, ".webp": {"riff"}, ".pdf": {"pdf"},
    ".zip": {"zip"}, ".docx": {"zip"}, ".xlsx": {"zip"}, ".pptx": {"zip"}, ".odt": {"zip"}, ".ods": {"zip"},
    ".odp": {"zip"}, ".epub": {"zip"}, ".doc": {"ole"}, ".xls": {"ole"}, ".ppt": {"ole"}, ".mp4": {"iso_bmff"},
    ".mov": {"iso_bmff"}, ".m4a": {"iso_bmff"}, ".mp3": {"mp3", "unknown"}, ".wav": {"riff"}, ".ogg": {"ogg"},
    ".webm": {"webm"}, ".woff": {"woff"}, ".woff2": {"woff2"}, ".wasm": {"wasm"}, ".gz": {"gzip"}, ".tgz": {"gzip"},
    ".7z": {"7z"}, ".rar": {"rar"}, ".bz2": {"bzip2"}, ".xz": {"xz"}, ".flac": {"flac"},
}
EXECUTABLE_MAGIC = {"windows_exe", "elf", "macho", "macho_or_java"}


def magic(head: bytes) -> str:
    for prefix, kind in MAGIC:
        if head.startswith(prefix):
            if kind == "iso_bmff" and head[4:8] not in (b"ftyp", b"moov", b"mdat", b"free", b"wide"):
                continue
            return kind
    if head[4:8] == b"ftyp":
        return "iso_bmff"
    if head.lstrip()[:1] in (b"<", b"{", b"[") or _textual(head):
        return "text"
    return "unknown"


def _textual(b: bytes) -> bool:
    if not b:
        return True
    if b"\x00" in b[:4096]:
        return False
    try:
        b[:4096].decode("utf-8")
        return True
    except UnicodeDecodeError as e:
        return e.start >= 4090             # a multi-byte character cut at the end of the sample


def _luhn(digits: str) -> bool:
    s, alt = 0, False
    for d in reversed(digits):
        n = int(d)
        if alt:
            n = n * 2 - 9 if n > 4 else n * 2
        s += n
        alt = not alt
    return s % 10 == 0


TW_LETTER = {c: v for c, v in zip("ABCDEFGHJKLMNPQRSTUVXYWZIO", range(10, 36))}


def _tw_id_ok(m: re.Match) -> bool:
    n = TW_LETTER.get(m.group(1))
    if n is None:
        return False
    digits = [n // 10, n % 10] + [int(c) for c in m.group(2) + m.group(3)]
    w = [1, 9, 8, 7, 6, 5, 4, 3, 2, 1, 1]
    return sum(a * b for a, b in zip(digits, w)) % 10 == 0


class Report:
    def __init__(self):
        self.findings: list[dict] = []

    def add(self, file: str, severity: str, kind: str, detail: str) -> None:
        if not any(f["file"] == file and f["kind"] == kind and f["detail"] == detail for f in self.findings):
            self.findings.append({"file": file, "severity": severity, "kind": kind, "detail": detail[:300]})


def _mask(s: str) -> str:
    s = s.strip()
    return (s[:4] + "…" + s[-2:]) if len(s) > 8 else "…"


def scan_text(text: str, name: str, rep: Report, *, maskable: bool, secrets: list[str] = (), page: bool = False,
              where: str = "") -> None:
    """Secrets, personal data, hidden characters and instructions aimed at an AI in a text."""
    at = f"（{where}）" if where else ""
    sev = "warn" if maskable else "block"
    how = "分享時會自動遮蔽" if maskable else "這種檔案無法自動遮蔽，請先移除"
    for v in secrets:
        if v and v in text:
            rep.add(name, sev, "stored_secret", f"內含 SinoBee 儲存的金鑰／密碼{at}：{how}")
            break
    for kind, rx in SECRETS:
        m = rx.search(text)
        if m:
            label = {"private_key": "私鑰", "api_key": "API 金鑰", "token": "存取權杖", "connection_string": "含密碼的連線字串",
                     "password": "密碼欄位"}[kind]
            rep.add(name, sev, kind, f"疑似{label} {_mask(m.group(0))}{at}：{how}")
    ids = [m.group(0) for m in TW_ID.finditer(text) if _tw_id_ok(m)]
    if ids:
        rep.add(name, "warn", "pii_national_id", f"{len(ids)} 個身分證字號（如 {_mask(ids[0])}）{at}")
    cards = []
    for m in CARD.finditer(text):
        d = re.sub(r"\D", "", m.group(0))
        if not (13 <= len(d) <= 19 and _luhn(d) and CARD_IIN.match(d) and len(set(d)) > 2):
            continue
        # a long run of digits passes the Luhn check one time in ten: count it only when written like a card
        # (groups of four) or named as one nearby - numbers inside a PDF's drawing commands are not cards
        near = text[max(0, m.start() - 30):m.start()]
        if re.search(r"\d{4}[ -]\d{4}[ -]\d{4}", m.group(0)) or CARD_WORD.search(near):
            cards.append(d)
    if cards:
        rep.add(name, "warn", "pii_card", f"{len(cards)} 個符合檢查碼的信用卡號（如 {_mask(cards[0])}）{at}")
    phones = TW_MOBILE.findall(text)
    if len(phones) >= 1:
        rep.add(name, "warn", "pii_phone", f"{len(phones)} 個手機號碼{at}")
    mails = {m for m in EMAIL.findall(text) if not NOREPLY.search(m)}
    if len(mails) >= 3:
        rep.add(name, "warn", "pii_email", f"{len(mails)} 個電子郵件地址{at}")
    elif mails:
        rep.add(name, "info", "pii_email", f"{len(mails)} 個電子郵件地址{at}")
    b = BIDI.findall(text)
    if any(c in "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069" for c in b):
        rep.add(name, "warn", "bidi", f"含改變文字方向的隱藏字元{at}，顯示的內容可能和實際不同")
    m = INJECTION.search(text)
    if m:
        rep.add(name, "warn", "prompt_injection", f"含疑似對 AI 下指令的文字「{m.group(0)[:40]}」{at}")
    if page:
        scan_page(text, name, rep, where)


def scan_page(text: str, name: str, rep: Report, where: str = "") -> None:
    at = f"（{where}）" if where else ""
    if MINER.search(text):
        rep.add(name, "block", "cryptominer", f"含挖礦程式的特徵{at}")
    if PASSWORD_INPUT.search(text) and (FORM_EXT.search(text) or SEND_OUT.search(text) or XHR_POST.search(text)):
        rep.add(name, "block", "credential_harvest", f"頁面有密碼欄位，並把資料送到外部網站{at}（釣魚頁面的特徵）")
    for m in FORM_EXT.finditer(text):
        rep.add(name, "warn", "form_external", f"表單會送到外部網站 {m.group(1)}{at}")
    for m in SEND_OUT.finditer(text):
        rep.add(name, "warn", "send_external", f"程式會連線到外部網站 {m.group(1)}{at}")
    for m in SCRIPT_SRC.finditer(text):
        host = m.group(2).lower()
        if host not in CDN_OK:
            rep.add(name, "warn", "external_script", f"載入外部程式 {host}{at}")
    for m in IFRAME.finditer(text):
        rep.add(name, "warn", "external_frame", f"嵌入外部網頁 {m.group(1)}{at}")
    m = META_REFRESH.search(text)
    if m:
        rep.add(name, "warn", "redirect", f"開啟後會自動轉到 {m.group(1)}{at}")
    if JS_URL.search(text):
        rep.add(name, "warn", "javascript_url", f"含 javascript: 連結{at}")
    if COOKIE.search(text):
        rep.add(name, "warn", "reads_credentials", f"程式讀取 cookie 或登入權杖{at}")
    if OBFUSCATION.search(text):
        rep.add(name, "warn", "obfuscated", f"含混淆或編碼後執行的程式碼{at}")
    t = TRACKERS.search(text)
    if t:
        rep.add(name, "info", "tracker", f"含追蹤程式 {t.group(0)}{at}")


# ---------------------------------------------------------------- containers
def _zip(p: Path, name: str, rep: Report, secrets, *, office: bool) -> None:
    try:
        z = zipfile.ZipFile(p)
    except (zipfile.BadZipFile, OSError) as e:
        rep.add(name, "block", "corrupt", f"壓縮檔無法讀取：{e}")
        return
    with z:
        infos = z.infolist()
        if len(infos) > ZIP_MAX_MEMBERS:
            rep.add(name, "block", "zip_bomb", f"壓縮檔有 {len(infos)} 個項目")
            return
        unpacked = sum(i.file_size for i in infos)
        packed = max(1, sum(i.compress_size for i in infos))
        if unpacked > ZIP_MAX_UNPACKED or (unpacked > 50 * 1024 * 1024 and unpacked / packed > ZIP_MAX_RATIO):
            rep.add(name, "block", "zip_bomb", f"解壓後 {unpacked // (1024 * 1024)} MB（壓縮比 {unpacked // packed}:1），疑似壓縮炸彈")
            return
        for i in infos:
            n = i.filename
            if n.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", n) or ".." in Path(n.replace("\\", "/")).parts:
                rep.add(name, "block", "zip_traversal", f"項目「{n[:80]}」會解壓到資料夾外")
            if (i.external_attr >> 16) & 0o170000 == 0o120000:
                rep.add(name, "block", "zip_symlink", f"項目「{n[:80]}」是符號連結")
            if i.flag_bits & 0x1:
                rep.add(name, "warn", "encrypted", "壓縮檔有加密的項目，無法檢查內容")
                continue
            low = n.lower()
            if office and low.endswith("vbaproject.bin"):
                rep.add(name, "block", "macro", "文件內含 VBA 巨集")
                continue
            if office and (low.startswith(("word/embeddings/", "xl/embeddings/", "ppt/embeddings/")) or "activex" in low):
                rep.add(name, "warn", "embedded_object", f"文件內嵌物件或 ActiveX：{n[:80]}")
            if office and low.endswith(".rels"):
                try:
                    rel = z.read(i)[:2_000_000].decode("utf-8", "replace")
                except Exception:  # noqa: BLE001
                    rel = ""
                if re.search(r'TargetMode="External"[^>]*Target="(?:https?:|file:|\\\\)|Target="(?:https?:|file:|\\\\)[^"]*"[^>]*TargetMode="External"', rel) \
                        and re.search(r"attachedTemplate|oleObject|frame|subDocument", rel):
                    rep.add(name, "block", "remote_template", "文件開啟時會從外部載入範本或物件（常見的惡意文件手法）")
            if office and low in ("docprops/core.xml", "docprops/app.xml"):
                meta = z.read(i)[:200_000].decode("utf-8", "replace")
                who = re.findall(r"<(?:dc:creator|cp:lastModifiedBy|Company|Manager)>([^<]{1,80})<", meta)
                if who:
                    rep.add(name, "info", "metadata", "文件屬性含作者／公司：" + "、".join(dict.fromkeys(who))[:120])
                continue
            ext = Path(low).suffix
            if not office and (ext in EXEC_EXT or ext in MACRO_EXT):
                rep.add(name, "block", "archive_executable", f"壓縮檔內有程式或巨集檔「{n[:80]}」")
            if not office and CREDENTIAL_NAMES.match(Path(n).name):
                rep.add(name, "block", "credential_file", f"壓縮檔內有憑證檔「{n[:80]}」")
            if i.file_size and (ext in TEXT_EXT or (office and ext in (".xml",))) and i.file_size <= TEXT_LIMIT:
                try:
                    data = z.read(i)
                except Exception:  # noqa: BLE001
                    continue
                txt = data.decode("utf-8", "replace")
                if office:
                    txt = re.sub(r"<[^>]+>", " ", txt)
                scan_text(txt, name, rep, maskable=False, secrets=secrets, page=ext in PAGE_EXT, where=n[:60])
            elif not office and ext in (".zip", ".docx", ".xlsx", ".pptx", ".jar"):
                rep.add(name, "warn", "nested_archive", f"壓縮檔內還有壓縮檔「{n[:80]}」，只檢查到第一層")


PDF_BAD = [(re.compile(rb"/JavaScript\b|/JS\s*[(<\[/]"), "block", "pdf_javascript", "PDF 內含 JavaScript"),
           (re.compile(rb"/Launch\b"), "block", "pdf_launch", "PDF 會嘗試執行外部程式（/Launch）"),
           (re.compile(rb"/RichMedia\b"), "warn", "pdf_richmedia", "PDF 內含多媒體／Flash 物件"),
           (re.compile(rb"/EmbeddedFile\b"), "warn", "pdf_attachment", "PDF 內附檔案，內容沒有檢查"),
           (re.compile(rb"/SubmitForm\b"), "warn", "pdf_submit", "PDF 表單會把資料送出"),
           (re.compile(rb"/URI\s*\(\s*(?:javascript|file):", re.I), "block", "pdf_uri", "PDF 含 javascript: 或 file: 連結")]


def _pdf(raw: bytes, name: str, rep: Report, secrets) -> None:
    blobs = [raw]
    for m in re.finditer(rb"stream\r?\n", raw):          # flate streams: object streams can hide /JavaScript
        if len(blobs) > 400:
            break
        end = raw.find(b"endstream", m.end())
        if end == -1:
            continue
        try:
            blobs.append(zlib.decompressobj().decompress(raw[m.end():end], 8 * 1024 * 1024))
        except zlib.error:
            pass
    for rx, sev, kind, msg in PDF_BAD:
        if any(rx.search(b) for b in blobs):
            rep.add(name, sev, kind, msg)
    if b"/Encrypt" in raw:
        rep.add(name, "warn", "encrypted", "PDF 有加密，內容沒有完整檢查")
    text = "\n".join(re.sub(r"[^\x20-\x7e\n]", " ", b.decode("latin-1")) for b in blobs[:200])
    scan_text(text, name, rep, maskable=False, secrets=secrets)
    who = [_pdf_str(m) for m in re.findall(rb"/Author\s*\(((?:[^)\\]|\\.){1,200})\)", raw[:2_000_000])]
    who = [w for w in who if w.strip()]
    if who:
        rep.add(name, "info", "metadata", "PDF 屬性含作者：" + "、".join(dict.fromkeys(who))[:120])


def _pdf_str(b: bytes) -> str:
    """A PDF literal string as text (UTF-16 with a byte-order mark, else PDFDocEncoding ~ Latin-1)."""
    b = re.sub(rb"\\([()\\])", rb"\1", b)
    if b[:2] == b"\xfe\xff":
        return b[2:].decode("utf-16-be", "replace").strip()
    return b.decode("latin-1").strip()


def _tiff_gps(t: bytes) -> bool:
    """Whether a TIFF/EXIF block has GPS latitude or longitude."""
    if t[:6] == b"Exif\x00\x00":
        t = t[6:]
    if len(t) < 8 or t[:2] not in (b"II", b"MM"):
        return False
    e = "<" if t[:2] == b"II" else ">"
    try:
        off = struct.unpack(e + "I", t[4:8])[0]
        n = struct.unpack(e + "H", t[off:off + 2])[0]
        for k in range(min(n, 500)):
            ent = t[off + 2 + 12 * k: off + 14 + 12 * k]
            if struct.unpack(e + "H", ent[:2])[0] == 0x8825:
                gps = struct.unpack(e + "I", ent[8:12])[0]
                cnt = struct.unpack(e + "H", t[gps:gps + 2])[0]
                tags = {struct.unpack(e + "H", t[gps + 2 + 12 * j: gps + 4 + 12 * j])[0] for j in range(min(cnt, 64))}
                return bool(tags & {2, 4})          # GPSLatitude / GPSLongitude
    except (struct.error, IndexError):
        return False
    return False


def _exif_gps(raw: bytes, kind: str) -> bool:
    """Whether a JPEG, PNG (eXIf), WebP (EXIF chunk) or TIFF image carries GPS coordinates (or XMP GPS)."""
    if kind == "jpeg" or kind == "unknown":
        i = raw.find(b"Exif\x00\x00")
        if i != -1 and _tiff_gps(raw[i:i + 6 + 256 * 1024]):
            return True
        if raw[:2] in (b"II", b"MM") and _tiff_gps(raw[:4 * 1024 * 1024]):
            return True
    elif kind in ("png", "riff"):
        tag = b"eXIf" if kind == "png" else b"EXIF"
        i = raw.find(tag)
        if i != -1:
            ln = struct.unpack(">I" if kind == "png" else "<I", raw[i - 4:i] if kind == "png" else raw[i + 4:i + 8])[0]
            start = i + 4 if kind == "png" else i + 8
            if _tiff_gps(raw[start:start + min(ln, 256 * 1024)]):
                return True
    return b"GPSLatitude" in raw[:1024 * 1024] or b"exif:GPSLatitude" in raw[:1024 * 1024]


# ---------------------------------------------------------------- one file, all files
def scan_file(p: Path, name: str, rep: Report, secrets: list[str]) -> None:
    ext = p.suffix.lower()
    if p.is_symlink():
        rep.add(name, "block", "symlink", "是符號連結，請分享實際的檔案")
        return
    if CREDENTIAL_NAMES.match(p.name):
        rep.add(name, "block", "credential_file", "檔名是憑證、金鑰或環境設定檔")
    size = p.stat().st_size
    if size > MAX_FILE:
        rep.add(name, "block", "too_large", f"{size // (1024 * 1024)} MB，超過單檔上限 {MAX_FILE // (1024 * 1024)} MB")
        return
    with open(p, "rb") as fh:
        head = fh.read(4096)
    kind = magic(head)
    if ext in EXEC_EXT or kind in EXECUTABLE_MAGIC:
        rep.add(name, "block", "executable", "是可執行程式或安裝檔" if ext in EXEC_EXT or kind != "script"
                else "是可執行的腳本")
        return
    if kind == "script" and ext not in TEXT_EXT:
        rep.add(name, "block", "executable", "是可執行的腳本（#! 開頭）")
        return
    if ext in MACRO_EXT:
        rep.add(name, "block", "macro", "啟用巨集的 Office 檔")
    want = EXPECT.get(ext)
    if want and kind not in want and not (kind == "unknown" and "unknown" in want):
        rep.add(name, "block", "type_mismatch", f"副檔名是 {ext}，實際內容卻是 {kind}")
        return
    if ext in TEXT_EXT and kind not in ("text", "unknown"):
        rep.add(name, "block", "type_mismatch", f"副檔名是 {ext}，實際內容卻是 {kind}")
        return
    if ext in TEXT_EXT or (kind == "text" and not ext):
        text = p.read_bytes()[:TEXT_LIMIT].decode("utf-8", "replace")
        scan_text(text, name, rep, maskable=ext in TEXT_EXT, secrets=secrets, page=ext in PAGE_EXT)
        if ext in (".js", ".mjs"):
            scan_page(text, name, rep)
        if size > TEXT_LIMIT:
            rep.add(name, "info", "partial", f"只檢查了前 {TEXT_LIMIT // (1024 * 1024)} MB")
        return
    raw = p.read_bytes()
    if kind == "zip":
        _zip(p, name, rep, secrets, office=ext in OOXML_EXT | {".odt", ".ods", ".odp"})
    elif kind == "ole":
        if re.search(rb"_VBA_PROJECT|V\x00B\x00A\x00|Macros", raw):
            rep.add(name, "block", "macro", "舊版 Office 檔內含 VBA 巨集")
        scan_text(raw.decode("latin-1"), name, rep, maskable=False, secrets=secrets)
    elif kind == "pdf":
        _pdf(raw, name, rep, secrets)
    elif kind in ("jpeg", "png", "riff") or (kind in ("unknown", "iso_bmff") and ext in (".tif", ".tiff", ".heic", ".dng")):
        if _exif_gps(raw, kind):
            rep.add(name, "warn", "gps", "照片含拍攝地點（GPS 座標）")
    elif ext in ARCHIVE_EXT and kind in ("7z", "rar", "gzip", "bzip2", "xz", "unknown"):
        rep.add(name, "warn", "unscannable_archive", f"{ext} 壓縮檔無法檢查內容，建議改分享解開後的檔案或 .zip")
    if kind in ("png", "jpeg", "gif", "riff") and re.search(rb"<script|<\?php|<%@", raw[-65536:] + raw[:65536], re.I):
        rep.add(name, "block", "polyglot", "圖片裡藏有網頁或伺服器程式碼")


def scan(paths: list[tuple[Path, str]], secrets: list[str] | None = None) -> dict:
    """paths: [(file on this computer, the name shown and uploaded)]."""
    rep = Report()
    total = 0
    files = []
    if len(paths) > MAX_FILES:
        rep.add("（全部）", "block", "too_many", f"{len(paths)} 個檔案，超過上限 {MAX_FILES} 個")
    for p, name in paths:
        try:
            size = p.stat().st_size
        except OSError as e:
            rep.add(name, "block", "unreadable", str(e))
            continue
        total += size
        files.append({"name": name, "size": size, "mime": mimetypes.guess_type(name)[0] or "application/octet-stream"})
        try:
            scan_file(p, name, rep, list(secrets or []))
        except Exception as e:  # noqa: BLE001 - a file that cannot be checked is not shared
            rep.add(name, "block", "scan_error", f"無法檢查：{type(e).__name__}: {e}")
    if total > MAX_TOTAL:
        rep.add("（全部）", "block", "too_large", f"合計 {total // (1024 * 1024)} MB，超過上限 {MAX_TOTAL // (1024 * 1024)} MB")
    counts = {s: sum(1 for f in rep.findings if f["severity"] == s) for s in ("block", "warn", "info")}
    order = {"block": 0, "warn": 1, "info": 2}
    return {"ok": counts["block"] == 0, "files": files, "total": total, "counts": counts,
            "findings": sorted(rep.findings, key=lambda f: order[f["severity"]])}


def summary(result: dict, limit: int = 12) -> str:
    """The findings as lines for the user (Traditional Chinese)."""
    mark = {"block": "⛔", "warn": "⚠️", "info": "ℹ️"}
    lines = [f"{mark[f['severity']]} {f['file']}：{f['detail']}" for f in result["findings"][:limit]]
    if len(result["findings"]) > limit:
        lines.append(f"…另有 {len(result['findings']) - limit} 項")
    return "\n".join(lines) or "✓ 沒有發現安全性問題"


def scan_bytes(data: bytes, name: str) -> dict:
    """For tests and the skill script: scan content held in memory."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / Path(name).name
        p.write_bytes(data)
        return scan([(p, name)])


__all__ = ["scan", "scan_file", "scan_text", "scan_bytes", "summary", "MAX_FILES", "MAX_FILE", "MAX_TOTAL"]
