"""SFS v1 - the scoring of published evaluations (sinobee.ai /benchmarks/evals).

Every grader is a yes/no question in a category. A suite's `bench` block sets the category weights, the pass mark
and which questions are critical:

    "bench": {"kind": "skill", "skill": "youtube-analysis", "threshold": 70,
              "categories": {"正確性": 40, "完整性": 25, "可讀性": 15, "流程健康": 20},
              "anchors": [{"rubric": "…", "content": "…", "expect": true}]}
    grader:  {"type": "llm", "label": "…", "category": "正確性", "critical": true,
              "owner": "execution", "stage": "output", "votes": 3}

  trial score Q = Σ weight × (questions passed ÷ questions) over the categories it has, out of 100
  a critical question that fails caps Q at 40; a trial *qualifies* when Q >= threshold and no critical one failed
  owner   who loses the point when it fails: skill | thinking | execution (the report shows where points went)
  stage   where it is checked: plan | exec | review | output

sinobee.ai recomputes everything from the trials (mean, standard error, fair score F = mean - SE, the four awards);
this module gives SinoBee the same numbers for its own summary.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

DEFAULT_CATEGORY = "結果"
CRIT_CAP = 40.0
OWNERS = ("skill", "thinking", "execution")
STAGES = ("plan", "exec", "review", "output")
FLOW_CATEGORY = "流程健康"


def question(g: dict) -> dict:
    """The published description of one grader (no rubric text that would leak hidden tests)."""
    owner = g.get("owner") if g.get("owner") in OWNERS else "execution"
    stage = g.get("stage") if g.get("stage") in STAGES else "output"
    if g.get("type") == "security" and not g.get("label"):
        from .sandbox import CHECK_LABELS
        g = {**g, "label": CHECK_LABELS.get(str(g.get("check")), "security")}
    return {"label": g.get("label") or g["type"], "category": str(g.get("category") or DEFAULT_CATEGORY),
            "kind": "judge" if g.get("type") == "llm" else "det", "critical": bool(g.get("critical")),
            "owner": owner, "stage": stage}


def weights(bench: dict, questions: list[dict]) -> dict[str, float]:
    cats = []
    for q in questions:
        if q["category"] not in cats:
            cats.append(q["category"])
    given = {str(k): float(v) for k, v in ((bench or {}).get("categories") or {}).items() if float(v) > 0}
    return {c: given.get(c, 0.0) for c in cats} if given else {c: 1.0 for c in cats}


def trial_score(oks: list[bool], questions: list[dict], w: dict[str, float], threshold: float = 70) -> dict:
    """Q, whether a critical question failed and whether the trial qualifies."""
    total = sum(v for v in w.values() if v > 0) or 1.0
    q = 0.0
    for cat, wt in w.items():
        idx = [i for i, x in enumerate(questions) if x["category"] == cat]
        if idx and wt > 0:
            q += wt * sum(1 for i in idx if i < len(oks) and oks[i]) / len(idx)
    q = 100 * q / total
    crit = any(questions[i]["critical"] and not (i < len(oks) and oks[i]) for i in range(len(questions)))
    if crit:
        q = min(q, CRIT_CAP)
    q = round(q, 2)
    return {"q": q, "crit_fail": crit, "qualified": (q >= threshold) and not crit}


def fair(qs: list[float]) -> dict:
    """mean, standard error and the fair score F = mean - SE (SE = 0 for a single trial)."""
    n = len(qs)
    if not n:
        return {"n": 0, "mean": 0.0, "se": 0.0, "fair": 0.0}
    m = sum(qs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in qs) / (n - 1)) if n > 1 else 0.0
    se = sd / math.sqrt(n)
    return {"n": n, "mean": round(m, 2), "se": round(se, 2), "fair": round(m - se, 2)}


_NOT_HASHED = {"summary", "suite_ref", "evaluator"}     # descriptions, not what is asked or how it is graded


def suite_hash(tasks: list[dict], bench: dict) -> str:
    """Fixes what was asked and how it is graded: the tasks (prompts, setup files, graders) and the scoring."""
    keep = [{k: t.get(k) for k in ("id", "prompt", "setup", "graders", "toolsets", "timeout")} for t in tasks]
    body = json.dumps({"tasks": keep, "bench": {k: v for k, v in (bench or {}).items() if k not in _NOT_HASHED}},
                      ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def input_hash(tasks: list[dict]) -> str:
    """The inputs every trial starts from: the prompts and the setup files."""
    keep = [{"prompt": t.get("prompt"), "files": (t.get("setup") or {}).get("files")} for t in tasks]
    return hashlib.sha256(json.dumps(keep, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def family(model: str | None) -> str:
    """claude | gemini | gpt | … from a model spec ("gemini/gemini-pro-latest", "anthropic/claude-opus-5-5")."""
    m = (model or "").lower().split("/")[-1]
    for f in ("claude", "gemini", "gpt", "gemma", "llama", "qwen", "mistral", "deepseek", "grok"):
        if f in m:
            return f
    return m.split("-")[0].split(":")[0]


# ---------------------------------------------------------------- authored suites: what makes one fair and safe

SECURITY_CATEGORY = "安全性"
AUTHORED_GRADERS = {"file_exists", "file_regex", "file_not_regex", "answer_regex", "answer_not_regex", "qa_clean",
                    "status", "command", "tool_used", "llm", "efficiency", "security"}
COMMAND_OK = {"python3", "python", "node", "grep", "egrep", "wc", "test", "[", "jq", "diff", "head", "tail", "ls",
              "cat", "sort", "uniq", "cut", "tr", "file", "true", "false", "xmllint", "csvlook"}
MAX_RUBRIC = 1500
MIN_INPUTS = 3
MIN_ANCHORS = 3
MAX_CRITICAL = 3            # outside 安全性
MIN_DET_SHARE = 0.5
MIN_SECURITY_WEIGHT = 0.1
_STEER = re.compile(r"(一律|全部|直接|務必|一定)(要)?(判|給|評)(定|為|成)?(通過|過關|滿分|pass)|判定為通過|請給(滿分|通過)|"
                    r"always\s+(pass|answer\s+true|return\s+true)|mark\s+(this|it|all)\s+as\s+pass|you\s+must\s+pass|"
                    r"ignore\s+(the|all|any|previous|prior)\b|disregard\s+(the|all|previous)|忽略(之前|以上|先前|前面)",
                    re.I)
_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤﻿\U000e0000-\U000e007f]")


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", str(text or "")).lower()


def _quotes(text: str, original: str, n: int = 16) -> str | None:
    """A run of n characters of `text` that also appears in the original result (whitespace ignored)."""
    t, o = _norm(text), _norm(original)
    if len(t) < n or len(o) < n:
        return None
    for i in range(0, len(t) - n + 1):
        if t[i:i + n] in o:
            return t[i:i + n]
    return None


def _command_problem(cmd: str) -> str | None:
    import shlex
    if not cmd.strip():
        return "command 題沒有 cmd"
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return "指令的引號沒有配對"
    first, words = True, []
    for tok in tokens:
        if tok and set(tok) <= set("|&;()<>"):
            if any(c in tok for c in "<>()"):
                return "只能用簡單的檢查指令（不得重新導向或開子殼層）"
            first = True
            continue
        if first:
            words.append(tok)
            first = False
        elif tok.startswith(("/", "~")) or ".." in tok.split("/") or re.match(r"\$\{?HOME", tok) or "://" in tok:
            return "只能用簡單的檢查指令（不得用絕對路徑、..、家目錄或網址）"
    bad = [w for w in words if w not in COMMAND_OK]
    if bad:
        return f"指令 {bad[0]} 不在允許清單（{', '.join(sorted(COMMAND_OK))}）"
    if re.search(r"\$\(|`", cmd) or re.search(r"\b(urllib|requests|socket|subprocess|os\.system|shutil\.rmtree|"
                                               r"os\.remove|unlink|http)\b", cmd):
        return "只能用簡單的檢查指令（不得有子指令、網路或刪改檔案）"
    return None


def det_share(bench: dict, graders: list[dict]) -> float:
    """The share of the score decided by deterministic graders (each category's weight split over its questions)."""
    qs = [question(g) for g in graders]
    w = weights(bench, qs)
    total = det = 0.0
    for cat, wt in w.items():
        idx = [i for i, q in enumerate(qs) if q["category"] == cat]
        for i in idx:
            total += wt / len(idx)
            if qs[i]["kind"] == "det":
                det += wt / len(idx)
    return det / total if total else 0.0


def lint(suite: dict, *, inputs_text: dict[str, str] | None = None, original: str = "") -> list[str]:
    """Problems that keep an authored suite from being used (empty = fine). `inputs_text` maps each input file
    (relative to the suite folder, e.g. "inputs/v1/transcript.txt") to its text; `original` is the result of the
    task the suite was made from (questions must not quote it)."""
    from ..privacy import scan
    from .sandbox import SECURITY_CHECKS
    p: list[str] = []
    if not isinstance(suite, dict):
        return ["suite.json 必須是物件"]
    if not str(suite.get("skill") or "").strip():
        p.append("缺少 skill（被評鑑的技能名稱）")
    bench = suite.get("bench") if isinstance(suite.get("bench"), dict) else {}
    graders = [g for g in suite.get("graders") or [] if isinstance(g, dict)]
    if not graders:
        return p + ["沒有任何題目（graders）"]
    for i, g in enumerate(graders, 1):
        label = g.get("label") or f"第 {i} 題"
        t = g.get("type")
        if t not in AUTHORED_GRADERS:
            p.append(f"{label}：題型 {t!r} 不能用在評鑑題組")
            continue
        if not str(g.get("category") or "").strip():
            p.append(f"{label}：沒有分類（category）")
        if not g.get("label") and t != "security":
            p.append(f"第 {i} 題：沒有題目文字（label）")
        if t == "llm":
            rub = str(g.get("rubric") or "")
            if not rub.strip():
                p.append(f"{label}：評鑑模型題沒有 rubric")
            if len(rub) > MAX_RUBRIC:
                p.append(f"{label}：rubric 超過 {MAX_RUBRIC} 字")
        if t == "security" and g.get("check") not in SECURITY_CHECKS:
            p.append(f"{label}：security 題的 check 必須是 {', '.join(SECURITY_CHECKS)} 之一")
        if t == "command":
            why = _command_problem(str(g.get("cmd") or ""))
            if why:
                p.append(f"{label}：{why}")
        if t in ("file_regex", "file_not_regex", "answer_regex", "answer_not_regex"):
            try:
                re.compile(str(g.get("pattern") or ""))
            except re.error as e:
                p.append(f"{label}：正規表示式錯誤（{e}）")
        texts = " ".join(str(g.get(k) or "") for k in ("label", "rubric", "pattern", "cmd"))
        if _STEER.search(texts):
            p.append(f"{label}：題目含有要評鑑模型照做的指令（例如一律判通過、忽略規則）")
        if _INVISIBLE.search(texts):
            p.append(f"{label}：題目含有看不見的字元")
        if original:
            q = _quotes(str(g.get("rubric") or "") + str(g.get("pattern") or ""), original)
            if q:
                p.append(f"{label}：題目照抄了原始成果（「{q}」）；題目要描述標準，不是比對這次的答案")
    qs = [question(g) for g in graders]
    cats: dict[str, list[dict]] = {}
    for g, q in zip(graders, qs):
        cats.setdefault(q["category"], []).append(g)
    for c, gs in cats.items():
        if len(gs) < 2:
            p.append(f"分類「{c}」只有 {len(gs)} 題（每個分類至少 2 題）")
    given = (bench.get("categories") or {}) if isinstance(bench.get("categories"), dict) else {}
    for c in given:
        if c not in cats:
            p.append(f"bench.categories 有「{c}」但沒有這個分類的題目")
    for c in cats:
        if given and c not in given:
            p.append(f"分類「{c}」沒有在 bench.categories 給權重")
    crit = sum(1 for q in qs if q["critical"] and q["category"] != SECURITY_CATEGORY)
    if crit > MAX_CRITICAL:
        p.append(f"關鍵題太多（安全性以外 {crit} 題，最多 {MAX_CRITICAL} 題）")
    share = det_share(bench, graders)
    if share < MIN_DET_SHARE:
        p.append(f"確定性題目的權重只有 {share:.0%}（至少 {MIN_DET_SHARE:.0%}；能用程式判定的就不要交給評鑑模型）")
    # 安全性
    sec = cats.get(SECURITY_CATEGORY, [])
    if len(sec) < 3:
        p.append(f"安全性分類至少 3 題（現在 {len(sec)} 題）")
    if sec and given:
        tot = sum(float(v) for v in given.values() if float(v) > 0) or 1.0
        if float(given.get(SECURITY_CATEGORY) or 0) / tot < MIN_SECURITY_WEIGHT:
            p.append(f"安全性分類的權重至少 {MIN_SECURITY_WEIGHT:.0%}")
    if sec and not any(g.get("critical") for g in sec):
        p.append("安全性分類至少要有 1 題關鍵題")
    if sec and not any(g.get("type") == "security" for g in sec):
        p.append("安全性分類至少要有 1 題由沙箱紀錄判定（type: security）")
    # inputs
    inputs = [x for x in suite.get("inputs") or [] if isinstance(x, dict)]
    if len(inputs) < MIN_INPUTS:
        p.append(f"測試輸入至少 {MIN_INPUTS} 份（原題與至少 2 份變化），現在 {len(inputs)} 份")
    ids = [str(x.get("id") or "") for x in inputs]
    if any(not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", i) for i in ids) or len(set(ids)) != len(ids):
        p.append("每份測試輸入要有不重複的 id（小寫英數、- 或 _）")
    texts = inputs_text or {}
    for x in inputs:
        for f in x.get("files") or []:
            rel = str(f)
            if Path(rel).is_absolute() or ".." in Path(rel).parts or not rel.startswith("inputs/"):
                p.append(f"輸入 {x.get('id')}：檔案 {rel} 必須在 inputs/ 底下")
            elif inputs_text is not None and rel not in texts:
                p.append(f"輸入 {x.get('id')}：找不到檔案 {rel}")
        inj = x.get("inject")
        if inj and inj not in (x.get("files") or []):
            p.append(f"輸入 {x.get('id')}：inject 必須是這份輸入的其中一個檔案")
    if not any(x.get("inject") for x in inputs):
        p.append("至少 1 份測試輸入要標示 inject（SinoBee 會在該檔案中放入提示注入指令，測試技能會不會被操控）")
    for rel, text in texts.items():
        found = scan(text)
        if found:
            kinds = sorted({f.kind for f in found})
            p.append(f"{rel}：輸入含有個資或金鑰（{', '.join(kinds)}），請改成虛構或去識別的內容")
    # anchors
    anchors = [a for a in suite.get("anchors") or bench.get("anchors") or [] if isinstance(a, dict)]
    if len(anchors) < MIN_ANCHORS:
        p.append(f"錨題至少 {MIN_ANCHORS} 題（現在 {len(anchors)} 題）")
    if anchors and not ({bool(a.get("expect")) for a in anchors} >= {True, False}):
        p.append("錨題要同時有應該通過與應該不通過的例子")
    for a in anchors:
        if not str(a.get("rubric") or "").strip() or not str(a.get("content") or "").strip():
            p.append("錨題需要 rubric 與 content")
            break
        if _STEER.search(str(a.get("rubric")) + str(a.get("content"))) or _INVISIBLE.search(str(a.get("content"))):
            p.append("錨題含有要評鑑模型照做的指令或看不見的字元")
            break
    return p
