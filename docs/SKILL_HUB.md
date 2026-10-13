# SinoBee Skill 規格、信任機制與 Skill Hub（sinobee.ai）

> 狀態：用戶端已實作（`sinobee/skills/`）；sinobee.ai Hub 服務端為本文件定義的協定，尚未上線。

## 1. 技能格式：Agent Skills 標準

SinoBee 的技能完全遵循 [Agent Skills 規格](https://agentskills.io/specification)，可與其他支援此標準的 agent（Claude Code、Codex、Hermes…）互通。

```
<skill-name>/
├── SKILL.md          必要：YAML frontmatter + Markdown 指令
├── scripts/          選用：可執行腳本（非互動、--help、結構化輸出）
├── references/       選用：按需載入的文件
└── assets/           選用：範本、資料、圖片
```

**Frontmatter 只允許六個欄位**（`skills-ref validate` 會拒絕其他欄位）：

| 欄位 | 必要 | 規則 |
|---|---|---|
| `name` | ✓ | 1–64 字元，小寫英數與單一連字號，不得以 `-` 開頭/結尾或含 `--`，**須等於資料夾名稱** |
| `description` | ✓ | 1–1024 字元，說明「做什麼」與「何時使用」，含觸發關鍵字 |
| `license` | | 授權名稱或隨附授權檔 |
| `compatibility` | | ≤ 500 字元，環境需求 |
| `allowed-tools` | | 空白分隔的預先核准工具（實驗性），例：`Bash(git:*) browser` |
| `metadata` | | 字串對字串的映射 |

**SinoBee 擴充一律放在 `metadata`（值皆為字串）**：

| key | 用途 |
|---|---|
| `version`、`author` | 生態通用 |
| `sinobee-tags` | 逗號分隔標籤 |
| `sinobee-triggers` | 觸發關鍵字（可多語），輔助檢索 |
| `sinobee-requires-tools` | 需要的 SinoBee 工具；工具不可用時自動隱藏 |
| `sinobee-platforms` | `linux, macos, windows` |
| `sinobee-forked-from` | 由 `skill_fork` 自動填入 |

範例：

```markdown
---
name: einvoice-download-tw
description: Downloads Taiwan e-invoices from the MOF portal as CSV. Use when the user asks for 電子發票, invoice exports or tax filing data.
license: MIT
metadata:
  version: "1.2.0"
  author: cafeca
  sinobee-tags: invoice, tax, browser
  sinobee-triggers: 電子發票, 發票, e-invoice
  sinobee-requires-tools: browser
---
# When to Use
# Procedure
# Gotchas
# Verification
```

**讀寫原則**
- **寬鬆讀取**（依 agentskills.io 用戶端實作指引）：接受其他用戶端或舊版格式（頂層 `tags`/`version`、`metadata.hermes.*`、未加引號的冒號、flow-style 清單），自動正規化並記錄診斷訊息；缺少 description 或 YAML 完全無法解析才跳過。
- **嚴格寫入**：SinoBee（含 agent 自動生成）寫出的每個 SKILL.md 都先正規化、通過與 `skills-ref` 相同的驗證、不使用 flow-style YAML、並通過安全掃描。測試中直接以官方 `skills-ref` 驗證內建技能與生成結果。
- **漸進揭露**：目錄只列 name + description（`<available_skills>`）；`skill_view` 啟用時才載入本文（去除 frontmatter、以 `<skill_content>` 包裝，附技能目錄與 `<skill_resources>` 清單）；附件按需讀取。
- 啟用中的技能指令在上下文壓縮時**不會被摘要掉**；同一 session 重複啟用會去重；`skill_view` 的 name 參數以 enum 限定為可見技能。
- 使用者可用 `/skill-name`（最多 5 個）直接啟用技能。

## 2. 技能路徑與範圍

| 範圍 | 路徑 | 說明 |
|---|---|---|
| 專案 | `<project>/.sinobee/skills/` | SinoBee 原生；可隨 repo 版本控制、團隊共享 |
| 專案（互通） | `<project>/.agents/skills/` | 跨用戶端慣例 |
| 全域 / 共享 | `~/.sinobee/skills/` | 使用者層級，所有專案共用；內建技能也安裝在此 |
| 全域（互通） | `~/.agents/skills/` | 其他 agent 安裝的技能自動可見 |
| 額外 | `skills.extra_dirs` | 唯讀，例如團隊共用網路磁碟 |

- 專案目錄：`SINOBEE_PROJECT` > 設定 `skills.project_dir` > 目前工作目錄；會往上掃描到 git 根目錄（monorepo）。
- **同名優先序：專案 > 全域 > 額外**。但若較高優先的副本不可用（未信任/封鎖），會改用下一個可用副本，避免惡意 repo 以同名技能「蓋掉」使用者的技能。被遮蔽的副本會顯示在 GUI。
- 版本歷史與刪除備份放在 `~/.sinobee/skills/.history/`（不污染專案 repo）。

## 3. 信任級別

信任**完全由 SinoBee 依來源與內容雜湊判定**，技能檔內寫什麼都不影響（避免技能自稱可信）。

| 級別 | 標籤 | 來源 | 目錄可見 | 腳本 | `allowed-tools` | agent 可直接修改 |
|---|---|---|---|---|---|---|
| `builtin` | 內建 | SinoBee 隨附，雜湊與發行版一致 | ✓ | 一般核准規則 | ✓ | ✗（需 fork） |
| `verified` | 官方驗證 | Hub：驗證發布者 + 已審核版本 + ed25519 簽章正確 | ✓ | 一般核准規則 | ✓ | ✗（需 fork） |
| `local` | 本機 | 使用者撰寫、放入、或核准的技能 | ✓ | 一般核准規則 | ✓ | ✓ |
| `project` | 專案 | 已信任專案中的技能（雜湊與信任時一致） | ✓ | 每次需核准 | ✗ | ✓ |
| `agent` | 自動生成 | SinoBee 學習迴圈產生，尚未經使用者核准 | ✓（標示 experimental） | 每次需核准 | ✗ | ✓ |
| `community` | 社群 | Hub 未審核、URL、GitHub；或安裝後被修改 | ✓（附警語） | 每次需核准 | ✗ | ✗（需 fork） |
| `untrusted` | 未信任 | 未信任的專案；信任後新增/變更的專案技能 | ✗ | 禁止 | ✗ | ✗ |
| `blocked` | 已封鎖 | 安全掃描不通過，或被 Hub 撤銷 | ✗ | 禁止 | ✗ | ✗ |

- 「每次需核准」在 `approval: auto`（沙箱）模式下自動通過，其他模式一律詢問。
- community / agent 技能啟用時附加警語：只當參考，不得據以做使用者未要求的事。
- **出處鎖檔** `~/.sinobee/skills/.sinobee-lock.json`：記錄每個技能的信任、來源、ref、內容雜湊、發布者、版本、簽章與審核狀態；內容雜湊不符即視為「已修改」。
- **專案信任** `~/.sinobee/trusted_projects.json` 存在專案之外（repo 無法自我信任）：信任時釘選每個技能的雜湊，之後 `git pull` 帶進的新技能或變更需重新核准。
- 內容雜湊：對技能資料夾內所有非隱藏檔案，依相對路徑排序，計算 `sha256(path \0 bytes \0 …)`，格式 `sha256:<hex>`。

## 4. 自適應技能生成

| 類型 | 觸發 | 行為 |
|---|---|---|
| 被動 | 使用者說「記住這個做法／做成技能」 | 任務後反思強制建立或修補技能 |
| 被動 | `/learn <URL｜路徑｜描述>` | agent 依 skill-authoring 技能讀取素材、檢查重複、建立技能 |
| 被動 | Web UI「從此對話學習技能」 | 以整段對話為素材產生技能 |
| 主動 | 缺口：多步驟或曾出錯修復的任務，且沒有相符技能 | 反思時評估是否建立 |
| 主動 | 重複：過往 ≥ 2 個相似任務（情節記憶） | 強烈建議建立技能 |
| 主動 | 失敗：使用的技能失敗或被使用者糾正 | 修補；唯讀技能先 fork 成 `<name>-custom` 再修補 |

- **生命週期**：自動生成 → `agent` 信任 + `experimental` 狀態 → 成功使用 `promote_after`（預設 2）次或使用者核准 → `active`；成功率低於門檻 → `NEEDS REVISION`，下次修補。
- **模式** `skills.auto_create`：`auto`（直接建立為 experimental）、`suggest`（暫存待使用者在 GUI 核准）、`off`。
- 生成內容依 agentskills.io 最佳實務：以實際執行過程與使用者修正為依據、只寫 agent 不知道的事、單一預設做法、Gotchas、驗證步驟、長內容放 references/。

## 5. sinobee.ai Skill Hub 協定（v1）

### 5.1 用戶端指令

```bash
sinobee skills search <關鍵字>                 # 搜尋 Hub
sinobee skills install hub:cafeca/einvoice-download-tw[@1.2.0]
sinobee skills install github:owner/repo/path/to/skill[@ref]
sinobee skills install https://example.com/skill.tar.gz | .../SKILL.md
sinobee skills install ./my-skill [--scope project] [--trust local]
sinobee skills outdated | update <name> [--force]
sinobee skills export <name> [-o dist/]        # 打包：<name>-<version>.tar.gz + manifest
sinobee skills publish <name>                  # 需先 sinobee keys set sinobee-hub
sinobee skills sync-revocations                # 取得撤銷清單，封鎖已撤銷的技能
```

### 5.2 API

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/api/v1/skills?q=&page=` | 搜尋：`{items:[{id, name, description, latest, trust, publisher, downloads}]}` |
| GET | `/api/v1/skills/{publisher}/{name}` | 詳情：`{id, name, license, publisher:{id, verified}, versions:[{version, sha256, bundle_url, signature, reviewed, published_at, scan:{verdict, findings}}]}`，versions 由新到舊 |
| GET | `bundle_url` | `.tar.gz`，內含單一 `<name>/` 資料夾與 `.sinobee-manifest.json` |
| GET | `/api/v1/revocations` | `{revoked:["sha256:…"]}`：被撤銷的內容雜湊 |
| POST | `/api/v1/skills` | 發布（Bearer token）：multipart `bundle` + `manifest` → `{id, version, status:"pending_review"}` |
| GET | `/.well-known/sinobee-hub-keys.json` | Hub 簽章公鑰（ed25519, base64）；用戶端以 `skills.hub_keys` 釘選 |

`manifest`：`{spec:"agentskills.io", name, version, description, license, content_hash, files:{path: sha256}, exported_at, client}`

### 5.3 驗證與信任流程（用戶端）

1. 下載 bundle（≤ 20 MB、≤ 500 檔；拒絕路徑穿越、符號連結、裝置檔）。
2. 依 Agent Skills 規格驗證；計算內容雜湊並與 Hub 宣告的 `sha256` 比對，不符即中止。
3. 以釘選公鑰驗證 `signature`（對 `content_hash` 字串的 ed25519 簽章）。
4. Hub 掃描判定 `dangerous` 即中止；本機再做一次安全掃描（注入、憑證、不可見字元、危險腳本）。
5. 信任判定：發布者已驗證 **且** 版本已審核 **且** 簽章正確 → `verified`；否則 `community`。
6. 寫入出處鎖檔；之後內容被改動 → 自動降為 `community`（已修改），使用者可核准為 `local`。

### 5.4 Hub 端治理（服務端待實作）

- **發布者等級**：一般帳號 → 驗證發布者（組織網域或身分驗證）。
- **審核流程**：自動化（規格驗證、`skills-ref`、惡意模式掃描、腳本靜態分析、沙箱試跑）→ 人工審核 → 簽章 → 標記 reviewed。
- **版本不可變**：已發布版本內容不可修改，只能發新版本或撤銷。
- **撤銷**：發現惡意或嚴重問題時，把內容雜湊加入撤銷清單；用戶端同步後自動封鎖。
- **評價與遙測（選擇加入）**：用戶端可回報匿名成功率，協助排序與品質審核。
