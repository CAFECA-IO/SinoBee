"""Deliverables are written in the user's language.

The thorium run of 2026-10-19 answered a Traditional-Chinese request with an English report.md and page; only the
reviewer noticed, and its finding was then lost. This check is deterministic: it reads the request's script (and
any explicit "in English / 用日文" instruction), strips code, URLs, markup and formulas from each produced text file
and from the final answer, and reports a file whose prose is in another language, or in Simplified characters when
the user wrote Traditional (and the reverse).
"""
from __future__ import annotations

import re
from pathlib import Path

TEXT_EXT = {".md", ".markdown", ".html", ".htm", ".txt"}
MIN_PROSE = 120                   # characters of prose before a file is judged

# characters whose Simplified and Traditional forms differ, and that are common in any prose
SIMP = set("们这为说国发会时对关应进实现种体么样经过问题动电长开来从业产东机学气无车书华见认让设计报务质达难战网图爱"
           "们个后还结线级运层场将总当热头处两条边门间资转标单数据较术统决环观规议论边护验输")
TRAD = set("們這為說國發會時對關應進實現種體麼樣經過問題動電長開來從業產東機學氣無車書華見認讓設計報務質達難戰網圖愛"
           "個後還結線級運層場將總當熱頭處兩條邊門間資轉標單數據較術統決環觀規議論護驗輸")
SIMP, TRAD = SIMP - TRAD, TRAD - SIMP

ASKED = [  # an explicit request for a language wins over the request's own script
    (re.compile(r"(?:用|以|寫成|翻譯成|译成|翻译成)\s*(?:英文|英語|英语)|in english|english version", re.I), "en"),
    (re.compile(r"(?:用|以|寫成|翻譯成|翻译成)\s*(?:日文|日語|日语)|in japanese", re.I), "ja"),
    (re.compile(r"(?:用|以|寫成|翻譯成|翻译成)\s*(?:韓文|韓語|韩语)|in korean", re.I), "ko"),
    (re.compile(r"(?:用|以|寫成|翻譯成)\s*(?:繁體|繁中|正體)|in traditional chinese", re.I), "zh-Hant"),
    (re.compile(r"(?:用|以|写成|翻译成)\s*(?:简体|简中)|in simplified chinese", re.I), "zh-Hans"),
    (re.compile(r"(?:用|以|寫成|翻譯成|翻译成)\s*(?:中文|華語|汉语)|in chinese", re.I), "zh"),
]
NAMES = {"en": "英文", "ja": "日文", "ko": "韓文", "zh": "中文", "zh-Hant": "繁體中文", "zh-Hans": "簡體中文",
         "other": "其他語言"}


def strip(text: str, html: bool = False) -> str:
    """Prose only: no code, markup, URLs, paths, formulas or numbers."""
    if html:
        text = re.sub(r"<(script|style|svg|code|pre)\b[\s\S]*?</\1>", " ", text, flags=re.I)
        text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"```[\s\S]*?```|`[^`\n]*`", " ", text)
    text = re.sub(r"\$\$[\s\S]*?\$\$|\$[^$\n]*\$", " ", text)
    text = re.sub(r"https?://\S+|www\.\S+|[\w./~-]+\.(?:md|html?|json|mjs|js|py|png|jpe?g|svg|csv|txt)\b", " ", text)
    text = re.sub(r"\[[^\]]*\]\([^)]*\)", lambda m: m.group(0).split("](")[0][1:], text)
    return re.sub(r"[\d_#*|>~=+\-/\\()\[\]{}<>:;,.!?\"'%°×·…]+", " ", text)


def detect(text: str) -> tuple[str, int]:
    """The dominant language of some prose, and how many letters it was judged on."""
    han = len(re.findall(r"[一-鿿]", text))
    kana = len(re.findall(r"[぀-ヿ]", text))
    hangul = len(re.findall(r"[가-힯]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    other = len(re.findall(r"[Ѐ-ӿ؀-ۿ฀-๿]", text))
    total = han + kana + hangul + latin + other
    if not total:
        return "", 0
    # a CJK character carries about as much text as three Latin letters
    weights = {"zh": han * 3, "ja": kana * 3 + (han * 3 if kana > han * 0.15 else 0), "ko": hangul * 3,
               "en": latin, "other": other * 2}
    if weights["ja"] and kana > han * 0.15:
        weights["zh"] = 0
    lang = max(weights, key=weights.get)
    if lang == "zh":
        chars = set(re.findall(r"[一-鿿]", text))
        s, t = len(chars & SIMP), len(chars & TRAD)
        if s + t >= 2:
            lang = "zh-Hans" if s > t * 2 else "zh-Hant" if t > s * 2 else "zh"
    return lang, total


def wanted(query: str) -> str:
    for rx, lang in ASKED:
        if rx.search(query or ""):
            return lang
    lang, n = detect(strip(query or ""))
    return lang if n >= 4 else ""


def compatible(want: str, got: str) -> bool:
    if not want or not got or want == got:
        return True
    if want == "zh":
        return got.startswith("zh")
    if got == "zh":
        return want.startswith("zh")
    return False


def check(query: str, paths: list[Path], answer: str = "") -> list[dict]:
    """Produced text files (and the final answer) whose prose is not in the language the user wrote or asked for."""
    want = wanted(query)
    if not want:
        return []
    issues = []
    for p in paths:
        p = Path(p)
        if p.suffix.lower() not in TEXT_EXT or not p.is_file() or p.stat().st_size > 4 * 1024 * 1024:
            continue
        prose = strip(p.read_text(encoding="utf-8", errors="replace"), html=p.suffix.lower() in (".html", ".htm"))
        got, n = detect(prose)
        if n >= MIN_PROSE and not compatible(want, got):
            issues.append({"path": str(p), "name": p.name, "problem": f"內容是{NAMES.get(got, got)}，但使用者用{NAMES[want]}",
                           "detail": f"把 {p.name} 的文字改寫成{NAMES[want]}（程式碼、專有名詞、單位與引文原句可保留）"})
    got, n = detect(strip(answer))
    if n >= MIN_PROSE and not compatible(want, got):
        issues.append({"path": "", "name": "最後的回答", "problem": f"回答是{NAMES.get(got, got)}，但使用者用{NAMES[want]}",
                       "detail": f"用{NAMES[want]}重寫回答"})
    return issues
