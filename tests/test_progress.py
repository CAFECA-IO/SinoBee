"""Progress decides when a task is stuck, not a step count. Two things are verified here in depth:

1. FILE TYPE: code vs document is decided correctly (N = 8 for code, 3 for documents), including the ambiguous
   cases - HTML reports vs HTML apps and templates, files without an extension, build files whose extension looks
   like a document (requirements.txt), and the files of the Roadster task that stalled.
2. CONTROL LOG: every consult, skipped consult and stop says exactly what triggered it - the trigger code, the
   numbers that crossed the threshold, the thresholds, the step-by-step evidence - in the failure report, the run
   record, the UI events and logs/control.jsonl.
"""
import json

import pytest

from sinobee.agent import Agent, Runtime, failures
from sinobee.agent import progress as P
from sinobee.types import Message, ToolCall

from conftest import ScriptedProvider

REPORT_HTML = "<html><head><style>body{font-family:serif}</style></head><body>" + "".join(
    f"<h2>第 {i} 章</h2><p>" + "羅德斯特火箭的推進原理與飛行包線分析。" * 30 + "</p>" for i in range(6)) + "</body></html>"
APP_HTML = ("<html><body><canvas id='c'></canvas><script>" + "const ctx=c.getContext('2d');function draw(t){"
            "ctx.clearRect(0,0,800,600);ctx.fillRect(Math.sin(t)*100,0,10,10);requestAnimationFrame(draw)}" * 20 +
            "</script><p>動畫</p></body></html>")
VUE_HTML = "<div id='app'><li v-for='x in items'>{{ x }}</li></div>"
JINJA_HTML = "<ul>{% for r in rows %}<li>{{ r.name }}</li>{% endfor %}</ul>"
PROSE = "這是一份會議紀錄。\n\n今天討論了三件事：預算、時程與人力。\n大家同意下週再確認。\n" * 5
SCRIPT = "#!/usr/bin/env bash\nset -e\necho hello\n"

# (path, content, expected kind) - the corpus the classifier is measured against
CORPUS = [
    # code by extension
    ("app/main.py", None, "code"), ("src/index.ts", None, "code"), ("src/App.tsx", None, "code"),
    ("build.mjs", None, "code"), ("lib/util.cjs", None, "code"), ("cmd/server/main.go", None, "code"),
    ("src/lib.rs", None, "code"), ("Main.java", None, "code"), ("styles/site.css", None, "code"),
    ("theme.scss", None, "code"), ("deploy.sh", None, "code"), ("query.sql", None, "code"),
    ("notebook.ipynb", None, "code"), ("component.vue", None, "code"), ("infra/main.tf", None, "code"),
    ("templates/base.j2", None, "code"), ("script.ps1", None, "code"), ("analysis.R", None, "code"),
    # code by name (extension would mislead or is missing)
    ("Makefile", None, "code"), ("Dockerfile", None, "code"), ("Dockerfile.dev", None, "code"),
    ("requirements.txt", None, "code"), ("package.json", None, "code"), ("pyproject.toml", None, "code"),
    ("go.mod", None, "code"), (".gitignore", None, "code"), ("CMakeLists.txt", None, "code"),
    ("bin/run", SCRIPT, "code"),
    # documents
    ("report.md", None, "document"), ("docs/guide.markdown", None, "document"), ("notes.txt", None, "document"),
    ("paper/main.tex", None, "document"), ("README.md", None, "document"), ("CHANGELOG.md", None, "document"),
    ("README", None, "document"), ("LICENSE", None, "document"), ("meeting-notes", PROSE, "document"),
    ("proposal.docx", None, "document"), ("deck.pptx", None, "document"), ("out/summary.pdf", None, "document"),
    ("subs.srt", None, "document"), ("guide.rst", None, "document"),
    # HTML: decided by folder, then content
    ("reports/roadster.html", None, "document"), ("docs/index.html", None, "document"),
    ("roadster-spacex-package-report-v3.html", REPORT_HTML, "document"),
    ("roadster-spacex-animations.html", APP_HTML, "code"),
    ("index.html", VUE_HTML, "code"), ("page.html", JINJA_HTML, "code"),
    ("src/index.html", None, "code"), ("templates/base.html", None, "code"), ("public/index.html", None, "code"),
    ("article.htm", REPORT_HTML, "document"), ("empty.html", None, "document"),
    # pages generated as drawings (the Roadster package reports are 98% inline SVG) are edited like code
    ("roadster-spacex-package-report-v2.html", "<html><body><h1>報告</h1>" + "<svg><path d='M0 0L10 10'/></svg>" * 400
     + "</body></html>", "code"),
    ("reports/feasibility/x/page.html", "<html><canvas></canvas><script type='module'>" + "draw();" * 900
     + "</script><p>圖</p></html>", "code"),                      # a figure page inside reports/: content wins
    # binaries, templates, unknown extensions
    (".DS_Store", "\x00\x00\x00\x01Bud1\x00", "other"), ("cache/3302278652788356063", "\x00\x01\x02abc", "other"),
    (".env.example", None, "data"), ("config.yaml.example", None, "data"), ("main.py.bak", None, "code"),
    ("prisma/schema.prisma", None, "code"), ("videos/duel/script.body", PROSE, "document"),
    # data
    ("data/prices.csv", None, "data"), ("config.yaml", None, "data"), ("settings.json", None, "data"),
    ("feed.xml", None, "data"), ("model.xlsx", None, "data"), ("events.jsonl", None, "data"),
    # other
    ("logo.svg", None, "other"), ("photo.png", None, "other"), ("blob", "\x00\x01{}();=", "other"),
]


