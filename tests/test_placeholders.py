"""A file built in parts must not be delivered with sections still empty (2026-10-21): the completion checks report
a skeleton placeholder with nothing written after it; a marker kept as a label above written content is fine."""
import asyncio

from sinobee.agent import qa


def test_empty_placeholders_are_found_and_labels_are_not():
    page = """<html><body><svg>
<!-- SECTION: tower -->
<g id="tower"><rect/></g>
<!-- SECTION: rotor -->
<!-- SECTION: nacelle -->
</svg>
<script>
/* SECTION: slider */
/* SECTION: physics */
function p(v){ return 0.5*v; }
// SECTION: readout
}
</script><!-- TODO: tidy the colours -->
<p>參數表</p><!-- 待填：參數表 --></body></html>"""
    left = qa.empty_placeholders(page)
    assert left == ["<!-- SECTION: rotor -->", "<!-- SECTION: nacelle -->", "/* SECTION: slider */",
                    "// SECTION: readout", "<!-- 待填：參數表 -->"]
    assert qa.empty_placeholders("<!-- SECTION: tower -->\n<g/>\n<!-- SECTIONS below -->\n<p/>") == []


def test_the_completion_checks_send_the_file_back(tmp_path):
    f = tmp_path / "wind.html"
    f.write_text("<html><body><!-- SECTION: tower -->\n</body></html>", encoding="utf-8")
    js = tmp_path / "app.js"
    js.write_text("function a(){\n/* SECTION: physics */\n}\n", encoding="utf-8")
    issues = qa.check_file(f) + qa.check_file(js)
    assert [i["name"] for i in issues] == ["wind.html", "app.js"]
    assert "骨架區塊沒有填" in issues[0]["problem"] and "SECTION: tower" in issues[0]["detail"]
    f.write_text("<html><body><!-- SECTION: tower -->\n<p>塔架</p></body></html>", encoding="utf-8")
    assert not any("骨架" in i["problem"] for i in asyncio.run(qa.check([f])))
