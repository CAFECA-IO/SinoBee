# SinoBee 架構設計

> 版本：0.1（MVP）・ 參考對象：[Hermes Agent](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture)（Nous Research）

SinoBee 是一個「不內建模型」的通用 AI Agent。它把任何主流 LLM 當作大腦，透過**經授權的電腦操作能力**（Shell、檔案、滑鼠鍵盤、瀏覽器）完成任務，並以**分層記憶**與**自我演化技能**讓自己越用越好。

---

## 1. 設計目標

| 目標 | 做法 |
|---|---|
| 模型中立 | 內部統一訊息格式，透過 Provider 轉接任何 API（OpenAI 相容、Anthropic、Gemini、本地 Ollama/vLLM…），支援備援鏈 |
| 能做任何事 | 以「工具」操作電腦：Shell、檔案、桌面（滑鼠/鍵盤/截圖）、瀏覽器（Playwright）、網頁搜尋 |
| 可在 Docker 執行 | 映像內建 Xvfb 虛擬桌面 + noVNC，agent 在容器中有完整桌面，使用者可即時觀看 |
| 授權可控 | Toolset 白名單 + 三段式核准模式（auto / confirm / readonly）+ 危險指令偵測 |
| 越用越強 | 分層記憶（核心 / 工作 / 長期語意 / 情節）＋ 技能自動建立、修補、評分、版本化 |
| Prompt 快取友善 | System prompt 只放穩定內容；動態內容（回憶、工作記憶、相關技能）放在訊息尾端 |

## 2. 系統總覽

```
          ┌─────────────── 入口 (Entry points) ───────────────┐
          │  CLI (sinobee chat)   Web UI / REST / WebSocket     │
          └───────────────────────┬────────────────────────────┘
                                  ▼
                      ┌───────────────────────┐
                      │   Agent（agent/loop）  │  ← 單一核心，所有入口共用
                      │ prompt 組裝 · 工具迴圈 │
                      │ 上下文壓縮 · 事件回呼  │
                      └──┬─────────┬──────┬───┘
            ┌────────────┘         │      └─────────────┐
            ▼                      ▼                    ▼
   ┌─────────────────┐   ┌──────────────────┐   ┌────────────────────┐
   │ Providers        │   │ Tool Registry     │   │ Learning           │
   │ OpenAI 相容      │   │ shell · files     │   │ Memory（4 層）     │
   │ Anthropic        │   │ desktop · browser │   │ Skills（SKILL.md） │
   │ Gemini           │   │ web · memory      │   │ Reflector（反思）  │
   │ 備援鏈 / 重試    │   │ skills · approval │   └─────────┬──────────┘
   └─────────────────┘   └──────────────────┘             ▼
                                               SQLite（FTS5 + 向量）
                                               ~/.sinobee
```

### 目錄結構

```
sinobee/
├── config.py            設定載入（YAML + 環境變數）
├── types.py             內部統一訊息格式
├── providers/           模型轉接層
│   ├── base.py          Provider 介面、重試、備援鏈
│   ├── openai_compat.py OpenAI / OpenRouter / Groq / DeepSeek / xAI / Mistral / Ollama / vLLM / Gemini(相容端點)…
│   ├── anthropic.py     Anthropic Messages API（含 prompt caching）
│   └── __init__.py      預設供應商表與解析器
├── agent/
│   ├── loop.py          Agent 主迴圈
│   ├── prompt.py        System prompt 分層組裝
│   └── context.py       Token 估算與上下文壓縮
├── tools/
│   ├── registry.py      工具註冊、schema、分派、核准
│   ├── approval.py      危險指令偵測與核准政策
│   ├── shell.py files.py web.py
│   ├── desktop.py       滑鼠鍵盤截圖（xdotool / pyautogui 後端）
│   ├── browser.py       Playwright 瀏覽器（元素 ref 快照）
│   ├── memory_tools.py  skill_tools.py
├── memory/store.py      分層記憶 + 對話 / 情節儲存
├── skills/
│   ├── spec.py          Agent Skills 規格：寬鬆解析、嚴格驗證、正規化
│   ├── trust.py         信任級別、政策矩陣、內容雜湊、出處鎖檔、專案信任
│   ├── manager.py       多範圍探索、目錄、啟用、撰寫（版本化）、成效與生命週期
│   └── hub.py           安裝（hub / GitHub / URL / 本機）、匯出、發布、更新、撤銷
├── learning/reflector.py 任務後反思：萃取記憶、建立 / 修補技能
├── pricing.py           各家費率表與成本計算
├── accounting.py        用量歸屬（session / 用途）
├── secrets.py           金鑰儲存（secrets.json, 0600）
├── server/              FastAPI + WebSocket + 靜態 Web UI（含設定頁、用量頁）
└── cli.py
docker/                  supervisord、entrypoint（Dockerfile 在根目錄）
scripts/docker-run.sh    以指令帶入模型與金鑰啟動容器
sinobee/bundled_skills/  內建技能（首次啟動安裝到 ~/.sinobee/skills，信任級別 builtin）
```

## 3. Agent 主迴圈

```
run(user_input):
  1. recall   = memory.recall(user_input)           # 長期記憶 + 情節檢索（top-k）
  2. skills   = skills.relevant(user_input)          # 相關技能索引（top-n）
  3. messages = [system(穩定)] + history(只增不改) + tail(每次重建、不存檔：<context>回憶/技能/工作記憶/黑板</context>
                                                       + 使用中技能 + 驗收清單)
  4. loop（沒有固定步數上限；進度決定何時停，見第 22 節；單一 Agent 300 步為安全閥）:
       resp = provider.chat(messages, tools)
       若無 tool_calls → 品質關卡（驗收清單 → 成果檢查 → 完成檢查），沒過就退回修正，過了才結束
       for call in tool_calls:
           approval.check(call) → 執行 → 結果（文字或圖片）加入 messages
       若估計 token > 門檻 → context.compress()（摘要中段對話）
  5. 儲存對話；背景觸發 reflector（依「學習訊號」決定是否反思）
```

- **事件回呼**：每個步驟（模型輸出、工具呼叫、工具結果、核准請求）都會送出事件，CLI 顯示於終端、Web UI 經 WebSocket 即時呈現。
- **可中斷**：`agent.cancel()` 在下一個安全點停止。
- **多模態**：工具可回傳圖片（截圖），若模型支援視覺就直接附上；否則改附文字說明（例如瀏覽器的元素清單）。

## 4. 模型中立層（Providers）

- 內部訊息格式：`Message(role, content: str | [TextPart | ImagePart], tool_calls, tool_call_id)`，各 Provider 負責雙向轉換。
- **兩種傳輸**即可覆蓋主流：
  - `openai`（Chat Completions 相容）：OpenAI、Azure、OpenRouter、Groq、DeepSeek、Mistral、xAI、Together、Fireworks、Ollama、vLLM、LM Studio，以及 Gemini 的 OpenAI 相容端點。
  - `anthropic`（Messages API）：原生 tool use、圖片、prompt caching 斷點。
  - `ollama`（Ollama 原生 `/api/chat`、`/api/embed`）：本機架設模型；可逐次指定 `num_ctx`（OpenAI 相容端點會忽略而截斷在 4k），並回報 token 數。
- 設定以 `provider/model` 指定，例如 `anthropic/claude-sonnet-4-5`、`openai/gpt-4.1`、`ollama/qwen3.5:9b`；`base_url`、`api_key_env` 可覆寫。
- **備援鏈**：`model.fallbacks` 依序嘗試；429 / 5xx 自動指數退避重試。
- **輔助模型**：反思、壓縮可指定較便宜的 `auxiliary_model`，降低成本（Hermes 的背景審查成本問題）。

## 5. 記憶系統（相對 Hermes 的優化）

### Hermes 的作法與限制
Hermes 以 `MEMORY.md`（2,200 字元）與 `USER.md`（1,375 字元）注入 system prompt，加上 SQLite FTS5 的對話搜尋。官方文件自己列出的弱點：容量僅約 1,300 tokens、會話中寫入要到下個會話才看得到（凍結快照）、沒有讀取動作、`replace` 以子字串比對容易歧義、背景審查成本高、無語意檢索。

### SinoBee：四層記憶

| 層 | 內容 | 儲存 | 注入方式 |
|---|---|---|---|
| **L0 核心記憶** | 使用者輪廓、agent 身分與長期規則（少量、高重要度） | SQLite `memories`（tier=core）| 會話開始時放入 system prompt（穩定、可快取） |
| **L1 工作記憶** | 當前任務目標、計畫、已知事實、待辦 | 會話內 scratchpad | 每次呼叫模型時放在請求最尾端的 tail 訊息（不存進歷史）→ **會話內即時可見**，又不破壞 prefix cache（見第 16 節） |
| **L2 長期語意記憶** | 事實、偏好、教訓、環境知識；含重要度、信心、存取次數、時間 | SQLite + FTS5（BM25）＋ 可選向量 | 每輪依輸入**自動召回 top-k**，並提供 `memory_search` 主動查詢 |
| **L3 情節記憶** | 完整對話紀錄 + 每個會話結束時的 LLM 摘要 | SQLite + FTS5 | `session_search` 查詢；摘要參與自動召回 |

**關鍵改進**

1. **以 ID 編輯**：每筆記憶有短 ID（如 `m3f9a`），`memory_update/forget` 直接指定 ID，消除子字串歧義；更新採「取代鏈」（`superseded_by`），保留歷史可追溯、可回復。
2. **混合檢索**：分數 = `α·BM25 + β·cosine(embedding) + γ·重要度 + δ·新近度(半衰期) + ε·log(存取次數)`；沒設定 embedding 模型時自動退化為 BM25 + 中繼資料排序，零依賴也能運作。中文以字元 bigram 斷詞建立 FTS 索引，確保中文檢索有效。
3. **容量不受 prompt 大小限制**：核心層只放最重要的少數條目（預設 ≤ 1,500 字元），其餘存於長期層按需召回；容量可到數萬筆。
4. **自動整併（consolidation）**：反思時偵測近似重複（相似度 > 門檻）→ 合併；矛盾 → 新條目取代舊條目；長期未用且低重要度 → 降級封存；高頻使用 → 升級到核心層。
5. **寫入安全**：所有寫入通過掃描（prompt injection、憑證外洩、不可見 Unicode），可選 `memory.write_approval` 人工核准。
6. **學習訊號驅動反思**：不是固定每 N 輪，而是在「工具呼叫數 ≥ 門檻、發生錯誤後恢復、使用者糾正（偵測『不是』『錯了』『應該』等）、任務明確完成」時才觸發，節省成本。

### 記憶處理技能：任務 / 短期 / 長期 / 永久

內建技能 `memory-management`（`sinobee/bundled_skills/memory-management/`）負責四件事：

- 把任務重點萃取成記憶；
- 依需求壓縮記憶；
- 依指示新增記憶；
- 依指示移除記憶。

它靠的是下面的資料模型與工具。

**兩個獨立的軸**

原本的 `core` 同時代表「留多久」與「放在哪」，兩件事綁在一起。現在拆開：

| 軸 | 值 | 說明 |
|---|---|---|
| 保存期限 | 任務 → 短期 → 長期 → 永久 | 決定何時可以淡出或被移除 |
| 注入位置 | core（每次都在 prompt）／召回（依相關度） | 決定模型何時看得到 |