def test_file_type_classification_is_exact_on_the_corpus():
    wrong = [(p, k, P.classify(p, c)) for p, c, k in CORPUS if P.classify(p, c)[0] != k]
    assert not wrong, "misclassified: " + "; ".join(f"{p} expected {k}, got {g[0]} ({g[1]})" for p, k, g in wrong)
    assert all(P.classify(p, c)[1] for p, c, _ in CORPUS)                  # every decision says why
    assert len(CORPUS) >= 60


def test_this_repository_is_classified_sensibly():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    py = [P.classify(str(f.relative_to(root)))[0] for f in (root / "sinobee").rglob("*.py")]
    assert py and set(py) == {"code"}
    md = [P.classify(str(f.relative_to(root)))[0] for f in (root / "docs").rglob("*.md")]
    assert md and set(md) == {"document"}
    ui = root / "sinobee" / "server" / "static" / "index.html"
    assert P.classify("sinobee/server/static/index.html", ui.read_text(encoding="utf-8"))[0] == "code"


def test_html_decisive_content_then_folder_then_content():
    # decisive content wins over the folder: an animation in reports/ is code, a long text page in src/ is a document
    assert P.classify("reports/app.html", APP_HTML)[0] == "code"
    assert P.classify("src/report.html", REPORT_HTML)[0] == "document"
    # otherwise the folder decides
    mixed = "<html><body><table>" + "<tr><td>1</td><td>2</td></tr>" * 50 + "</table><p>摘要</p></body></html>"
    assert P.classify("src/table.html", mixed)[0] == "code" and P.classify("docs/table.html", mixed)[0] == "document"
    kind, why = P.classify("roadster-spacex-animations.html", APP_HTML)
    assert kind == "code" and "script" in why
    kind, why = P.classify("roadster-spacex-package-report-v3.html", REPORT_HTML)
    assert kind == "document" and "可讀文字" in why
    assert P.classify("C:\\work\\docs\\a.html")[0] == "document"           # Windows paths


def test_document_change_size():
    base = "# 標題\n\n" + "內容" * 500
    assert P.doc_change(P.doc_shape(base), P.doc_shape(base.replace("內容", "內文", 30)))[0] is False   # rewording
    assert P.doc_change(P.doc_shape(base), P.doc_shape(base + "\n## 新章節\n"))[0] is True                # a heading
    more, why = P.doc_change(P.doc_shape(base), P.doc_shape(base + "新" * 300))
    assert more and "門檻" in why
    less, why = P.doc_change(P.doc_shape(base), P.doc_shape(base + "新" * 20))
    assert not less and "未達" in why
    assert P.doc_change(P.doc_shape(base), P.doc_shape(base.replace("\n", "\n\n  ")))[0] is False      # re-wrap


@pytest.mark.parametrize("out,sig", [
    ("==== 2 failed, 10 passed in 0.52s ====", "pytest 2F/10P"),
    ("======== 12 passed in 1.02s ========", "pytest 0F/12P"),
    ("Tests:       1 failed, 7 passed, 8 total", "jest 1F/7P"),
    ("test result: FAILED. 3 passed; 1 failed; 0 ignored", "cargo 1F/3P"),
    ("Ran 5 tests in 0.01s\n\nFAILED (failures=2)", "unittest 2F/3P"),
    ("Ran 5 tests in 0.01s\n\nOK", "unittest 0F/5P"),
    ("ok  \tpkg/a\t0.1s\nFAIL\tpkg/b\t0.2s", "go 1F/1P"),
    ("hello world", None),
])
def test_test_results_are_read(out, sig):
    assert P.test_signature(out) == sig


