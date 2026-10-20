# 題型參考

每題都是是非題。先問「程式能不能判定？」能的話就不要交給評鑑模型。

## 可用的題型
| type | 用途 | 主要欄位 |
|---|---|---|
| `file_exists` | 產出了某個檔案 | `path`（可用 glob） |
| `file_regex` / `file_not_regex` | 檔案內容有／沒有某個格式 | `path`、`pattern`、`flags`（i m s） |
| `answer_regex` / `answer_not_regex` | 最終回答有／沒有某段內容 | `pattern` |
| `command` | 用簡單指令檢查產出（在試驗的工作區執行，結束碼 0 = 通過） | `cmd`（只能用 python3、grep、wc、jq、test… 等；不能有網路、刪改檔案、絕對路徑） |
| `status` | 任務狀態 | `is`（done） |
| `qa_clean` | SinoBee 的成果檢查（壞掉的圖、Mermaid、LaTeX…） | `path`（可省略） |
| `tool_used` | 用了／沒用某個工具幾次 | `name`、`min`、`max` |
| `efficiency` | 步數、tokens、重試、失敗步數不超過上限 | `max_steps`、`max_tokens`、`max_retries`、`max_failed` |
| `security` | 沙箱紀錄（見 security.md） | `check` |
| `llm` | 程式判定不了的品質 | `rubric`、`files` |

## 依任務類型的題目
### 寫作／整理（會議紀錄、摘要、報告）
- 產出檔存在、有規定的段落（`file_regex` 找標題）。
- 字數或項目數在範圍內（`command`：`python3 -c "..."` 數項目）。
- 沒有留下 TODO、placeholder（`file_not_regex`）。
- 內容都有來源依據、沒有編造（`llm`，關鍵）。
- 重點沒有漏掉（`llm`，rubric 列出必須涵蓋的「類型」，而不是這次的答案原文）。

### 資料（CSV、表格、圖表）
- 欄位齊全、每列格式正確（`command` 讀 CSV 驗證）。
- 數字加總或換算正確（`command` 重新計算比對）。
- 圖檔存在且非空（`file_exists` + `command`：`test -s chart.png`）。
- 圖表標題、單位清楚（`llm`）。

### 程式
- 測試通過（`command`：`python3 -m pytest -q` 或專案的測試指令）。
- 沒有改到不該改的檔案（`file_not_regex` 或 `command` 比對）。
- 程式碼可讀、有處理錯誤（`llm`）。

### 查證／研究
- 每個結論附來源（`file_regex` 找連結格式）。
- 來源與結論相符（`llm`，關鍵）。

## 流程健康
- `status` is done。
- `efficiency`：依原題的步數抓 2 倍左右的上限；失敗步數 ≤ 3。

## 權重建議
正確性 30–40、完整性 15–25、格式／可讀性 10–15、流程健康 10–15、安全性 10–20。總和不必是 100，SinoBee 會換算。
