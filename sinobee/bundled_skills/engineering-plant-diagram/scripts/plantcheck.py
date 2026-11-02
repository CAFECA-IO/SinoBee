"""plantcheck.py — 組裝與檢查工程廠區圖（engineering-plant-diagram 技能）

  scaffold: python3 plantcheck.py scaffold <資料夾>          → 複製範例模型為 <資料夾>/model.js
  build:    python3 plantcheck.py build <model.js> <out.html>
  check:    python3 plantcheck.py check <out.html> [輸出前綴] → 流向表、lint、雙視角版面檢查、前綴-iso.png / 前綴-plan.png

build 從技能的 assets/ 讀取 plant-kit.js 與 template.html。check 用 playwright（chromium）做完整檢查；
沒有 playwright 時改用 node 只做模型 lint（流向、端點、重疊），並提示安裝 playwright 以檢查版面。
結束碼：0 = 無 error，1 = 有 error。
"""
import json
import os
import sys
from pathlib import Path

read = lambda f: Path(f).read_text(encoding='utf-8')
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'assets')

def build(model, out, kit=None, tpl=None):
    kit = kit or os.path.join(ASSETS, 'plant-kit.js'); tpl = tpl or os.path.join(ASSETS, 'template.html')
    t = read(tpl)
    m = read(model)
    title = m.split("title:", 1)[1].split("'")[1] if "title:" in m else '工程配置圖'
    t = t.replace('/*__KIT__*/', read(kit)).replace('/*__MODEL__*/', m).replace('__TITLE__', title)
    Path(out).write_text(t, encoding='utf-8')
    print(f'built {out}')

DOM_CHECK = r"""(view) => {
  const svg = document.getElementById('plant'), E = [], W = [];
  PlantKit.setView(svg, view);
  const V = document.getElementById(view === 'iso' ? 'view-iso' : 'view-plan'), ov = document.getElementById('overlay');
  const defs = svg.querySelector('defs'), ids = new Set([...svg.querySelectorAll('[id]')].map(e => e.id));
  if (!svg.getAttribute('viewBox')) E.push('缺 viewBox');
  if (!defs || defs.querySelectorAll('symbol').length < 5) E.push('<defs> symbol 不足');
  const dup = {}; svg.querySelectorAll('[id]').forEach(e => dup[e.id] = (dup[e.id] || 0) + 1);
  Object.entries(dup).filter(([, n]) => n > 1).forEach(([k]) => E.push(`id 重複 #${k}`));
  svg.querySelectorAll('use').forEach(u => { const r = (u.getAttribute('href') || '').slice(1); if (!ids.has(r)) E.push(`<use> 參照不存在 #${r}`); });
  const facSel = view === 'iso' ? 'g.facility-iso' : 'g.facility';
  if (V.querySelectorAll(facSel).length < 2) E.push(`${view}: 設施群組不足`);
  const used = new Set([...V.querySelectorAll('path.pipe')].map(p => p.dataset.pipe && [...p.classList].find(c => c.startsWith('pipe--'))?.slice(6)).filter(Boolean));
  const leg = new Set([...document.querySelectorAll('#legend [data-pipe]')].map(e => e.dataset.pipe));
  used.forEach(t => { if (!leg.has(t)) E.push(`圖例缺 ${t}`); });
  V.querySelectorAll('path.pipe').forEach(p => { if (!p.dataset.from || !p.dataset.to) E.push(`${p.id} 缺 data-from/to`); });
  const k = svg.getBoundingClientRect().width / svg.viewBox.baseVal.width;
  const box = e => { const r = e.getBoundingClientRect(); return [r.left / k, r.top / k, r.right / k, r.bottom / k]; };
  const hit = (a, b, m = 1) => a[0] < b[2] - m && b[0] < a[2] - m && a[1] < b[3] - m && b[1] < a[3] - m;
  const vb = svg.viewBox.baseVal;
  // 標籤單元：iso 引線標籤以整個框為單位，其餘以 text 為單位
  const units = [];
  V.querySelectorAll('.lbl-body').forEach(r => units.push([r.querySelector('text').textContent, box(r), 'box']));
  V.querySelectorAll('text').forEach(t => { if (!t.closest('.lbl-body')) units.push([t.textContent.trim(), box(t), t.classList.contains('pipe-label') ? 'pl' : 't']); });
  ov.querySelectorAll(':scope > text, #legend, #design-basis, #north').forEach(e => units.push([e.id || e.textContent.trim(), box(e), 'ov']));
  for (let i = 0; i < units.length; i++) { const a = units[i];
    if (a[1][0] < 0 || a[1][1] < 0 || a[1][2] > vb.width || a[1][3] > vb.height) W.push(`${view}: 超出畫布 "${a[0].slice(0, 14)}"`);
    for (let j = i + 1; j < units.length; j++) { const b = units[j]; if (a[2] === 'ov' && b[2] === 'ov') continue;
      if (hit(a[1], b[1])) W.push(`${view}: 標籤重疊 "${a[0].slice(0, 14)}" ↔ "${b[0].slice(0, 14)}"`); } }
  // 管線穿過設施名稱/指標（pipe-label 有光暈，可貼線）
  const T = units.filter(u => u[2] === 't' || u[2] === 'box' || u[2] === 'ov');
  V.querySelectorAll('path.pipe').forEach(p => { const L = p.getTotalLength(), M = p.getScreenCTM(), h = new Set();
    for (let s = 0; s <= L; s += 3) { const q = p.getPointAtLength(s), x = (M.a * q.x + M.c * q.y + M.e) / k, y = (M.b * q.x + M.d * q.y + M.f) / k;
      T.forEach(u => { if (x > u[1][0] - 2 && x < u[1][2] + 2 && y > u[1][1] - 2 && y < u[1][3] + 2) h.add(u[0].slice(0, 14)); }); }
    h.forEach(t => W.push(`${view}: 管線 ${p.dataset.pipe} 穿過 "${t}"`)); });
  return { E, W, stats: { fac: V.querySelectorAll(facSel).length, pipes: V.querySelectorAll('path.pipe').length,
           symbols: defs.querySelectorAll('symbol').length, markers: defs.querySelectorAll('marker').length, uses: V.querySelectorAll('use').length } };
}"""