def tracker(tmp_path, **cfg):
    class C:
        def get(self, key, default=None):
            return cfg if key == "agent.progress" else default
    return P.Tracker(C())


def write(tmp_path, name, text):
    f = tmp_path / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    return ("write_file", {"path": str(f), "content": text}, "ok", False)


def test_code_gets_8_file_only_steps_documents_3(tmp_path):
    t = tracker(tmp_path)
    assert t.observe(1, [write(tmp_path, "a.py", "x = 0")])["strong"]               # new file
    verdicts = [t.observe(i, [write(tmp_path, "a.py", f"x = {i}")]) for i in range(2, 11)]
    assert [v["progress"] for v in verdicts] == [True] * 8 + [False]
    assert "N=8" in verdicts[-1]["why"] and "code" in verdicts[-1]["why"]
    d = tracker(tmp_path)
    base = "# 報告\n\n" + "內容" * 300
    d.observe(1, [write(tmp_path, "r.md", base)])
    verdicts = [d.observe(i, [write(tmp_path, "r.md", base + "改" * i)]) for i in range(2, 6)]
    assert [v["progress"] for v in verdicts] == [True, True, True, False]
    assert "N=3" in verdicts[-1]["why"] and "document" in verdicts[-1]["why"]
    v = d.observe(6, [write(tmp_path, "r.md", base + "\n## 新的一節\n")])           # structure resets the count
    assert v["strong"] and "標題數" in v["why"] and d.file_only == 0


def test_changing_test_results_are_progress_while_editing_code(tmp_path):
    t = tracker(tmp_path)
    t.observe(1, [write(tmp_path, "m.py", "v0")])
    for i in range(2, 20):
        v = t.observe(i, [write(tmp_path, "m.py", f"v{i}"),
                          ("shell", {"command": "pytest"}, f"== {20 - i} failed, {i} passed in 1s ==", False)])
        assert v["progress"] and v["strong"] and "測試結果" in v["why"]
    v = t.observe(20, [("shell", {"command": "pytest"}, "== 20 passed in 1s ==", False)])
    assert v["strong"] and "pytest 1F/19P → pytest 0F/20P" in v["why"]
    v = t.observe(21, [("shell", {"command": "pytest"}, "== 20 passed in 1s ==", False)])
    assert not v["progress"] and "測試結果沒變" in v["why"]


def test_loops_are_noticed_at_once(tmp_path):
    t = tracker(tmp_path)
    for i in range(1, 4):
        v = t.observe(i, [("read_file", {"path": "a.txt"}, "same", False)])
    assert v["loop"]["trigger"] == "loop_repeat" and "3 次" in v["loop"]["detail"]

    t = tracker(tmp_path)                                  # two different failing commands, alternating
    a = ("shell", {"command": "npm run build"}, "[exit 1] cwd=/w\nModule not found: x", True)
    b = ("shell", {"command": "npm install x"}, "[exit 1] cwd=/w\nE404 not found", True)
    out = [t.observe(i, [c]) for i, c in enumerate([a, b, a, b], 1)]
    assert out[-1]["loop"]["trigger"] == "loop_alternating" and "A→B→A→B" in out[-1]["loop"]["detail"]
    # re-reading a file after each edit that changed it is checking, not looping (Roadster turn 4, steps 2-6)
    t = tracker(tmp_path)
    tail = ("shell", {"command": "tail -n 10 build.mjs"}, "</html>\\`;", False)
    seq = [[tail], [write(tmp_path, "build.mjs", "v1")], [tail], [write(tmp_path, "build.mjs", "v2")], [tail]]
    assert not any(t.observe(i, c)["loop"] for i, c in enumerate(seq, 1))
    # scrolling a page and taking screenshots is reading new parts of it, not a loop (Roadster turn 4, steps 29-34)
    t = tracker(tmp_path)
    shots = []
    for i, y in enumerate([0, 0, 720, 720, 1440, 1440], 1):
        if i % 2:
            shots.append(t.observe(i, [("browser", {"action": "scroll", "direction": "down"}, f"Page: R\nScroll: {y}/9000", False)]))
        else:
            shots.append(t.observe(i, [("browser", {"action": "screenshot"}, "Browser screenshot of file:///r.html", False)]))
    assert all(v["progress"] and not v["loop"] for v in shots)
    v = t.observe(7, [("browser", {"action": "scroll", "direction": "down"}, "Page: R\nScroll: 1440/9000", False)])
    assert not v["progress"]                               # at the bottom: nothing new
    t2 = tracker(tmp_path)
    t2.observe(1, [write(tmp_path, "y.py", "A")])
    seq = ["B", "A", "C", "A", "B"]                        # A comes back twice among other edits
    out = [t2.observe(i, [write(tmp_path, "y.py", v), ("read_file", {"path": f"log{i}"}, "x", False)])
           for i, v in enumerate(seq, 2)]
    assert any(v["loop"] and v["loop"]["trigger"] == "file_oscillation" for v in out)

    t = tracker(tmp_path)
    for i in range(1, 4):
        v = t.observe(i, [("read_file", {"path": f"missing{i}.txt"}, f"ERROR: no such file missing{i}.txt", True)])
    assert v["loop"]["trigger"] == "repeated_error" and "相同的錯誤" in v["loop"]["detail"]


