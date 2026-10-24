#!/usr/bin/env bash
# Sandbox plan stage 3 - checks to run on your own Mac (Docker Desktop):
#   1. plan C: can Docker Desktop overlay one of your folders (no copy, changes kept apart)?
#   2. LibreOffice in the office sandbox image: fill a Word form and a spreadsheet, export PDF, recalculate.
# Usage: scripts/acceptance/stage3.sh [a big folder to try the overlay on, default ~/Documents]
set -uo pipefail
cd "$(dirname "$0")/../.."
FOLDER=${1:-$HOME/Documents}
PY=${PYTHON:-python3}
VER=$($PY -c 'import sinobee; print(sinobee.__version__)')
echo "== 1. overlay of $FOLDER (nothing in it is written)"
$PY -m sinobee.cli sandbox probe-overlay "$FOLDER"; OVL=$?

echo; echo "== 2. LibreOffice in sinobee-sandbox-office:$VER"
if ! docker image inspect "sinobee-sandbox-office:$VER" >/dev/null 2>&1; then
  echo "   the office image is not built: Settings → 沙盒資源與管理 → 含 LibreOffice → 建立 (or: sinobee sandbox build --office)"
  OFF=2
else
  docker run --rm --network none --user 1000 -e HOME=/tmp -w /tmp "sinobee-sandbox-office:$VER" bash -lc '
set -e
python3 - <<PY
from docx import Document
d = Document(); d.add_heading("請款單", 1)
t = d.add_table(rows=3, cols=2)
for i, (k, v) in enumerate([("姓名", "{{name}}"), ("金額", "{{amount}}"), ("日期", "{{date}}")]):
    t.cell(i, 0).text, t.cell(i, 1).text = k, v
d.save("form.docx")
d = Document("form.docx")
fill = {"{{name}}": "王小明", "{{amount}}": "NT$ 12,345", "{{date}}": "2026-10-24"}
for row in d.tables[0].rows:
    for c in row.cells:
        for k, v in fill.items():
            if k in c.text: c.text = c.text.replace(k, v)
d.save("filled.docx")
from openpyxl import Workbook
wb = Workbook(); ws = wb.active
ws["A1"], ws["A2"], ws["A3"] = 120, 80, "=A1+A2"
wb.save("sum.xlsx")
PY
soffice --headless --convert-to pdf filled.docx >/dev/null 2>&1
soffice --headless --convert-to "xlsx:Calc MS Excel 2007 XML" --outdir recalc sum.xlsx >/dev/null 2>&1
python3 - <<PY
import zipfile, re
from openpyxl import load_workbook
v = load_workbook("recalc/sum.xlsx", data_only=True).active["A3"].value
import subprocess
txt = subprocess.run(["pdftotext", "filled.pdf", "-"], capture_output=True, text=True).stdout if __import__("shutil").which("pdftotext") else ""
ok_pdf = __import__("os").path.getsize("filled.pdf") > 1000
print("   form filled + PDF:", "ok" if ok_pdf else "FAILED", ("(王小明 found in PDF text)" if "王小明" in txt else ""))
print("   spreadsheet recalculated A3 =", v, "ok" if v == 200 else "FAILED")
raise SystemExit(0 if ok_pdf and v == 200 else 1)
PY
'; OFF=$?
fi

echo; echo "== results: overlay $([ $OVL = 0 ] && echo works || echo 'does not work') · LibreOffice $([ $OFF = 0 ] && echo ok || ([ $OFF = 2 ] && echo 'image not built' || echo FAILED))"
echo "Next by hand (in SinoBee, approval mode 沙盒):"
echo "  a. 檔案選擇視窗上傳: ask SinoBee to upload a file through a site's 'choose file' button"
echo "  b. Thorium: rerun the Thorium MSR task of claude/SinoBee-EVAL-thorium-msr.md in a new conversation"
echo "  c. G7: ask for a task on a site you are signed in to → allow 'browser' when it asks to use your Mac"
echo "  d. git: give a git project (審核後寫回), let it change files, then 審核變更 → 交付到分支"