| 層 | 實作 | 壽命 | 自動流轉 |
|---|---|---|---|
| 任務記憶 | `working_memory`（goal / plan / done / facts / next）＋ 執行紀錄黑板 | 本任務 | 任務結束時由技能程序 A 萃取 ≤ 5 點 |
| 短期記憶 | `tier=short`、`expires_at`（`memory.short_ttl_days`，預設 14 天） | 約兩週 | 到期時被用過 ≥ 2 次或重要度 ≥ 0.7 → 轉長期；否則封存 |
| 長期記憶 | `tier=long` | 數月 | 低重要度且 90 天未用 → 封存；高頻偏好／教訓 → 升到 core |
| 永久記憶 | `pinned=1`（可在 core 或 long） | 永久 | 不降級、不過期、不封存、不被合併掉；只有使用者明確指示才能修改或移除 |

**流程**

```mermaid
flowchart LR
  T[任務進行中<br/>working_memory] -->|任務結束：萃取 ≤5 點<br/>先搜尋去重／更正| D{選層}
  D -->|專案進度、決策| S[短期 short<br/>14 天到期]
  D -->|偏好、教訓、環境| L[長期 long]
  D -->|使用者說「永久記住」| P[永久 pinned]
  S -->|到期且有用| L
  S -->|到期且沒用| A[封存 archive]
  L -->|久未使用且不重要| A
  L -->|高頻偏好| C[core<br/>常駐 prompt]
  L & S -->|memory_compress<br/>分群 → 合併 → 防漏檢查| L
  U[使用者：忘掉 X] -->|預覽 → 確認| A
```

**工具**

| 工具 | 用途 | 防呆 |
|---|---|---|
| `memory_save(tier=short\|long\|core\|permanent, ttl_days)` | 新增 | 近似重複自動合併；短期事實再次出現會延長或升級 |
| `memory_update(id, tier, pinned, …)` | 更正或改層 | 改動永久記憶需 `confirm_permanent` |
| `memory_forget(query\|ids\|id, tier, kind, tag)` | 移除 | 用 query 時只回傳預覽；需 `confirm=true`；永久記憶會跳過 |
| `memory_compress(query, tier, kind, tag, target_chars, dry_run)` | 壓縮 | 預設只預覽；合併後遺失數字、路徑或網址就拒絕；原文保留在版本歷史 |
| `memory_stats` | 各層容量、core 用量、即將到期的短期記憶 | — |

UI 記憶分頁可以：

- 依層篩選；
- 看 core 用量與到期日；
- 一鍵設為永久或取消永久；
- 封存記憶。

CLI 支援 `sinobee memory stats | pin | unpin | compress [--apply]`。

**壓縮演算法**

1. 篩選要壓縮的記憶。
2. 依「同 kind ＋ 詞彙 Jaccard ≥ 0.2（同標籤時減半）」做貪婪分群。
3. 每群交給輔助模型合併成一句。
4. 防漏檢查：來源中的數字、路徑、網址或程式碼片段必須全部保留，且合併結果要比原文短。
5. 檢查不過時改用抽取式合併：最詳細的優先，詞彙被覆蓋 ≥ 80% 的句子刪除。抽取式不改寫，所以不會捏造內容。
6. 套用：新記憶以 `superseded_by` 取代來源。

若仍超過 `target_chars`，只「建議」低價值的候選，不會自動刪除。

#### 架構優缺點分析

**原架構（L0–L3）的優點**

- 以 ID 編輯、保留取代鏈：可追溯、可回復。
- 混合檢索：不需要 embedding 也能運作，中文 bigram 檢索有效。
- 核心層有容量上限：prompt 穩定、可快取。
- 寫入有掃描：防注入與憑證外洩。

**原架構的缺點**

- **期限與注入綁在一起**：
  - 想永久保存的東西若不夠重要，會被 core 上限擠到 long；
  - 進了 long 之後，又可能因 90 天未用被封存。
  - 沒有「永久」的保證。
- **缺少短期層**：
  - 反思把專案細節直接寫進 long，長期記憶逐漸被一次性的專案狀態污染；
  - 召回的雜訊也隨之上升。
- **只能精確去重**：語意重複會累積，容量與召回品質一起變差；沒有壓縮工具。
- **移除只能逐筆按 ID**：使用者說「忘掉 A 專案」時，模型得自己搜尋再逐筆刪除，容易漏刪或誤刪；沒有預覽。
- **反思可以改寫任何記憶**：使用者明確交代的內容可能被自動「更正」掉。

**新架構的優點**

- 保存期限與注入位置分開：永久記憶有明確保證，core 仍維持小而穩定。
- 短期層加上「用過才留」的規則，讓長期記憶只留下被驗證過有用的事實，降低召回雜訊。
- 壓縮可預覽、有防漏檢查、可回溯，降低把關鍵細節合併掉的風險。
- 批次移除先預覽再確認；永久記憶需要使用者二次確認。
- 反思不能建立或修改永久記憶。
- 子代理不能寫入、刪除或壓縮記憶。

**新架構的缺點與風險（以及對策）**

| 風險 | 說明 | 對策 |
|---|---|---|
| 永久記憶無上限 | 釘選在 core 的永久記憶可能超過 core 上限，佔用 prompt | `memory_stats` 標示 `over_limit`，UI 顯示警告；建議只把少數規則同時設為 core |
| 短期記憶誤判 | 重要但兩週內沒被用到的事實會被封存 | 封存不是刪除：仍可用 `include_archive` 找回；重要度 ≥ 0.7 的不會被封存 |
| 壓縮仰賴輔助模型 | 弱模型合併品質差 | 防漏檢查 ＋ 抽取式備援；預設只預覽 |
| 分群是詞彙式 | 同義但用詞不同的記憶不會被分在一起 | 有 embedding 時可改用向量分群（後續路線） |
| 任務記憶不跨會話 | `working_memory` 屬於單一會話 | 長任務的進度存在執行紀錄與黑板；任務結束時萃取到短期記憶 |
| 整併時機 | consolidate 只在反思後或手動執行，到期的短期記憶不會即時處理 | 已解決：見下節「自動記憶維護」 |

### 自動記憶維護（不靠模型自己記得去做）

需求有三點：

- SinoBee 要能自行選用記憶技能；
- 執行任務時與定期排程中要自動整理記憶；
- 對話內容接近模型上限時要壓縮記憶。

**原始構想的優缺點**

如果全部做成「讓模型在適當時機自己呼叫記憶技能」，好處是實作最少、規則集中在技能文件。缺點是：

| 問題 | 說明 |
|---|---|
| 依賴模型自律 | 弱模型常不會主動 `skill_view`；整理記憶這種維運工作若靠模型判斷，常常就是不會發生 |
| 和任務搶資源 | 任務中途整理記憶，會拉長回應時間、多花 token，還可能把當前任務剛寫入或剛召回的記憶合併掉 |
| 自動壓縮與「先預覽」衝突 | 手動壓縮預設只預覽，但無人值守的排程沒有人可以確認 |
| 「壓縮記憶」的範圍不清 | 上下文裡佔空間的有三種：對話歷史、注入的記憶（核心＋召回＋工作記憶）、技能內容。原本只會摘要對話歷史，被摘要掉的細節之後再也找不回來 |
| 工作記憶不即時 | 舊版每輪只組一次 `<context>`，任務中途更新的 `working_memory` 要到下一輪才看得到 |

**改良後的設計：確定性程式碼負責維運，技能負責對話中的判斷**

| 時機 | 機制 | 防呆 |
|---|---|---|
| 使用者提到記憶 | `skills.suggest()`：請求中出現技能觸發詞時，把技能名稱與快速上手放進 `<suggested_skill>`，模型不用自己找。只建議內建、已驗證或本機技能；英文觸發詞要整個字相符 | 觸發詞避開「記憶體」「memory leak」等誤判 |
| 每輪結束後 | `maybe_maintain()` 在背景執行，最多每 `memory.maintain_interval_hours`（6 小時）一次：先 consolidate，記憶量超過 `memory.auto_compress_chars` 時再合併相關記憶 | 一次只跑一個；預算用盡時只做抽取式合併；不碰永久、核心與 1 天內改過的記憶；不刪除；合併可用 `memory_restore` 還原 |
| 每天定時 | 首次啟動時在工作分頁建立「記憶整理」工作（流程 `memory-maintenance`，動作 `memory.maintain`，預設 03:17），執行結果顯示在執行紀錄中 | 只建立一次：使用者刪除或修改後不會被加回 |
| 上下文接近上限（預估 > 80%） | 依序處理：① 召回的記憶只留前 2 則 ② 工作記憶超過 `working_char_limit`/2 時壓縮（保留所有欄位，輔助模型失敗時保留第一行與最新內容）③ 仍不夠才摘要較早的對話 ④ 被摘要的部分由輔助模型萃取最多 5 個重點，存成短期記憶（標籤 `context-flush`）| 只會寫短期或長期記憶，不會寫核心或永久；寫入前一樣經過掃描與 `write_approval` |
| 每一步 | `<context>` 在每次呼叫模型前重組，任務中途寫入的工作記憶立刻可見 | 召回與技能索引每輪只算一次，重組成本很低 |

```mermaid
flowchart TB
  subgraph 任務進行中
    S[每一步] --> E{上下文 > 80%?}
    E -- 否 --> M[呼叫模型]
    E -- 是 --> D1[召回記憶只留前 2 則] --> D2[壓縮工作記憶] --> E2{仍 > 80%?}
    E2 -- 否 --> M
    E2 -- 是 --> C[摘要較早對話] --> F[背景：重點存入短期記憶] --> M
  end
  M --> T[回合結束] --> R[反思學習（有學習訊號時）]
  T --> MM{距上次整理 ≥ 6 小時?}
  MM -- 是 --> MT[背景 maintain：consolidate + 必要時合併]
  CRON[每日 03:17 記憶整理工作] --> MT
  U[使用者：「整理記憶」] --> SK[suggested_skill → memory-management] --> TOOL[memory_maintain / compress / forget]
```

**新設計仍有的取捨**

- 自動合併不經使用者預覽。安全網有三層：防漏檢查、版本歷史、`memory_restore`，而且合併前後的內容都記在 `maintain.last_report`。
- 從摘要萃取記憶會多一次輔助模型呼叫。只在真的發生上下文壓縮時才做，而且在背景執行。
- 觸發詞建議是詞彙比對，可能漏判。漏判時仍有技能索引，模型可以自己選。

## 6. 技能系統（Agent Skills 標準 + 信任機制）

完整規格見 **[docs/SKILL_HUB.md](SKILL_HUB.md)**，重點如下：