# ------------------------------------------------------------------------------------------------ in the agent
def msg(text="", calls=None):
    return Message("assistant", text, [ToolCall(f"c{i}{n}{len(str(a))}", n, a) for i, (n, a) in enumerate(calls or [])])


def run_agent(config, main, thinker=None, platform="web"):
    events = []
    rt = Runtime(config, provider=main, thinking_provider=thinker)
    agent = Agent(rt, session_id="sess-progress", platform=platform, emit=lambda k, d: events.append((k, d)))
    return rt, agent, events


async def test_rewording_a_document_is_stopped_and_every_decision_says_why(home, config):
    config.data["agent"]["progress"] = {**P.DEFAULTS, "k": 4}
    config.data["agent"]["consult_max"] = 1
    config.data["agent"]["failure_diagnosis"] = False
    ws = config.workspace
    base = "# 報告\n\n" + "內容" * 300
    steps = [msg("", [("write_file", {"path": "report.md", "content": base})])]
    steps += [msg("", [("write_file", {"path": "report.md", "content": base + "改" * i})]) for i in range(1, 30)]
    thinker = ScriptedProvider([msg("你一直在改同樣的句子。\nDECISION: continue")])
    thinker.label = "anthropic/thinker"
    rt, agent, events = run_agent(config, ScriptedProvider(steps), thinker)
    await agent.run("寫一份報告到 report.md")
    st = agent.last_status
    assert st["status"] == "failed" and st["error"]["trigger"] == "no_progress"
    report = failures.get(config.home, next(d for k, d in events if k == "turn_end")["failure"])
    log = report["control_log"]
    assert [(e["event"], e["trigger"]) for e in log] == [
        ("consult_requested", "no_progress"), ("consult", "no_progress"),
        ("consult_skipped", "no_progress"), ("stop", "advice_exhausted"), ("delivery", "partial_delivery")]
    req = log[0]
    # the exact numbers: 1 new file + 3 allowed rewordings, then 4 steps without progress (K=4)
    assert req["step"] == 8 and "連續 4 步沒有進度（門檻 K=4）" in req["detail"] and "第 4 步" in req["detail"]
    assert req["thresholds"]["K"] == 4 and req["thresholds"]["N_document"] == 3
    assert any("超過 document 的容忍 N=3" in line for line in req["evidence"])
    assert log[1]["decision"] == "continue" and "anthropic/thinker" in log[1]["detail"]
    assert log[2]["decision"] == "max" and "諮詢次數已用完" in log[2]["detail"]
    assert "advice_exhausted" == log[3]["trigger"] and "連續多步沒有進度" in log[3]["detail"]
    assert report["thresholds"]["K"] == 4 and report["progress"][0]["why"].startswith("新檔案 report.md")
    text = failures.summary_text(report)
    assert "CONTROL DECISIONS" in text and "PROGRESS PER STEP" in text
    assert failures.listing(config.home)[0]["trigger"] == "advice_exhausted"
    # the same entries in the UI events, the run record and logs/control.jsonl
    assert [d["trigger"] for k, d in events if k == "control"] == [e["trigger"] for e in log]
    assert any(k == "status" and "[第 8 步] 請教思維模型" in d["message"] for k, d in events)
    from sinobee.work.hub import get_hub
    run = get_hub(rt).store.run(agent._run_rec.rid)
    assert [c["trigger"] for c in run["state"]["control"]] == [e["trigger"] for e in log]
    assert run["state"]["progress"][-1]["progress"] is False
    rows = P.read_log(config.home, session="sess-progress")
    assert [r["event"] for r in rows] == [e["event"] for e in log] + ["report"]
    assert rows[-1]["failure_id"] == failures.listing(config.home)[0]["id"] and rows[-1]["trigger"] == "advice_exhausted"
    assert all(r["run_id"] == agent._run_rec.rid and r["model"] for r in rows)
    assert (config.home / "logs" / "control.jsonl").exists()