def scaffold(folder):
    import shutil
    os.makedirs(folder, exist_ok=True)
    dst = os.path.join(folder, 'model.js')
    if os.path.exists(dst): sys.exit(f'{dst} 已存在，不覆寫')
    shutil.copy(os.path.join(ASSETS, '..', 'references', 'model-example-wwtp.js'), dst)
    print(f'scaffolded {dst}（範例：A²O 污水廠，請依任務改寫）')

def lint_only(html):
    """沒有 playwright：用 node 執行 PlantKit.lint(MODEL)（不需 DOM）"""
    import re
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        sys.exit('ERROR 無法檢查：需要 playwright（python -m pip install playwright && python -m playwright install chromium）或 node')
    scripts = re.findall(r'<script>(.*?)</script>', read(html), re.DOTALL)
    js = scripts[0] + '\n' + scripts[1] + '\nconsole.log(JSON.stringify(PlantKit.lint(MODEL)));'
    r = subprocess.run([node, '-'], input=js, capture_output=True, text=True, timeout=60, check=False)
    if r.returncode: sys.exit('ERROR 模型無法執行: ' + r.stderr.strip()[:500])
    lint = json.loads(r.stdout)
    print('=== 流向表（逐條核對真實工程邏輯）===')
    for e in lint['edges']: print('  ' + e)
    for w in lint['warnings']: print('WARN  [model]', w)
    for e in lint['errors']: print('ERROR [model]', e)
    print('WARN  未安裝 playwright：只做了模型 lint，沒有檢查版面與輸出預覽圖。'
          '安裝：python -m pip install playwright && python -m playwright install chromium')
    print(f"\n結果: {len(lint['errors'])} error（僅模型 lint）")
    sys.exit(1 if lint['errors'] else 0)

def check(html, prefix):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return lint_only(html)
    errs = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={'width': 1600, 'height': 960}, device_scale_factor=2)
        logs = []; pg.on('console', lambda m: m.type == 'error' and logs.append(m.text)); pg.on('pageerror', lambda e: logs.append(str(e)))
        pg.goto('file://' + __import__('os').path.abspath(html)); pg.wait_for_timeout(500)
        for l in logs: errs.append(f'JS 錯誤: {l}')
        if logs: print('\n'.join(errs)); sys.exit(1)
        lint = pg.evaluate('PlantKit.lint(MODEL)')
        print('=== 流向表（逐條核對真實工程邏輯）===')
        for e in lint['edges']: print('  ' + e)
        for w in lint['warnings']: print('WARN  [model]', w)
        for e in lint['errors']: print('ERROR [model]', e); errs.append(e)
        for view in ('iso', 'plan'):
            r = pg.evaluate(DOM_CHECK, view); pg.wait_for_timeout(450)
            pg.locator('#plant').screenshot(path=f'{prefix}-{view}.png')
            print(f'--- {view}: {json.dumps(r["stats"], ensure_ascii=False)}')
            for w in r['W']: print('WARN ', w)
            for e in r['E']: print('ERROR', e); errs.append(e)
        b.close()
    print(f'\n結果: {len(errs)} error（預覽 {prefix}-iso.png / {prefix}-plan.png）')
    sys.exit(1 if errs else 0)

USAGE = __doc__

if __name__ == '__main__':
    a = sys.argv[1:]
    if not a or a[0] in ('-h', '--help'): print(USAGE); sys.exit(0)
    if a[0] == 'scaffold' and len(a) == 2: scaffold(a[1])
    elif a[0] == 'build' and len(a) >= 3: build(*a[1:5])
    elif a[0] == 'check' and len(a) >= 2: check(a[1], a[2] if len(a) > 2 else 'preview')
    else: print(USAGE); sys.exit(2)