- **格式**：完全遵循 [agentskills.io 規格](https://agentskills.io/specification)，frontmatter 只用 `name`、`description`、`license`、`compatibility`、`allowed-tools`、`metadata`；SinoBee 擴充（版本、標籤、觸發詞、需要的工具、平台）放在 `metadata` 的 `sinobee-*` 字串鍵。寬鬆讀取其他用戶端的技能並自動正規化，寫入一律嚴格驗證（與官方 `skills-ref` 一致）。
- **路徑**：專案 `<project>/.sinobee/skills/`（+ `.agents/skills/`），全域 / 共享 `~/.sinobee/skills/`（+ `~/.agents/skills/`）；專案優先，但未信任的專案技能不會遮蔽可用的全域技能。
- **信任級別**：`builtin`／`verified`／`local`／`project`／`agent`／`community`／`untrusted`／`blocked`，由來源與內容雜湊判定（不讀技能自述），決定是否可見、腳本是否每次需核准、是否採用 `allowed-tools` 預先核准、agent 能否直接修改。出處記錄在 `~/.sinobee/skills/.sinobee-lock.json`，專案信任記錄在專案外的 `~/.sinobee/trusted_projects.json`。
- **漸進揭露**：`<available_skills>` 目錄（name + description + 信任）→ `skill_view` 以 `<skill_content>` 包裝本文與資源清單 → 按需讀取附件；`/skill-name` 可直接啟用；啟用中的技能不會被上下文壓縮摘要掉。
- **自適應生成**：被動（使用者要求、`/learn`、GUI 從對話學習）與主動（缺口、重複任務、失敗修補）；新技能為 `agent` 信任 + experimental，成功使用後自動轉正，失敗多則標記待修正；`auto_create` 可設 auto / suggest / off。
- **Hub**：`sinobee skills install hub:發布者/名稱`、GitHub、URL、本機資料夾；匯出、發布、更新、撤銷同步；ed25519 簽章 + 審核 → `verified`。

| 項目 | Hermes | SinoBee |
|---|---|---|
| 格式 | agentskills.io 相容，另有自訂頂層欄位 | 嚴格符合規格，擴充全放 metadata，通過 `skills-ref validate` |
| 索引 | 全部清單放 system prompt | ≤ 20 個全列；更多時依當輪輸入檢索 top-n，放在訊息尾端 |
| 信任 | builtin / official / trusted / community（Hub 來源） | 8 級，涵蓋專案、agent 生成、修改偵測、撤銷，並對應執行政策 |
| 成效 | 無 | 使用次數、成功率、狀態（experimental → active → 待修正） |
| 版本 | 可修補，無版本史 | 每次寫入保存版本，可回復；刪除可還原 |
| 建立時機 | 固定間隔 nudge | 學習訊號（缺口、重複、失敗、使用者要求）觸發 |

## 7. 電腦操作工具

| Toolset | 工具 | 說明 |
|---|---|---|
| `shell` | `shell` | 執行指令（逾時、輸出截斷、工作目錄、危險指令核准） |
| `files` | `read_file` `write_file` `edit_file` `list_dir` `search_files` | 精確字串取代編輯，避免整檔重寫 |
| `desktop` | `screenshot` `mouse`（move/click/double/right/drag/scroll）`keyboard`（type/key/hotkey）`screen_info` | Linux/Docker 用 xdotool，macOS/Windows 用 pyautogui；截圖縮放後回傳，座標自動換算回實際解析度 |
| `browser` | `browser`（navigate/snapshot/click/type/press/scroll/select/back/forward/tabs/screenshot/get_text/evaluate/wait） | Playwright 持久化設定檔（保留登入）；`snapshot` 為可互動元素標上 `[ref]`，模型以 ref 操作，比座標點擊穩定且省 token |
| `web` | `web_search` `web_fetch` | 搜尋（DuckDuckGo 免金鑰，或 Brave/Tavily）與網頁轉文字 |
| `usage` | `usage_stats` | 查詢自身 token 用量與花費 |
| `memory` | `memory_save` `memory_search` `memory_update` `memory_forget` `session_search` `working_memory` | |
| `skills` | `skill_search` `skill_view` `skill_create` `skill_patch` `skill_fork` `skill_write_file` `skill_run` `skill_feedback` `skill_rollback` | 刪除、核准、信任專案只能由使用者在 GUI / CLI 操作 |

**桌面與瀏覽器的分工**：網頁任務優先用 `browser`（DOM 層級、可靠）；遇到非網頁應用、驗證碼後的人工步驟、或需要像素層級互動時用 `desktop`。在 Docker 中兩者共用同一個 Xvfb 顯示器，瀏覽器以有頭模式運行，使用者可透過 noVNC 看到 agent 的每個動作。

## 8. 授權與安全

- **Toolset 授權**：`permissions.toolsets` 決定開放哪些能力（例如只開 `browser`、`web`）。
- **核准模式** `permissions.approval`：
  - `auto`：全部自動（建議僅用於 Docker 沙箱）
  - `confirm`：危險操作（`rm -rf`、`sudo`、磁碟格式化、對外傳送憑證、`curl | sh`…）需使用者在 CLI / Web UI 核准
  - `readonly`：禁止寫入類工具
- **路徑限制** `permissions.allowed_paths`：檔案工具僅能存取白名單路徑。
- **Docker 隔離**：非 root 使用者、資料集中於 `~/.sinobee` 與 `~/workspace` 兩個 volume、可用 `--network` 限制外連。
- **主機模式**：若要控制實體電腦，以 `pip install` 原生執行；macOS 需在「隱私權與安全性」授予終端機「輔助使用」與「螢幕錄製」權限。

## 9. 部署（Docker）

```
supervisord
 ├── Xvfb :99 (1440x900)        虛擬顯示器
 ├── fluxbox                    視窗管理器
 ├── x11vnc → websockify/noVNC  :6080 觀看/接手桌面
 └── sinobee serve              :10024 Web UI + API
volumes: ~/.sinobee → config.yaml、settings.json、secrets.json、sinobee.db、skills/、browser-profile/
         ~/workspace → 專案（<project>/.sinobee/skills）
```

`docker compose up -d` 後開啟 `http://localhost:10024`，左側對話、右側即時桌面。

## 10. 設定與金鑰管理

**優先序**（後者覆蓋前者）：內建預設 → `config.yaml`（手動編輯，可省略）→ `settings.json`（Web UI 設定頁寫入）→ 啟動環境變數（`docker run -e`）。
啟動時帶入的值會標記為「鎖定」，GUI 顯示 🔒 且唯讀，避免 GUI 改了、重啟後又被環境變數蓋回的困惑。

**金鑰解析**：啟動參數 `<PROVIDER>_API_KEY` → GUI / `sinobee keys set` 存入的 `secrets.json`（權限 600）→ `config.yaml`（不建議）。API 只回傳遮罩值；儲存後 `Runtime.reload()` 熱重建模型鏈，不需重啟。

| 入口 | 方式 |
|---|---|
| Web UI | 設定頁「模型」：上方依角色（主要／輔助／升級／Embedding／備援）從所有已連線供應商的模型挑選；下方是供應商卡片，金鑰按「連線」即時儲存並驗證；Ollama 卡片展開本機模型管理。另有核准模式與能力、預算與幣別、費率覆寫 |
| Docker 啟動 | `scripts/docker-run.sh -m <model> -k <provider>[=key]`（未給值時隱藏輸入，以 `-e VAR` 傳遞避免出現在 `ps`），或 `docker run -e` / `VAR=... docker compose up` |
| 執行中容器 | `docker exec -it sinobee sinobee keys set <provider>` |

**模型目錄（`providers/catalog.py`）**

舊版的設定頁有兩個問題：

- 「雲端 API／本機模型」是二選一的模式。主要模型是本機模型時，雲端的金鑰欄與模型欄整個被隱藏；儲存後畫面又依主要模型切回本機模式。
- 「載入可用模型」只查主要模型所屬的供應商（ollama）。

所以填了 Gemini 金鑰之後，Gemini 的模型永遠不會出現。

新版改為單一目錄：

- **列出可用的供應商**：`GET /api/models/catalog` 同時查詢所有可用的供應商，也就是已有金鑰的、本機服務、以及自訂端點。
- **取得模型清單**：
  - 呼叫各家的 `/models`，逾時 8 秒，結果快取 10 分鐘；
  - 依名稱分成 chat、embedding 與 other（TTS、圖像、影片等無法執行 agent 的模型）；
  - 推薦模型排在最前面；
  - 查詢失敗時退回內建的推薦清單。
- **設定金鑰（`PUT /api/providers/{name}/key`）**：
  - 立即寫入 `secrets.json`；
  - 熱重建模型鏈，再用列出模型的方式驗證金鑰；
  - 401、403 或 "API key not valid" 會顯示為「金鑰無效」；
  - 只有在執行模型無法使用時，才建議改用新連線供應商的推薦模型。
- **儲存檢查**：`PUT /api/settings` 會回傳 `warnings`，列出哪個角色選了還沒設定金鑰的供應商。
- **混用**：本機與雲端模型可以任意搭配，例如執行模型用本機，思維模型或備援模型用 Gemini。
- **模型角色**：思維模型 `model.thinking`、執行模型 `model.default`、審查模型 `model.reviewer`、輔助模型 `model.auxiliary`、Embedding；分工見第 20 節。

## 11. Token 用量與成本

```
FallbackChain.chat ──► Runtime._record(provider, usage)
                         ├─ pricing.price_for(provider, model, 覆寫表) → 單價
                         ├─ pricing.cost_usd(輸入, 輸出, 快取讀, 快取寫, 長上下文分級)
                         ├─ usage 表（ts, session, 用途, 模型, tokens, cost）
                         └─ UsageScope（contextvar：本輪/本次反思的累計）
```

- **完整記帳**：主任務、反思（背景）、上下文壓縮、連線測試、Embedding 都記錄，並以 `purpose` 區分；Anthropic 的 cache write / read、OpenAI 的 cached tokens 分開計價。
- **費率表**：`sinobee/pricing.py` 內建 OpenAI、Anthropic、Gemini、DeepSeek 參考價（含長上下文分級），以模型名稱最長前綴比對（相容日期後綴與 OpenRouter 命名）；本機供應商計 0；GUI 可覆寫或補上自訂模型，未知模型會標示「未設定費率」。
- **預算**：每日／每月上限，達 80% 送出提醒事件，`enforce_budget` 開啟時達上限即停止呼叫模型。
- **介面**：Web UI「用量」頁（KPI、每日花費圖、依模型／用途／對話明細、預算進度、費率表）；每輪結束顯示本輪與對話累計花費；`usage_stats` 工具讓 agent 回答自身花費；CLI `sinobee usage`。

## 12. 對話介面與回饋學習

**視覺**：深藍（#060d1b）→ 青綠（#2fb3a0）→ 薄荷（#7fe8b6）的品牌色，側欄與首頁以 canvas 繪製網點（halftone）圖樣；支援深色（預設）／淺色切換。

**Markdown 渲染**：`marked`（GFM）→ `DOMPurify`（禁止 script/style/iframe/表單與行內樣式）→ 自訂渲染器：圖片、影片／音訊（依副檔名）、附件卡、mermaid 區塊（原始碼以 URI 編碼存於 `data-src`，避免被消毒器的 `-->` 規則移除；mermaid 以 `securityLevel: strict` 延遲載入）。三個函式庫皆打包在 `static/vendor/`，離線可用。

**檔案存取**：`GET /api/files?path=` 只允許工作區、專案目錄與 `allowed_paths`，拒絕 SinoBee 家目錄（工作區除外）與 `secrets.json`、`settings.json`、`config.yaml`、資料庫等；HTML/SVG 一律以下載回應並加 `nosniff`。`POST /api/upload` 存至 `workspace/uploads/YYYYMMDD/`（200 MB 上限、檔名清理、自動去重），使用者訊息中的圖片同時以 `ImagePart` 傳給模型。

**任務狀態**：每輪的事件廣播給所有連線；`turn_end` 帶 `status ∈ {done, error, incomplete, stopped, blocked}`、步數、耗時、用量與 `turn_id`；錯誤經 `explain_error` 轉為可讀說明與建議。

**回饋 → 學習**：

```
👍/👎 + 標籤 + 留言 ──► POST /api/feedback ──► feedback 表（關聯 turns：模型、技能、步數、花費）
                                  └─► learn_from_feedback（背景）
                                        ├─ 以使用者評價為準反思該輪（任務、工具軌跡、回覆）
                                        ├─ 記憶：偏好／教訓（supersede 舊記憶）
                                        ├─ 技能：依評價調整成功率；僅在評價改變時重算
                                        └─ 修補／建立技能（受信任級別與 auto_create 約束）
```

只有狀態為 `done` 的輪次會自動反思；失敗輪次只有在使用者給回饋時才學習。`GET /api/feedback/stats` 提供滿意度與依模型／技能／原因的統計，`/api/feedback/export` 匯出 JSONL 資料集。

## 13. 本機架設模型

```
sinobee/local/
  hardware.py   CPU / RAM（含 cgroup 上限）/ GPU（nvidia-smi、rocm-smi、Apple Silicon 統一記憶體）/ 磁碟
                → budget {fast_gb: 全速可用（VRAM 或約 2/3～3/4 統一記憶體）, total_gb: 含溢出到系統記憶體}
                → auto_num_ctx（16K / 32K / 64K）
  catalog.py    精選模型（僅收支援 tools 的 Ollama 標籤；大小為 2026-10-13 查核的 ≈Q4 下載量）
                fit(): need = 權重×1.1 + KV cache(參數量, num_ctx) + 0.6GB → great | ok | slow | no；recommend()
  ollama.py     服務探索（設定 > SINOBEE_OLLAMA_URL > OLLAMA_HOST > 127.0.0.1 > ollama（compose）> host.docker.internal）
                LocalRuntime：啟停受管理的 `ollama serve`、背景下載（串流進度）、載入／卸載
  __init__.py   status()：合併硬體、目錄、/api/tags、/api/ps、/api/show（capabilities）與下載工作
```

**狀態機**：`absent → pulling(進度%) → downloaded → loading → running(GPU%、佔用、ctx、釋放時間)`，另有 `error`。
事件 `local_progress`（下載進度，0.5 秒節流）與 `local_changed` 透過 WebSocket 廣播，設定頁與左下角狀態即時更新。

**選用**：`POST /api/local/use` 把 `model.default`（或 `embedding`）設為 `ollama/<tag>` 並預先載入；`needs_key("ollama")` 為 false，價格歸類為 free。
**上下文保護**：選用本機模型時，agent 的壓縮門檻改為 `num_ctx − num_ctx/4 − 4000`（扣掉回答與工具 schema），輸出上限 `num_ctx/4`，避免本地模型靜默截斷系統提示。
**Docker**：容器看不到主機 GPU，`scripts/host-hardware.sh` 產生 `SINOBEE_HOST_HARDWARE`；服務在 host.docker.internal（或 Linux 上的 Ollama 容器）時以主機硬體評估。compose 提供 `--profile local-llm` 的 Ollama 服務與 `docker-compose.gpu.yml`。

| API | 用途 |
|---|---|
| `GET /api/local/status` | 服務、硬體、預算、模型清單與狀態、推薦 |
| `POST /api/local/runtime/start` / `stop` | 啟停 SinoBee 管理的 `ollama serve`（僅停止自己啟動的） |
| `POST /api/local/pull`、`DELETE /api/local/pull/{name}` | 下載／取消 |
| `POST /api/local/load` / `unload` / `delete` | 載入記憶體／釋放／刪除檔案（使用中的模型不可刪） |
| `POST /api/local/use` | 設為主要或 Embedding 模型 |
| `PUT /api/local/settings` | 服務位址、num_ctx、keep_alive、記憶體覆寫 |

## 14. 背景工作：子代理、圖流程、黑板與排程

SinoBee 原本是一個代理的工具迴圈（agentic harness）。這一版在它外面加上三種結構，彼此可以組合：

| 結構 | 模組 | 說明 |
|---|---|---|
| 子代理 | `work/agents.py`、工具 `delegate` | 子代理是同一個 `Agent` 類別的新實例，有自己的 session。它用角色的工具白名單、角色提示詞和步數上限執行，深度固定 1。子代理拿不到 `CHILD_DENY` 裡的工具：再委派、排程、寫記憶與技能。並行數由 `work.max_parallel_subagents` 限制，預設 3。 |
| 圖流程 | `work/graph.py`、`work/workflows/*.yaml` | 流程是 YAML 定義的 DAG，節點型態有四種：`agent`（子代理）、`map`（對清單並行派子代理）、`action`（內建動作，如 `db.health_collect`、`db.migrate`、`report.write`）、`approval`（等你核准）。<br>節點欄位：<br>• `needs`：相依的前置節點；<br>• `when`：執行條件，用安全的運算式求值器判斷；<br>• `allow_skipped`：前置節點被略過時仍執行；<br>• `output: json`：輸出解析成 JSON。<br>樣板語法 `{{nodes.x.output.y}}`。<br>執行方式：同一層的節點並行執行；前置節點失敗或略過時，後續節點也略過；每個節點的狀態都存下來，中斷後從已完成的節點之後繼續。 |
| 黑板 | `work/store.py` 的 `board` 表、工具 `board_write`／`board_read` | 黑板有三種範圍：`session:<id>`、`run:<id>`、`job:<id>`。<br>條目種類：goal、plan、finding、decision、issue、question、artifact、note。同一 kind 加同一 key 的條目會被取代。<br>每一輪都把黑板摘要放進 `<context>`，主代理、子代理和同一工作的後續執行都看得到。 |
| 排程 | `work/schedule.py`、`work/hub.py` | 排程型態：manual、once、interval（最短 60 秒）、daily／weekly、cron（本地時間）。<br>`WorkHub` 在 `sinobee serve` 內每 10 秒檢查一次到期的工作，同時執行數受 `work.max_parallel_runs` 限制，超過的排隊。<br>持續性工作靠兩樣東西接續：`job:<id>` 黑板，以及上一次執行的摘要。 |

無人在場時的核准：背景工作的 `approve` 會在 `approvals` 表新增一筆，再等待一個 asyncio Future，執行狀態變成 `waiting`。Web UI 的「工作」頁或 `/api/work/approvals/{id}` 決定後，工作才繼續。伺服器重啟時，未決定的核准會作廢，被中斷的流程會自動續跑。

「過去／現在／接下來」由 `WorkHub.overview()` 組成：

- 過去：執行紀錄加上對話回合；
- 現在：背景執行加上進行中的對話；
- 接下來：各工作的下幾次執行時間，加上黑板上未完成的 plan。

資料存在 `~/.sinobee/work.db`，API 在 `/api/work/*`，命令列是 `sinobee work`。

## 15. 任務執行的可靠度與互動

### 從失敗案例看到的缺陷
實際案例是「建立古裝武打片」：本機模型跑了 31 步、約 50 分鐘仍沒有渲染，之後又把同一個錯誤呼叫重複了 5 次，最後卻宣稱「資料 100% 完成」。逐項對照架構，缺陷如下：

| # | 缺陷 | 症狀 | 處理 |
|---|---|---|---|
| 1 | 工具失敗沒有一致的訊號：`ERROR:` 字串、腳本非零結束碼都被當成成功 | 介面顯示 ✓，迴圈不知道失敗了 | `_normalise` 把 `ERROR` 開頭的結果標成錯誤；`skill_run` 非零結束碼視為錯誤 |
| 2 | 沒有「無進展」偵測 | 同一個失敗呼叫重複多次 | 已失敗的相同呼叫直接拒絕並附上原錯誤；拒絕 3 次或連續 6 次錯誤就停止，狀態設為 incomplete |
| 3 | 完成判定只看「模型不再呼叫工具」 | 失敗卻顯示「✓ 完成」，背景學習還把技能評成低分 | 最後一步是錯誤就標示 incomplete，也不進行學習 |
| 4 | 技能只是文字，呼叫方式要模型自己組出 script 與 args | 把 project.json 當成腳本，或自行發明指令 | 技能可宣告 `sinobee-entry` 與 `sinobee-quickstart`；最自然的呼叫 `skill_run(name, args)` 或把檔案放在 `script` 都會改由入口腳本執行 |
| 5 | 長流程全靠模型記住下一步，壓縮上下文後技能說明可能遺失 | 迷路、重做、停下來問 | 使用中技能的 quick start 每一步都固定附在請求裡；影片技能改由 autopilot（`run.py`）推進機械步驟 |
| 6 | 對話執行中是封閉的 | 不能插話；中間產物埋在工具輸出文字裡 | 執行中可插話（steer），或「停止並改做」（interrupt）；產生的檔案以 `artifact` 事件即時顯示，重開對話也還在（`/api/sessions/{id}/artifacts`） |
| 7 | 對話任務與背景工作（WorkHub run）是兩套系統 | 對話沒有步驟、產出、檢查點與續跑；圖流程只用在背景工作 | 已實作：每輪對話也是一筆 run；技能可宣告固定流程（見下方） |
| 8 | 模型能力和任務難度沒有對應 | 小模型被派去跑 30 步以上的多階段任務 | 已實作：完成檢查與模型升級（見下方） |

### 執行中互動（已實作）
- **補充指示**：任務執行中送出的訊息透過 WebSocket `mode: steer` 呼叫 `Agent.steer()`，放進 inbox。
  - 下一次呼叫模型前，訊息以「[使用者在執行中補充指示]」寫入歷史，並發出 `steer_applied` 事件。
  - 若任務剛好結束，inbox 裡剩下的訊息會自動成為下一輪。
- **停止並改做**：`mode: interrupt` 會取消目前任務（必要時強制），等它結束後改做新的要求。
- **中間產物**：每次工具呼叫後，`agent/artifacts.py` 從工具參數與輸出找出「這次任務產生、存在、而且允許顯示」的檔案，判斷規則與 `/api/files` 共用 `fileaccess.py`。
  - 純讀取類工具不算產出；本次任務之前就存在、只是被提到的檔案也不算。
  - 腳本輸出中的相對路徑，會以參數裡的資料夾為基準解析。
  - 介面在對應步驟下方顯示縮圖、播放器或檔案卡；`turn_end` 再附上「本次產出」清單。

### 架構決定：漸進統一，不整套替換
我不換成外部框架。對話這條路徑改為使用既有的 run store 與圖流程引擎，並加上完成驗證：

**1. 每輪對話都是一筆 run**（`work/chatrun.py`）
- `kind=chat`、`trigger=chat`，與背景工作共用 `work.db`。
- 記錄的內容：
  - 要求本身；
  - 每個工具步驟：成功或失敗、耗時、錯誤摘要；
  - 產出檔案，同時寫進 `run:<id>` 黑板；
  - 執行中補充、完成檢查與模型升級等事件；
  - 最後的狀態、摘要與花費。
- 「工作」頁的現在與過去因此同時涵蓋對話、背景工作與流程。對話 run 的詳細頁會列出步驟、補充、產出和檢查紀錄，並可前往該對話。
- 被中斷或未完成的對話 run 可以「續跑」：在原對話送出一則「從中斷處接著做」的訊息（`WorkHub.chat_resumer`）。對話歷史本身就是逐步保存的檢查點，所以不會重做已完成的步驟。從「工作」頁停止對話 run，會透過 `chat_canceller` 停掉那一輪。

**2. 技能可以宣告固定流程**（技能 metadata 加上 `sinobee-workflow: <name>`，檔案放在技能內的 `workflows/<name>.yaml`）
- `WorkHub.workflows()` 會把這類流程與內建流程一起列出。只有能執行腳本的技能會被列出，未信任或被封鎖的技能不會。
- 技能的 quick start 會直接告訴模型：優先呼叫 `run_workflow(name, inputs, wait=false)`。
- 影片製作（`video-production`）的節點如下：

  | 節點 | 類型 | 做什麼 |
  |---|---|---|
  | `plan` | action `skill.plan` | 企劃子代理（`planner` 角色）依範例格式產生 plan.json。技能入口腳本檢查內容，缺什麼就把清單交回去補，最多 3 輪。只有這一步需要 LLM。 |
  | `drafts` | action `skill.autopilot` | 執行 `run.py --until final`：配音、定裝照、草稿渲染、品管與自動重拍，最後輸出 720p 草稿預覽 `out/draft.mp4`。 |
  | `confirm_final` | approval（`on_reject: continue`） | 顯示段數、秒數、預覽路徑與預估 GPU 分鐘數，請使用者核准正式渲染。 |
  | `final` 或 `deliver_draft` | action | 核准就做正式渲染與剪接；拒絕就以草稿品質交付。 |

- 渲染伺服器連不上時，autopilot 會等待（最多 30 分鐘）而不是直接失敗。產出的影片、預覽與品管圖都會記成 run 的 artifact。
- **素材包（production kit）**：
  - **匯出**：`scripts/kit.py export` 在完成時由 `run.py` 自動執行，產生 `out/<片名>-kit.zip`。內容：
    - 給人看的文件：劇本、人物、場景、風格、分鏡表等 Markdown；
    - 給 SinoBee 再用的檔案：`bible.json`（可直接重用的企劃與 seeds），以及含 sha256 的 `kit.json`；
    - 素材檔：定裝照、場景母版、首尾幀、語音、配樂與 LUT。
  - **匯入**：流程輸入 `kit`（新的 `file` 輸入型別，可直接上傳）與 `kit_mode`（series／remake）。`skill.plan` 會先執行 `kit.py import`，再把匯入後的企劃交給企劃子代理，作為新企劃的起點。
  - **鎖定**：每次合併 plan.json 後，`kit.py lock` 依 `kit.lock.json` 還原以下欄位：
    - 角色的身分、側寫、聲線與定裝照；
    - 場景描述與母版；
    - 整體風格；
    - remake 時另加 render seeds。

    改了 id 但同名的角色會被改回素材包的 id，新增的角色與場景不受影響。
  - **安全檢查**：匯入時拒絕以下素材包：
    - 路徑跳脫（zip slip）；
    - 非預期的檔案類型；
    - 超過 4 GB 或 2000 個檔案；
    - 校驗碼不符。
- approval 節點新增 `on_reject: continue`：拒絕時輸出 `{approved: false}`、改走另一條分支，而不是讓整個流程失敗。

**3. 完成檢查與模型升級**（`agent.verify`、`model.escalate`；模型升級已由第 20 節的思維／執行分工取代）
- **完成檢查**：使用了 `agent.verify_min_tool_calls`（預設 3）個以上工具的回合，在回報完成前，會由輔助模型依證據判斷是否真的完成。證據包括要求、執行中補充、工具步驟與結果、產出檔案與最後回答。
  - 沒完成時，加入一則「[自動完成檢查]」訊息讓 agent 再做一輪。
  - 第二次仍沒完成，狀態就標成 incomplete，並寫明缺什麼。
  - 檢查本身失敗不會阻擋回答。
- **模型升級**（舊設計，2026-10-19 起移除，見第 20 節）：只要設定了 `model.escalate`（或 `SINOBEE_ESCALATE_MODEL`），以下任一情況會讓這一輪剩下的步驟改用較強的模型：
  - 連續工具錯誤達 `agent.escalate_after_errors`（預設 3）；
  - 重複呼叫已失敗的操作；
  - 步驟超過 `agent.escalate_after_steps`（預設 25）；
  - 完成檢查未通過；
  - 使用了標示 `sinobee-complexity: high` 的技能。

  每輪最多升級一次，事件會顯示在對話與 run 紀錄中。設定頁的「模型」區可以填寫升級模型、開關完成檢查。

## 16. 複雜任務的品質把關

### 主流做法（2026-10 調查）

| 來源 | 做法 | 值得學的細節 |
|---|---|---|
| [Anthropic 研究系統](https://www.anthropic.com/engineering/multi-agent-research-system) | 主代理規劃 + 子代理並行搜尋 | 計畫先存進記憶以免截斷；委派要給目標、輸出格式、工具指引與邊界；投入量依難度（簡單 1 代理 3–10 次工具、比較 2–4 子代理、複雜 10+）；多代理比單代理好 90.2%，但約 15 倍 token；獨立的引用代理核對出處 |
| [Anthropic 長時任務](https://anthropic.com/engineering/effective-harnesses-for-long-running-agents) | 初始化代理 + 逐步代理 | 需求展開成 JSON 功能清單（測試步驟 + `passes`，只能改這個欄位）；一次一項、git 提交、進度檔；瀏覽器端對端測試大幅改善，只跑單元測試會誤判完成 |
| [Claude Code 最佳實務](https://code.claude.com/docs/en/best-practices) | 給它能跑的檢查 | 測試 / 建置 / 截圖；Stop hook 確定性關卡；全新上下文的子代理反駁結果；要證據不要宣稱；提醒審查員只攔正確性與需求問題，避免過度設計 |
| [Cognition / Devin](https://cognition.com/blog/multi-agents-working) | 多個代理一起「想」、單一代理「寫」 | 撰寫者 + 審查者循環（審查員每 PR 抓約 2 個 bug、58% 嚴重），審查員要乾淨上下文，審查意見先比對使用者指示；向強模型「請教」只在兩邊都是頂尖模型時有效；並行撰寫群仍不可靠 |
| [Manus](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus) | 上下文工程 | 輸入:輸出 ≈ 100:1，快取命中率是最重要的指標（價差 10 倍）；前綴穩定、只增不改；檔案系統當記憶，壓縮保留路徑與網址；每步重寫 todo.md 讓目標留在尾端；保留錯誤紀錄 |
| [Anthropic 上下文工程](https://agentic-ai.readthedocs.io/en/latest/ContextEngineering/anthropic/) | 小而精的上下文 | 用到才讀；子代理回 1–2k token 摘要；清掉舊工具結果（峰值 33.5 萬 → 17.3 萬 token）；壓縮常遺失數字與原文 |
| [Anthropic 評估](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)、[OpenAI 護欄](https://openai.github.io/openai-agents-python/guardrails/) | 評估與護欄 | 20–50 個真實失敗起步；評結果狀態不評步驟；能力 / 回歸分開；pass^k 衡量穩定；讀執行紀錄；輸入 / 輸出 / 工具護欄可中止 |

### 對照 SinoBee 的缺口
1. **完成檢查只問「做了沒」**：輔助模型看截斷的工具紀錄，不打開產出；公式少反斜線、HTML 錯誤、壞連結、Mermaid 錯誤都會通過。
2. **檢查者比執行者弱**，只補一輪。
3. **驗收標準沒有在開頭定下**：計畫只是提示建議，長任務壓縮後中途補充的要求最容易遺失。
4. **審查看路徑**：只有研究報告流程有審查員。
5. **子代理產出沒有交叉驗證**，回傳長度沒有上限。
6. **上下文擺放傷快取與注意力**：`<context>`（含分鐘級時間、黑板、工作記憶）每次呼叫重建後附在本輪使用者訊息上，一變就讓後面所有工具結果的快取失效；目標也停在本輪開頭，不在尾端。
7. **沒有評估執行器**：換模型或改提示無法量化升降。

### 路線

| 階段 | 內容 | 狀態 |
|---|---|---|
| 1 | 易變上下文移到尾端、歷史只增不改；驗收清單每步附在尾端；確定性成果檢查；完成前的品質關卡 | **已實作**（見下） |
| 2 | 全新上下文 + 強模型逐項驗收（輸入實際成果與截圖）；高風險任務自動加審查員並過濾審查意見；委派投入量規則；子代理摘要上限；向強模型「請教」取代單向切換；依風險分級的驗證深度（快速 / 標準 / 嚴謹） | **已實作**（見下） |
| 3 | `sinobee eval`（評估集、pass@k / pass^k、退步偵測）；**每個 bug 變成一條檢查規則**（倒讚 → 確定性規則 + 回歸題）；技能宣告 `sinobee-checks`；寫檔快照與一鍵復原；長時任務的進度筆記與逐項專注 | **已實作**（見下）；技能品質分數上傳 SinoBee.ai 尚未做 |

### 第 1 階段（已實作）

**請求版面：只增不改的歷史 + 易變尾巴**（`agent/loop.py`、`providers/anthropic.py`、`types.TextPart.volatile`）
- 每次呼叫模型的請求 = `system` + 歷史（只增不改）+ 一則 `volatile` 的 tail 訊息：`<context>`、使用中技能的用法、驗收清單、空白回覆提醒。tail 每次重建、從不存進歷史。
- Anthropic 的快取斷點放在 tail 之前最後一個穩定區塊，前一次請求寫入的快取在下一次一定命中；OpenAI 相容端點（OpenAI、Gemini、DeepSeek…）的自動前綴快取同樣受惠。
- 驗收清單和進度在請求的最尾端，模型跑到第 50 步仍看得到目標（Manus 的 todo 重寫法）。

**驗收清單**（工具 `task_checklist`，`tools/checklist_tools.py`，屬於永遠開啟的 `core` 工具組）
- `set / add / update / view`；每項是「結果」不是「動作」，附驗證方式；`done` 必須附證據（指令輸出、檔案路徑、截圖、網址），`dropped` 必須說明原因。
- 存在 session 狀態與黑板（`kind=plan, key=checklist`），伺服器重啟或「繼續」都接得上；全部完成的清單在下一輪清空。
- 工具呼叫達 `agent.checklist_after_tool_calls`（預設 4）或使用高複雜度技能、卻還沒有清單時，tail 會提醒先寫清單。
- 介面在步驟摘要下方顯示清單與進度（收合步驟時仍可見），完成項附證據。

**成果檢查**（`agent/qa.py`，不使用模型）

| 檔案 | 檢查 |
|---|---|
| 全部 | 存在、非空、文字檔沒有 JSON 跳脫留下的控制字元（`\f` `\b`…） |
| HTML | 用無頭 Chromium 以文件檢視器的方式開啟（同樣自動加上 KaTeX）：JavaScript 錯誤、本機圖片 / 腳本 / 樣式 / 資料載入失敗、圖片無法解碼、`.katex-error`、Mermaid 語法錯誤圖、空白頁 |
| Markdown | 相對路徑的圖片與連結是否存在；每個 `$…$` / `$$…$$` 用 KaTeX 解析；每個 mermaid 區塊用 `mermaid.parse`（先套用與聊天介面相同的自動修正） |
| JSON / CSV / SVG | 可解析 / 欄數一致 / XML 正確 |

外部網路請求在檢查時一律擋掉，結果不受網路影響；沒有 Chromium 時只跑檔案層檢查。

**品質關卡**（`Agent._quality_gate`，在完成檢查之前）
1. 驗收清單還有未完成項目 → 退回「[自動驗收] 還有 N 項未完成…」。
2. 本次產出的檔案有問題 → 退回「[自動成果檢查] … 問題清單」。
3. 兩者各最多退回 `agent.quality_rounds`（預設 2）輪；仍不通過就標成 incomplete，結果卡列出問題，不顯示「✓ 完成」。
4. 通過後才進行原本的完成檢查（完成檢查的證據也加入驗收清單）。
- 設定：`agent.qa`（成果檢查開關）、`agent.quality_rounds`、`agent.checklist_after_tool_calls`。

### 第 2 階段（已實作）

**品質等級**（`agent/review.py` 的 `quality_level`，設定 `agent.quality`：auto / fast / standard / strict）

| 等級 | 做什麼 | auto 何時選它 |
|---|---|---|
| 快速 | 只有第 1 階段的確定性關卡（驗收清單、成果檢查），不問模型 | 簡單任務、子代理（由協調者把關） |
| 標準 | ＋ 驗收審查，修正 1 輪 | 產出了檔案，或用了 3 個以上工具 |
| 嚴謹 | ＋ 獨立審查員，修正 2 輪 | 修改了程式碼；用 2 次以上網頁研究並寫成報告；回報資料庫查詢的數字；使用高複雜度技能 |

`agent.verify=false` 等同快速。選定的等級與原因會顯示在對話裡。

**驗收審查**（`Agent._acceptance` / `_review`）
- 由審查模型（沒設定時用思維模型，再沒有就用執行模型；不用較弱的輔助模型）以**全新上下文**審查：只有一則訊息，不給工具。審查模型無法回應時不改找別的模型，而是記錄成「未經驗收審查」。
- 輸入是實際成果：要求、執行中補充、驗收清單與證據、產出檔案的節錄（每檔 6,000 字元、合計 24,000）、產出頁面的截圖（成果檢查時以無頭瀏覽器拍下，模型看得懂圖時附上）、最後回答；嚴謹等級另附獨立審查員的發現。
- 規則：評「結果」不評過程；只把**造成結果錯誤或漏掉需求**的問題退回，風格偏好與額外加分項不退（避免過度修改）；審查員的發現由它判斷哪些算阻擋。
- 回覆 JSON：`verdict`、逐項 `items`、`problems`（what / evidence / fix）、`notes`、`summary`。退回訊息「[自動驗收審查]」附逐條修正清單，並提醒只處理這些問題、不要擴大範圍；仍不通過就標成 incomplete，結果卡列出問題。
- 審查模型呼叫失敗時退回原本的輕量完成檢查（輔助模型），不會擋住回答。

**獨立審查員**（嚴謹等級，`Agent._independent_review`）
- 在驗收審查之前派一個唯讀的 `reviewer` 子代理（全新上下文，`agent.reviewer_iterations` 預設 15 步）：重新打開引用的網頁、重跑唯讀查詢、讀檔重算關鍵數字，回報不超過 300 字並以 `VERDICT:` 結尾。
- 每輪只派一次；之後的修正輪由驗收審查對照目前的檔案確認是否已修好（借用 Devin 的審查橋接）。`agent.reviewer=false` 可關閉。

**向較強的模型請教**（舊設計；2026-10-19 起只保留「請教」並交給思維模型，switch／auto 已移除，見第 20 節）
- consult：較強的模型讀取完整上下文的分支（不給工具），回覆「哪裡出錯、接下來 2–4 步、該停止做什麼」，以「[較強模型的建議]」加入歷史，原模型繼續做。每輪最多 2 次、至少相隔 5 步。
- switch：原本的行為，剩下的步驟改用較強的模型（每輪一次）。
- auto：任務一開始就屬於高複雜度技能，或目前是本機模型（`ollama/`、localhost 端點）時用 switch；其他用 consult。這呼應 Cognition 的觀察：兩邊都是頂尖模型時請教有效，主模型太弱則不然。
- 完成檢查未通過不再觸發升級：驗收審查本身就由較強的模型給出修正清單。

**委派**（`delegate`）
- 工具說明加入投入量規則：單一查詢不委派、比較 2–4 個子代理、只有界線清楚的大型研究才更多；同一個檔案只由一個代理撰寫。
- 每個子任務可帶 `output`（預期輸出）與 `boundaries`（不涵蓋什麼），組成自足的任務說明。
- 子代理報告控制在約 300 字；回給協調者的報告超過 3,000 字元時截斷，完整內容寫到黑板（`finding`，key `report:<session>`）。流程節點仍拿到完整輸出。

**介面**：設定 →「模型」→ 進階：「品質把關」（自動／快速／標準／嚴謹）與「開工前先規劃」（自動／每次／不規劃；原本的「卡關時找較強的模型」已移除）。對話中顯示品質等級、獨立審查、驗收審查的進度；審查要求修正時列出問題與修法，成果檢查的問題也以清單顯示。

### 第 3 階段（已實作）

**品質規則：每個 bug 變成一條檢查**（`agent/rules.py`，存在 `$SINOBEE_HOME/qa_rules.json`）
- 一條規則 = 套用的檔案（glob）＋一個正規表示式，`forbid`（不可出現）或 `require`（必須包含）。不可出現的規則不得匹配空字串（否則每個檔案都會被擋）。
- 品質關卡在成果檢查之後套用所有啟用的規則，違反時以「違反品質規則「…」」退回，和成果檢查共用修正輪數；每條規則記錄抓到的次數。
- 三個來源：
  1. 使用者對結果按 👎：`learn_from_feedback` 在反思之後呼叫 `propose_from_feedback`，由輔助模型判斷問題能否用一個正規表示式在產出檔案中抓到。**只有實際能標出這一輪被嫌棄的檔案時才保留**（證明它抓得到這個 bug），同時存一題回歸題（評分：完成、該規則不再違反、依回饋寫成的 rubric）。對話裡顯示「新增品質規則…」。
  2. 使用者要求「以後不要再犯」：工具 `quality_rule`（core 工具組，子代理不可用），新增 / 刪除需要使用者核准（核准模式為 auto 時除外）。
  3. 設定 →「品質」手動新增。
- 設定：`learning.quality_rules`（倒讚是否提出規則，預設開）。

**技能宣告的檢查**（`skills/checks.py`）
- `SKILL.md` metadata：`sinobee-checks: "scripts/check.py {file}"`（`{file}` / `{dir}` 自動帶入）與 `sinobee-checks-on: "reports/*.md"`。
- 本輪用過的技能（`skill_view` / `skill_run` / 斜線指令）若有宣告，品質關卡就對符合的產出檔案執行它：結束碼 0 且輸出 JSON 的 `problems` 為空才算通過；`status: fix` 或 `ok: false` 也算失敗。腳本被拒絕、或需要核准但核准模式不是 auto 的技能不執行。
- 結果寫進 `skill_checks` 表，技能統計多一欄 `checks`（次數、通過率、最近的問題）。只用 `{dir}` 的檢查以資料夾為單位，每個資料夾只跑一次，問題標在資料夾上。`youtube-analysis` 已宣告 `scripts/run.py check {dir}`（`reports/youtube/*/report.md`），`engineering-feasibility` 宣告同樣的指令（`reports/feasibility/*/report.md`，在報告寫好後檢查 feasibility.json、report.md 與 page.html）；`skill-authoring` 教寫技能時加上。
- 設定：`agent.skill_checks`。

**評測**（`sinobee/evals/`，CLI `sinobee eval`，API `/api/evals`，設定 →「品質」）
- 任務：`prompt`、`setup.files`（文字、base64 或從檔案複製）、`toolsets`、`trials`、`timeout`、`graders`。
- 評分器：`file_exists`、`file_regex`、`file_not_regex`、`answer_regex`、`answer_not_regex`、`qa_clean`（成果檢查＋品質規則）、`status`、`command`（在工作區執行，看結束碼）、`tool_used`、`rule`、`llm`（強模型依 rubric 評，不確定就判失敗）。能用確定性評分就不用模型。
- 每次試跑都在**拋棄式的 SinoBee** 裡進行：暫時的 home 與工作區、使用者技能的複本、核准自動、學習與快照關閉，讀取使用者的金鑰但不寫入任何設定、記憶或技能；模型費用以 purpose `eval` 計入使用者的用量。
- 指標：每題 k 次的 pass@k（至少一次通過：做得到）與 pass^k（全部通過：靠得住），加上費用、平均步數、時間。和同一測試組上一次的結果比較，列出**退步**（上次通過、這次失敗）與修好的題目；CLI 有退步時結束碼為 2，可放進 CI。
- 測試組：`core`（內建：mermaid quadrantChart、頁面中的 LaTeX、Markdown 圖片嵌入、驗收清單紀律與數字一致、不捏造答案）、`regressions`（從 👎 產生）、`skill:<名稱>`（技能的 `evals/evals.json`，anatomy 格式，以 expected_output 當 rubric）、任意 JSON 檔。
- 結果存在 `$SINOBEE_HOME/evals/results/<id>.json`。
```
sinobee eval list
sinobee eval run core -k 3          # 每題跑 3 次
sinobee eval run regressions
sinobee eval run skill:youtube-analysis --task youtube-analysis:1 --keep
sinobee eval show                   # 最近一次的結果
```

**寫檔快照與復原**（`agent/snapshots.py`）
- `write_file` / `edit_file` 改動檔案前，把原本的內容複製到 `$SINOBEE_HOME/snapshots/<session>/<turn>/`（每個檔案每輪只存第一次，也就是要回到的狀態；超過 20 MB 不存並註明）。同一輪內子代理的改動也存在同一份快照。
- 結果卡出現「復原本輪的檔案變更」：把改過的檔案放回原樣、移除這輪新建的檔案，並列出無法復原的檔案。shell 指令造成的改動不在範圍內。
- 舊快照在 `agent.snapshot_days`（預設 14）天後清除；`agent.snapshots=false` 關閉。

**長時任務**
- 驗收清單 8 項以上且仍有未完成時，尾端提醒「一次只做一項：標成 doing、做完並驗證、附證據標 done，再開始下一項」。
- 任務沒做完就結束（步驟用完、連續錯誤、未通過驗收…）且清單還有未完成項目時，寫一則黑板筆記（`note`，key `progress`）：已完成哪些、還剩哪些、為什麼停下；下一輪「繼續」時與清單一起接上。

## 17. 從 Roadster Rocket 案例學到的（2026-10-18）

### 案例
題目是分析 Tesla Roadster Event 影片的 Roadster Rocket，並用 anatomy 做 3D 動畫。本機 SinoBee（gemini-flash → gemini-pro）跑了 36 分鐘、30 步，交付的 page.html 與 sinobee.ai 上的分享成果**逐位元組相同**：

1. `engineering-feasibility` 的範本 `feasibility.example.json` 本身就是 Roadster 的完整答案，`--example roadster` 又把整套模型與五張圖複製進工作區。代理只把 `"example": true` 改成 `false`。
2. 答案裡的 690 bar／313 kg 和逐字稿矛盾。講者在 12:11 說「two of them … 600 L … about 250 kilos of air」，換算約 450 bar。代理手上已有逐字稿，卻沒有發現。
3. 獨立審查員寫「2 罐」（頁面是 3 罐）、「audit 與 check 均無錯誤」（本輪沒跑 audit），最後判 pass。
4. 審查結束後品質關卡崩潰：`name 'Path' is not defined`。
5. 開頭召回的情節記憶帶著「successfully … fully meets」這類自評。
6. 每步等 1–3 分鐘：輸入約 35k token/呼叫，同步的上下文壓縮跑在本機 4B 模型上。

### 修正

| 缺陷 | 修正 | 位置 |
|---|---|---|
| 品質關卡崩潰 | 補 `Path`；每個品質步驟包在 `_guarded`，出錯只略過該步並記在 `turn_end.quality_skipped`，不讓已完成的任務變成 error；`ruff F821/F811/F401` 成為測試 | `agent/loop.py`、`tests/test_quality4.py` |
| 技能範例＝答案 | 範本改成 TODO 骨架；拒絕 `--example`，Roadster 移到 `references/` 只當程式寫法參考；scaffold 寫 `.scaffold.json` 出處；check 擋下與範例相同的檔案、重複範例的 tiles、未改的範本模型 | `bundled_skills/engineering-feasibility` |
| 數字與來源矛盾 | `feasibility.json` 新增 `source_facts`（原句、時間、數值、單位）。check 會在逐字稿裡找原句，並要求模型用到每個數字，否則必須寫 `override` 與證據；每張圖要有 `audits/<fig>.txt` 且含 `audit passed` | 同上 `scripts/check.py` |
| 影片技能同型風險 | plan.json 的標題或劇本與內建範例片相同時，validate 判錯（`example_ok: true` 才放行） | `ai-video-production/scripts/project.py` |
| 審查員蓋章 | 審查員與驗收審查都拿到 **工作紀錄**（每個工具步驟）與本輪讀過的來源；審查員必須先做 SOURCE CHECK（引用來源原句核對關鍵數字），只有工作紀錄裡有、或自己跑過的檢查才能說通過；宣稱某項檢查通過、但工作紀錄裡沒有對應步驟的，會被標記：審查員的 pass 作廢，代理自己的宣稱以 UNBACKED CLAIMS 交給驗收審查 | `agent/review.py`、`agent/loop.py` |
| 同源盲點 | 新角色 `model.reviewer`（設定頁「審查模型」、`SINOBEE_REVIEWER_MODEL`）：獨立審查員與驗收審查都用它，建議選和主要模型不同家族的模型 | `config.py`、`settings_api.py`、`index.html` |
| 記憶自評 | 反思提示要求情節摘要只寫事實；存入與召回時都去掉自評字眼；召回的情節標示「過去會話的線索，需重新驗證」 | `memory/neutral.py`、`learning/reflector.py`、`agent/prompt.py` |
| 證據只是自述 | 驗收清單標 done 的證據必須可查：存在的檔案路徑、URL，或本輪工具真的回傳過的文字 | `tools/checklist_tools.py` |
| 延遲 | `agent.inline_aux`（預設 auto）：主模型在雲端、輔助模型在本機時，任務當下要等的壓縮改用主模型；背景工作仍用輔助模型 | `agent/loop.py` |
| 重跑時偽造稽核紀錄（`echo PASS`） | `run.py audit <folder>` 執行每張圖的 `build({ audit: true })`，紀錄帶圖檔雜湊與結束碼；check 只認這種紀錄 | `engineering-feasibility` |
| 舊資料夾被沉默沿用 | scaffold 對已有前次檔案的資料夾發出警告 | 同上 |
| 60 步不夠 | 2026-10-19 起取消固定步數上限，改由進度判斷（第 22 節）；300 步只是安全閥 | `agent/progress.py`、`agent/loop.py` |
| 升級模型一步卡 15 分鐘 | `agent.model_deadline`（240 s）；逾時請求只重試 1 次。2026-10-19 起步驟一律由執行模型做，思維／審查呼叫超時就略過並記錄，不再改用別的模型（第 20 節） | `agent/loop.py`、`providers/base.py` |
| 審查員的 revise 被丟掉 | 驗收審查無法執行時，審查員的 revise 當修正清單送回一次 | `agent/loop.py` |
| 召回不相關記憶 | 關鍵詞依查詢詞覆蓋率計分、embedding 0.45 以下不計、`memory.recall_min_rel`（0.3） | `memory/store.py` |
| 挑容易的主張湊「可行」 | 極限清單每項要有主張檢驗或 `verdict_impact`；文章引文存 `sources/` 才能查證 | `engineering-feasibility` |
| 圖形畫出畫框、範本文字留在頁面 | 成果檢查偵測 SVG 超出 viewBox；check 偵測範本可見文字 | `agent/qa.py`、`engineering-feasibility` |
| 中文題目交英文報告（釷基熔鹽反應爐） | 成果檢查以字元統計判斷每個產出文字檔（.md/.html/.txt，剝除程式碼、網址、公式）與最後回答的語言，與使用者的語言不符就退回；分辨繁體／簡體；使用者明說「用英文」時以指示為準；子代理不檢查 | `agent/language.py`、`agent.language_check` |
| 沒有人看過成果的畫面 | 結構檢查都通過後，替每個產出頁面截全頁與每張圖（svg/canvas/img/video，附圖說或最近的標題），連同產出的圖片檔，交給視覺模型（審查模型 → 升級模型 → 主模型，各有截止時間）依評分準則判讀：空白或被裁切、文字重疊、與圖說不符、物理上可見的錯誤（力或流向相反、座標軸沒有單位、數字互相矛盾）、範本文字或語言不符。只有 blocking 會退回；判讀過關的畫面以雜湊快取，同一輪不重送；無法判讀時記為「未檢查」，不當作通過 | `agent/visual.py`、`agent/qa.py`（FIG_PROBE）、`agent.visual_check` |
| 回歸 | `core` 評測新增 `source-numbers-win`：逐字稿說 250 kg，舊文章說 690 bar／313 kg，答案必須以講者為準 | `evals/suites/core.json` |

### 仍待處理
- 一般性的「產出與技能資產相同」偵測只在 engineering-feasibility 與 ai-video-production 的 check 裡；其他技能的範例，在新增時就要遵守「範例只示範形狀，不放可直接交付的內容」。
- 已安裝且被使用者改過的內建技能不會自動升級（`_install_bundled` 只升級未修改的副本）；改過 engineering-feasibility 的使用者要手動更新。

## 18. 後續路線

1. MCP client：接入外部 MCP 工具
2. 訊息閘道：Slack / Telegram / LINE（背景工作完成與待核准通知）
3. 流程編輯器：在 Web UI 以圖形方式編輯 YAML 流程
4. sinobee.ai Skill Hub 服務端（協定見 SKILL_HUB.md；用戶端已完成）
5. Electron 桌面殼：包裝 Web UI，並以主機模式運行
6. 記憶：有 embedding 時以向量分群壓縮
7. 技能品質分數：把技能宣告檢查的通過率（匿名、可關閉）上傳 SinoBee.ai，顯示在技能頁

（子代理、平行工作流與排程任務已在第 14 節實作；Roadster 案例的修正見第 17 節。）

## 19. 內建技能盤點（對照 Claude 內建技能，2026-10-19）

對照 Claude（Claude Code / Cowork）提供的技能，SinoBee 缺少的是**一般工作最常交付的檔案格式**與**看成果**的能力。補上的技能全部是 SinoBee 自己撰寫（Claude 的 docx/xlsx/pptx/pdf 技能為專有授權，不複製），每個都有 `scripts/run.py`、宣告的檢查與測試（`tests/test_builtin_skills.py`）。

| Claude 技能 | SinoBee 對應 | 狀態 |
|---|---|---|
| docx | `word-documents`：大綱 JSON → .docx（樣式化標題、清單、表格、圖說、目錄、頁碼、東亞字型）；`replace --track` 產生真正的追蹤修訂；check（標題層級、殘留範本字、CJK 字型、空段落）；render | 新增 |
| xlsx | `spreadsheets`：活公式、輸入/公式配色、數字格式、凍結窗格、原生圖表；check 以 LibreOffice 重算後找 #DIV/0!、#REF!、#NAME?、文字型數字、手打的合計 | 新增 |
| pptx | `presentations`：16:9 主題版型（title/section/bullets/two-column/image/chart/table/quote/big-number/closing）、原生圖表、講者備註；check 以 CJK 字寬估算文字溢出、出界、空白版位、過小字級；render 逐頁 PNG | 新增 |
| pdf / pdf-reading | `pdf-documents`：info（掃描頁、字型嵌入）、text、tables、render、merge/split/rotate/stamp、表單 fields/fill、HTML→PDF（Chromium，中文正確） | 新增 |
| file-reading | 核心：`read_file` 讀 .docx/.xlsx/.pptx/.pdf 時回傳文字（`sinobee/docread.py`，Office 只用標準函式庫） | 核心改進 |
| dataviz | `data-visualization`：圖表形式選擇、誠實刻度、`sbplot`（CJK 字型、缺字拒存）、經 protan/deutan/tritan 模擬驗證的 6 色調色盤（`run.py validate`） | 新增 |
| frontend-design | `frontend-design`：方向、設計代號、行動優先、離線自足；check 以 390/1280 px 與深色模式開頁：橫向捲動、對比、alt、lang、viewport、網路資源、腳本錯誤 | 新增 |
| /review、/security-review（Claude Code） | `code-review`：scope（範圍、提交、diff、測試指令）、secrets；流程要求每個發現都要以具體輸入證實 | 新增 |
| skill-creator | `skill-authoring` | 已有 |
| deep-research | `web-research` | 已有 |
| computer-use / chrome-browser | `desktop-automation`、`browser-form-filling` | 已有 |
| 生活類（訂餐、退貨、報帳…）、品牌/主題、Slack GIF | 不內建：屬個人或組織情境，可由使用者安裝 | 不納入 |

核心配套：
- 技能的 Python 腳本改用 SinoBee 自己的直譯器（`sys.executable`）執行，`pip install "sinobee[office]"` 裝的套件就用得到。
- 新的中繼資料 `sinobee-requires-python`（`docx=python-docx, openpyxl`）：缺套件時 `skill_run` 直接回報缺哪些與安裝指令；技能檢查在缺套件時略過而不誤擋。
- 視覺檢查（第 17 節）會把產出的 PDF / Word / PowerPoint / Excel 前 4 頁轉成圖片一起判讀（需要 LibreOffice 與 pdftoppm）。
- `pyproject.toml` 新增 `office` extra。

## 20. 思維模型與執行模型（2026-10-19）

### 決定
SinoBee 原本是「主模型 + 升級模型」：卡關、步驟太多或遇到複雜技能時，剩下的步驟改由較強的模型接手（switch），或由它給建議（consult）；較強的模型逾時又會把步驟交回主模型。結果是一個任務常由兩個模型輪流做，失敗時說不清是誰、在哪一步、因為什麼能力不足，也無從改進。

現在改成兩個固定的角色，任務中**不自行切換模型**：

| 角色 | 設定 | 做什麼 | 不做什麼 |
|---|---|---|---|
| 思維模型 | `model.thinking`（`SINOBEE_THINKING_MODEL`；舊的 `model.escalate` 會自動視為它） | 開工前規劃；卡關時給建議並判斷執行模型還能否完成；交付前驗收（沒有設定審查模型時）；失敗時診斷 | 不呼叫工具、不執行任何步驟 |
| 執行模型 | `model.default`（`SINOBEE_MODEL`） | 依計畫執行每一步：工具、檔案、回覆；主代理與子代理都用它 | 不會中途被換掉 |
| 審查模型（選用） | `model.reviewer` | 驗收審查、獨立審查員 | — |
| 輔助模型 | `model.auxiliary` | 背景工作：反思、記憶整理、壓縮 | 不參與任務步驟與判斷 |

沒有設定思維模型時，思維的工作由執行模型以獨立的呼叫完成（用同樣的規劃／請教／診斷提示，不混進執行步驟）。`model.fallbacks` 仍是**服務中斷**時的備援（供應商無法連線、額度用完），不會因為任務困難而啟用。

### 流程（`agent/thinking.py`、`agent/loop.py`）
1. **規劃**（`agent.plan`：auto／always／off）：auto 在指定了技能、用到 `sinobee-complexity: high` 的技能、或需求超過 200 字時觸發。思維模型拿到需求、記憶脈絡、工具清單與相關技能的用法，回覆 JSON：理解、做法、3–10 個步驟、2–8 項驗收清單、風險、需要先問使用者的問題。計畫以「[思維模型的計畫]」加入歷史（只加一次，不影響快取），驗收清單直接建立；對話顯示計畫卡。接續先前未完成的清單（「繼續」）時不重新規劃。子代理不規劃，由協調者給任務說明。
2. **執行**：每一步都由執行模型做（`_step_chat` 不再有逾時換手）。
3. **請教**：連續工具錯誤達 `agent.consult_after_errors`（3）、重複呼叫已失敗的操作、連續 K 步沒有進度、偵測到循環、或健康水位（每 40 步、回合內壓縮上下文）時（觸發條件見第 22 節），下一步開始前思維模型讀取完整上下文的分支，回覆「哪裡出錯、接下來 2–4 步、該停止什麼」，最後一行 `DECISION: continue` 或 `DECISION: stop - 原因`。建議以「[思維模型的建議]」加入歷史。每個任務最多 `agent.consult_max`（2）次、至少相隔 5 步。
4. **判定失敗**：
   - 思維模型回覆 `stop` → 任務結束，狀態 `failed`（`error.kind = capability`），結果卡顯示「✕ 任務失敗」與原因。
   - 達到硬上限（連續錯誤 `agent.max_consecutive_errors`、重複失敗 3 次）時，若還能請教就先請教並再給一次機會；已請教過仍卡住 → `failed`。
   - 執行模型連續回傳空白回覆 → `failed`。
   - 諮詢次數用完後仍沒有進度或仍在循環 → `failed`（`error.trigger` 記錄是哪一個條件）。
   - 單一 Agent 達 300 步 → 安全閥，`failed`（`error.kind = safety_valve`，視為系統異常）。
   - 呼叫端指定的步數上限（子代理、審查員）用完、未通過驗收仍維持 `incomplete`，但同樣保存失敗報告。
5. **逾時與錯誤**：思維模型、審查模型的呼叫超過 `agent.model_deadline`（240 秒）或出錯時，那一步略過並記錄在 `turn_end.thinking_skipped`／`quality_skipped`，不改找別的模型代替。

### 失敗報告（`agent/failures.py`）
- 狀態 `failed`、`incomplete`、`error` 的任務（主代理）都會存一份 `$SINOBEE_HOME/failures/<id>.json`（最多 300 份）：
  - 要求、最後回答、失敗原因；
  - 執行／思維／審查模型、用到的技能；
  - 思維模型的計畫、每次請教的原因、建議與決定；
  - 驗收清單與證據；
  - 工作紀錄：每一步的工具、參數、成功與否、錯誤或結果摘要，以及歷史中的系統訊息（建議、修正清單、使用者插話）；
  - 品質檢查：退回輪數、成果檢查問題、驗收審查、略過的檢查；
  - 用量、步數、時間。
  已存的金鑰與密碼會從紀錄中替換成 `[secret]`。
- 背景由思維模型**診斷**：類別（執行能力不足、技能有誤、缺少工具或權限、環境、需求不明確、外部阻擋、系統錯誤）、出錯的步驟、引用的證據、具體改進（對象：model／skill:名稱／tool:名稱／prompt／settings／sinobee）、使用者現在可以怎麼做。對話中顯示「失敗診斷」。
- 查看：結果卡的「查看」、設定 →「品質」→ 失敗報告、`sinobee failures list|show|regression|delete`、`GET /api/failures[/<id>]`。
- **轉成回歸測試**：以原始要求建立一題 `regressions` 評測（評分：完成，且不再以診斷描述的方式失敗），改進之後用 `sinobee eval run regressions` 量化是否修好。
- 設定：`agent.failure_reports`、`agent.failure_diagnosis`。

### 介面
- 設定 →「模型」：思維模型、執行模型、輔助模型、審查模型、Embedding；備援模型說明改為「只在服務中斷時」。
- 設定 →「模型」→ 進階：「開工前先規劃」。原本的「卡關時找較強的模型（自動／請教／切換）」移除。
- 對話：計畫卡、請教與判定的事件、「✕ 任務失敗」結果卡與失敗報告連結、失敗診斷。

## 21. 中斷任務自動恢復（2026-10-19）

**問題**：伺服器重新啟動或程序當掉時，執行中的對話任務在工作紀錄被標成 interrupted，但只有固定流程會自動續跑；對話畫面沒有結果卡，看起來既沒在跑、也沒說失敗。

**做法**（`work/recovery.py`，由工作中心的排程迴圈每分鐘呼叫）
1. **找出中斷的任務**：
   - 標成執行中、但伺服器裡已沒有對應執行中對話的 chat run（開始超過 60 秒），改標為中斷（「執行中斷（沒有回應）」）；
   - 啟動時被標為中斷的 run。
2. **對話任務**：只處理網頁介面開的對話、`work.auto_resume_hours`（24）小時內、而且是該對話最新的一輪。
   - **輔助模型**讀取原本的要求、已完成的步驟、驗收清單與最後的回覆，判斷要不要續跑；已經回答完、或正在等使用者回答或核准的，就不續跑。
   - 要續跑時，輔助模型寫出進度整理（已完成、還沒做、先做什麼），以「[自動續跑]」訊息在同一段對話開始新的一輪，由執行模型接著做。
   - 每段對話每天最多自動續跑 `work.auto_resume_max`（2）次，避免一直讓程序當掉的任務無限重來。
   - 決定結果記錄在 run 的 `state.auto_resume`。
3. **排程工作**：中斷後自動重新執行一次。
4. **介面**：
   - 打開中斷的對話時顯示「△ 任務中斷」卡片與自動續跑狀態，並提供「立即繼續」；
   - 續跑時顯示「↻ SinoBee 自動續跑中斷的任務」與進度整理；
   - 手動續跑過的 run，自動檢查不會再處理。
- **使用者停止的任務絕不自動續跑**：
  - 按停止（含強制中止）時，run 記為 cancelled，並標示 `state.user_stopped`；
  - 中斷卡片上按「不要續跑」也會這樣標示；
  - 執行中當掉的回合記為失敗，不算中斷；
  - 對話一開始就建立 run 紀錄，連規劃階段中斷也找得到。
- **中斷卡片**依狀態顯示：
  - 正在續跑：可直接「停止」；
  - 會自動續跑；
  - 為什麼不會續跑（超過時限、今天已續跑過、你已停止…）：可按「立即繼續」或「不要續跑」。
- 歷史中的系統訊息（思維模型的計畫與建議、自動檢查的退回）顯示為可展開的系統註記，不再像使用者訊息。
- **沒有回合紀錄的最後一輪**（重啟中斷、選了不續跑、當掉），依它的工作紀錄顯示一張卡片：
  - 狀態、做了幾步；
  - 已產出的檔案，可以直接開啟；
  - 「繼續」按鈕。
- 同一輪的錯誤只顯示一張卡片：對話紀錄裡的失敗標記與回合紀錄合併。
- **案例（2026-10-19，「分析 Roadster Rocket 汽車飛行的實現原理」）**：
  - 續跑的那一輪做了 53 步、產出 v3 報告與動畫頁之後，SinoBee 重新啟動而中斷；後來使用者選了不續跑，畫面就什麼都不顯示。
  - 同一輪「對話接近模型上限」出現 16 次，原因是 `gemini-pro-latest`／`gemini-flash-latest` 沒有對到 1M 的上下文表，被當成 32k，每隔幾步就壓縮一次。
  - 已修正：Gemini 1.5 之後與 `-latest` 別名一律 1,048,576。
- 網頁介面的確認、提示與輸入改用自己的對話框（`uiConfirm`／`uiAlert`／`uiPrompt`），不再用瀏覽器原生的 confirm／alert／prompt。
  - 刪除、撤回這類動作用紅色按鈕，並預設聚焦「取消」：按 Enter 不會誤刪。
  - Esc 或點背景即可取消。
- 設定：`work.auto_resume`、`work.auto_resume_max`、`work.auto_resume_hours`。



## 22. 以進度取代步數上限，與執行控制紀錄（2026-10-19）

依〈SinoBee 工作流程改善提案〉第 1 階段實作。程式：`agent/progress.py`、`agent/loop.py`。

### 每一步的進度判斷
每一步（模型的一次回覆與它呼叫的工具）結束後，`Tracker.observe` 判斷這一步有沒有讓任務前進：

| 判斷 | 條件 |
|---|---|
| 實質進度 | 驗收清單狀態改變、測試結果改變（pytest／jest／cargo／unittest／go）、新檔案、文件結構變化（標題數改變，或內容增減 ≥ 200 字且 ≥ 5%）、寫入黑板、委派、詢問使用者 |
| 只有檔案修改 | 檔案內容（忽略空白）改了，但上面都沒有。連續 N 步內仍算進度：程式碼 N=8、文件 N=3、其他 N=5 |
| 新資訊 | 新的讀取、搜尋或指令輸出，而且沒有在改檔 |
| 沒有進度 | 錯誤、重複讀取、寫入相同內容、檔案改回先前版本、測試結果沒變 |

連續 K（15）步沒有進度 → 請教思維模型。循環不必等 K 步：同一組操作 3 次且沒有進度（`loop_repeat`）、A→B→A→B（`loop_alternating`）、檔案在 8 步內改回先前版本 2 次（`file_oscillation`）、連續 3 步相同錯誤（`repeated_error`）。健康水位：每 40 步驗收清單仍未完成、或回合內因自己的工作壓縮了上下文 → 請教（`health_steps`／`health_compaction`），只是檢查點。諮詢次數用完（`agent.consult_max`）仍沒有進度或仍循環 → `failed`（`advice_exhausted`）。單一 Agent 300 步 → 安全閥（`safety_valve`）。

設定：`agent.progress.{k, n_code, n_document, n_other, health_steps, safety_max_steps, window}`。`agent.max_iterations`、`max_iterations_complex`、`consult_after_steps` 已移除；子代理與審查員仍可由呼叫端指定步數上限。

### 檔案類型判定（`progress.classify`）
1. 二進位內容 → other。`.example`／`.bak` 等後綴先去掉再判定。
2. 檔名：Makefile、Dockerfile、requirements.txt、package.json、.gitignore… → code；.env、.npmrc… → data。
3. 副檔名：程式（.py .ts .mjs .css .sql .prisma …）→ code；文件（.md .txt .tex .docx …）→ document；資料（.json .yaml .csv …）→ data。
4. HTML：先看內容是否有決定性（模板或框架語法、程式＋SVG＋樣式 ≥ 80%、canvas 加 script ≥ 30% → code；可讀文字 ≥ 50% → document），再看資料夾（src、static、templates… → code；docs、reports… → document），最後看其餘內容比例，預設 document。
5. 沒有副檔名：shebang → code；README、LICENSE → document；一般文字 → document；否則 other。

不依工具判斷：報告任務也會用 bash 建置。每次判定都附理由（例如「HTML 以程式與圖形為主（SVG 98%）」），會出現在進度紀錄中。

驗證：
- `tests/test_progress.py` 的 69 個標註樣本（含 Roadster 案例的頁面形狀）全部正確。
- 在實際資料上跑過：使用者工作區、SinoBee、SinoBee.ai 共約 4,000 個檔案。第一版找出三類錯誤並已修正：
  - `.DS_Store`、`.ruff_cache` 等二進位檔被當成文件；
  - Roadster 的 package report 頁面被當成文件，實際上 98% 是生成的 SVG，修改方式接近程式；
  - `.prisma`、`.env.example` 被判為無法判定。

### 執行控制紀錄
每一次請教、略過請教（原因：次數用完／這步已排定／距上次不到 5 步）、思維模型的回覆，以及停止，都是一筆紀錄：

```json
{"event": "consult_requested", "trigger": "no_progress", "label": "連續多步沒有進度", "step": 8,
 "detail": "連續 4 步沒有進度（門檻 K=4）；最後一次有進度是第 4 步",
 "thresholds": {"K": 4, "N_code": 8, "N_document": 3, "N_other": 5, "health_steps": 40, "safety_max_steps": 300},
 "evidence": ["#3 ✓ 只有檔案修改 2/3 步（document）：修改 report.md（document：副檔名 .md）：內容變動 +2 字，未達結構變化門檻 200", "…"]}
```

同一筆紀錄寫到下面這些地方：
- 失敗報告的 `control_log`、`progress`（每步判斷）和 `thresholds`，並納入思維模型的診斷；
- 執行紀錄的 `state.control` / `state.progress`，工作頁的執行詳情顯示「執行控制」；
- 對話中的控制卡片，展開「觸發依據」可看門檻與每步判斷；
- `$SINOBEE_HOME/logs/control.jsonl`，超過 5 MB 會輪替；
- 伺服器 log `sinobee.control`；
- `/api/control-log?session=&trigger=&event=`（含各觸發條件的次數）；
- `sinobee control-log [--stats] [--evidence] [--trigger …]`；
- 設定 →「品質」的「執行控制紀錄」。