async def test_the_same_edits_to_code_are_not_stopped(home, config):
    config.data["agent"]["progress"] = {**P.DEFAULTS, "k": 4}
    steps = [msg("", [("write_file", {"path": "calc.py", "content": "x = 0"})])]
    steps += [msg("", [("write_file", {"path": "calc.py", "content": f"x = {i}"})]) for i in range(1, 9)]
    steps.append(msg("改好了"))
    rt, agent, events = run_agent(config, ScriptedProvider(steps))
    out = await agent.run("修 calc.py")
    assert out == "改好了" and agent.last_status["status"] == "done"
    assert not [d for k, d in events if k == "control"]                 # 8 code edits are within N=8


async def test_a_repeated_read_asks_the_thinking_model_at_once(home, config):
    config.data["agent"]["failure_diagnosis"] = False
    steps = [msg("", [("list_dir", {"path": "."})]) for _ in range(3)] + [msg("看完了")]
    thinker = ScriptedProvider([msg("你已經看過了，直接回答。\nDECISION: continue")])
    rt, agent, events = run_agent(config, ScriptedProvider(steps), thinker)
    out = await agent.run("看一下資料夾")
    assert out == "看完了"
    ctl = [d for k, d in events if k == "control"]
    assert ctl[0]["trigger"] == "loop_repeat" and ctl[0]["step"] == 3 and "第 1, 2, 3 步" in ctl[0]["detail"]
    assert ctl[1]["event"] == "consult" and ctl[1]["decision"] == "continue"


async def test_the_safety_valve_stops_and_says_it_is_a_system_anomaly(home, config):
    config.data["agent"]["progress"] = {**P.DEFAULTS, "safety_max_steps": 5}
    config.data["agent"]["failure_diagnosis"] = False
    steps = [msg("", [("list_dir", {"path": f"d{i}"})]) for i in range(10)]        # new information every step
    rt, agent, events = run_agent(config, ScriptedProvider(steps))
    await agent.run("一直看")
    st = agent.last_status
    assert st["status"] == "failed" and st["error"]["kind"] == "safety_valve" and st["error"]["category"] == "system_bug"
    stop = [d for k, d in events if k == "control"][-1]
    assert stop["event"] == "stop" and stop["trigger"] == "safety_valve" and "safety_max_steps=5" in stop["detail"]


async def test_the_health_check_asks_every_n_steps_while_the_checklist_is_open(home, config):
    config.data["agent"]["progress"] = {**P.DEFAULTS, "health_steps": 3}
    config.data["agent"]["failure_diagnosis"] = False
    steps = [msg("", [("task_checklist", {"action": "add", "items": ["寫好報告"]})])]
    steps += [msg("", [("list_dir", {"path": f"d{i}"})]) for i in range(3)] + [msg("好了")]
    thinker = ScriptedProvider([msg("繼續做。\nDECISION: continue")])
    rt, agent, events = run_agent(config, ScriptedProvider(steps), thinker)
    await agent.run("做報告")
    ctl = [d for k, d in events if k == "control"]
    assert ctl and ctl[0]["trigger"] == "health_steps" and ctl[0]["step"] == 4
    assert "已執行 3 步，驗收清單尚未完成（健康水位每 3 步檢查一次）" in ctl[0]["detail"]


async def test_tool_errors_still_say_which_threshold(home, config):
    config.data["agent"]["consult_max"] = 1
    config.data["agent"]["failure_diagnosis"] = False
    main = ScriptedProvider([msg("", [("read_file", {"path": f"nope{i}/x.txt"})]) for i in range(12)])
    thinker = ScriptedProvider([msg("先列出資料夾\nDECISION: continue")])
    rt, agent, events = run_agent(config, main, thinker)
    await agent.run("讀檔")
    ctl = [d for k, d in events if k == "control"]
    assert ctl[0]["trigger"] in ("repeated_error", "consecutive_errors")
    assert ctl[-1]["event"] == "stop" and ctl[-1]["trigger"] == "advice_exhausted"
    assert agent.last_status["status"] == "failed"


def test_control_log_api_and_cli(home, config, capsys):
    from fastapi.testclient import TestClient
    from sinobee.server import create_app
    for i, trig in enumerate(["no_progress", "loop_repeat", "no_progress"]):
        P.append_log(config.home, {**P.entry("consult_requested", trig, i + 1, f"detail {i}", None),
                                   "session_id": "s1" if i < 2 else "s2"})
    with TestClient(create_app(Runtime(config, provider=ScriptedProvider()))) as c:
        r = c.get("/api/control-log").json()
        assert len(r["entries"]) == 3 and r["entries"][0]["text"].startswith("[第 3 步] 請教思維模型")
        assert r["counts"][0] == {"event": "consult_requested", "trigger": "no_progress", "label": "連續多步沒有進度", "n": 2}
        assert len(c.get("/api/control-log?session=s1").json()["entries"]) == 2
        assert len(c.get("/api/control-log?trigger=loop_repeat").json()["entries"]) == 1
    from sinobee.cli import main
    main(["control-log", "--stats"])
    assert "no_progress" in capsys.readouterr().out
    main(["control-log", "--trigger", "loop_repeat", "--evidence"])
    out = capsys.readouterr().out
    assert "同一操作重複且沒有進度" in out and "detail 1" in out


def test_code_and_report_in_one_task_keep_the_code_n(tmp_path):
    """A task that fixes code and writes its report must not be judged by the document N=3."""
    t = tracker(tmp_path)
    t.observe(1, [write(tmp_path, "calc.py", "v0"), write(tmp_path, "REPORT.md", "# 報告\n\n" + "內容" * 300)])
    both = [t.observe(i, [write(tmp_path, "calc.py", f"v{i}"), write(tmp_path, "REPORT.md", "# 報告\n\n" + "內容" * 300 + "改" * i)])
            for i in range(2, 10)]
    assert all(v["progress"] for v in both) and "code+document" in both[-1]["why"] and "N=8" not in both[-1]["why"]
    assert "8/8" in both[-1]["why"]
    t = tracker(tmp_path)                                  # alternating steps: .py, .md, .py, .md ...
    t.observe(1, [write(tmp_path, "a.py", "v0")])
    t.observe(2, [write(tmp_path, "a.md", "# A\n\n" + "字" * 400)])
    alt = [t.observe(i, [write(tmp_path, "a.py", f"v{i}") if i % 2 else write(tmp_path, "a.md", "# A\n\n" + "字" * 400 + "改" * i)])
           for i in range(3, 11)]
    assert all(v["progress"] for v in alt)
    t = tracker(tmp_path)                                  # one early code edit does not relax a reworded document forever
    t.observe(1, [write(tmp_path, "b.md", "# B\n\n" + "字" * 400)])
    t.observe(2, [write(tmp_path, "b.py", "x = 1")])
    t.observe(3, [write(tmp_path, "b.py", "x = 2")])
    doc = [t.observe(i, [write(tmp_path, "b.md", "# B\n\n" + "字" * 400 + "改" * i)]) for i in range(4, 14)]
    stalled = [v for v in doc if not v["progress"]]
    assert stalled and "document" in stalled[0]["why"] and "N=3" in stalled[0]["why"]


async def test_replay_reproduces_the_live_decisions(home, config):
    """The offline replay (stored transcript only) reaches the same consult as the live turn."""
    from sinobee.agent import replay as R
    config.data["agent"]["progress"] = {**P.DEFAULTS, "k": 4}
    config.data["agent"]["consult_max"] = 1
    config.data["agent"]["failure_diagnosis"] = False
    base = "# 報告\n\n" + "內容" * 300
    steps = [msg("", [("write_file", {"path": "report.md", "content": base})])]
    steps += [msg("", [("write_file", {"path": "report.md", "content": base + "改" * i})]) for i in range(1, 30)]
    rt, agent, events = run_agent(config, ScriptedProvider(steps), ScriptedProvider([msg("改章節\nDECISION: continue")]))
    await agent.run("寫一份報告到 report.md")
    live = [(d["step"], d["trigger"]) for k, d in events if k == "control" and d["event"] == "consult_requested"]
    rows = R.replay_sessions(rt.memory, config, session=agent.session_id)
    assert rows and [(d["step"], d["trigger"]) for d in rows[0]["decisions"]] == live
    assert rows[0]["stop"]["trigger"] == "advice_exhausted"
    s = R.summary(rows)
    assert s["turns"] == 1 and s["consults"] == {"no_progress": 1} and s["near_safety_valve"] == []


def test_exploring_without_producing_is_a_stall(tmp_path):
    """The stalled Roadster turn: 58 steps, no file written, the same skill / folder / report read again and again
    with a few new reads in between - those kept resetting the K count, so it needs its own rule."""
    t = tracker(tmp_path)
    reads = [("skill_view", {"name": "anatomy"}), ("shell", {"command": "ls -la"}),
             ("read_file", {"path": "reports/youtube/report.md"}), ("working_memory", {"action": "view"})]
    out = []
    for i in range(1, 41):
        name, args = reads[i % 4] if i % 5 else ("shell", {"command": f"ls dir{i}"})   # every 5th step is new
        out.append(t.observe(i, [(name, args, "same output", False)]))
    stall = next((v for v in out if v["loop"] and v["loop"]["trigger"] == "exploration_stall"), None)
    assert stall and "沒有寫任何檔案" in stall["loop"]["detail"] and max(v["no_progress"] for v in out) < 15
    t.advised(stall["step"])                                  # advice opens a fresh window
    assert not t._loop(stall["step"] + 1) or t._loop(stall["step"] + 1)["trigger"] != "exploration_stall"
    # research that keeps reading NEW sources is not a stall
    r = tracker(tmp_path)
    vs = [r.observe(i, [("web_fetch", {"url": f"https://example.com/{i}"}, f"page {i}", False)]) for i in range(1, 41)]
    assert not any(v["loop"] for v in vs)


def test_polling_a_running_job_is_waiting_not_a_loop(tmp_path):
    """The video render: the same status command, output still "working" - neutral for K and loops; a very long
    wait asks the thinking model (long_wait)."""
    t = tracker(tmp_path, wait_limit=6)
    poll = ("skill_run", {"name": "ai-video-production", "script": "scripts/status.py"},
            '{\n "status": "working",\n "done": 3}', False)
    out = [t.observe(i, [poll]) for i in range(1, 7)]
    assert out[0]["progress"] and all(v["waiting"] for v in out[1:]) and t.no_progress == 0
    assert not any(v["loop"] for v in out[:-1]) and out[-1]["loop"] is None
    v = t.observe(7, [poll])
    assert v["loop"]["trigger"] == "long_wait" and "6 次" in v["loop"]["detail"]
    # the same command with an unchanged non-running output is a repeat, not a wait
    r = tracker(tmp_path)
    same = ("shell", {"command": "cat out.txt"}, "hello", False)
    vs = [r.observe(i, [same]) for i in range(1, 4)]
    assert not vs[-1]["waiting"] and vs[-1]["loop"]["trigger"] == "loop_repeat"


async def test_an_agent_fixing_code_and_writing_its_report_is_not_stopped(home, config):
    """End to end: every step edits calc.py AND rewords REPORT.md (no structural change). With K=4 a document-only
    N=3 would stall it after step 4; the code N=8 must hold."""
    config.data["agent"]["progress"] = {**P.DEFAULTS, "k": 4}
    report = "# 修正報告\n\n" + "說明" * 300
    steps = [msg("", [("write_file", {"path": "calc.py", "content": "x = 0"}),
                      ("write_file", {"path": "REPORT.md", "content": report})])]
    steps += [msg("", [("write_file", {"path": "calc.py", "content": f"x = {i}"}),
                       ("write_file", {"path": "REPORT.md", "content": report + "補" * i})]) for i in range(1, 9)]
    steps.append(msg("修好了，報告也寫了"))
    rt, agent, events = run_agent(config, ScriptedProvider(steps))
    assert await agent.run("修 calc.py 並寫修正報告") == "修好了，報告也寫了"
    assert agent.last_status["status"] == "done" and not [d for k, d in events if k == "control"]
    trace = agent._progress.trace
    assert "code+document" in trace[-1]["why"] and "8/8" in trace[-1]["why"]
