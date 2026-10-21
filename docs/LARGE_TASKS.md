# 大任務不再中途中斷：架構說明（2026-10-20）

提案文件：Claude Docs〈SinoBee 更新提案：讓複雜大任務不再中途中斷〉。本頁記錄已實作的部分。

## 起因

風力發電機任務（anatomy 技能，gemini-flash-latest）在第 7 步中斷：模型想用一次 `write_file` 寫完整頁，隱藏思考加上整頁內容超過 8192 的輸出上限兩次（`finish=length`，內容為空、工具呼叫遺失），上限加倍後非串流請求 180 秒沒有回應而 ReadTimeout，被誤報為「連不到模型服務」。實驗另外發現：規劃呼叫只給思維模型 1500 tokens，gemini-pro-latest 3 次裡 2 次回不出計畫，所以任務沒有計畫也沒有里程碑。

## 階段 0（c3e8025、f6b13c4）

| 項目 | 位置 |
|---|---|
| 呼叫等待時間依輸出上限：max(180, 60 + 上限 ÷ 50) 秒 | `providers/base.py` `call_timeout` |
| OpenAI 相容直連預設串流（`model.stream`、`providers.<名稱>.stream` 可關）；沒有 index 的工具呼叫分開解析 | `providers/__init__.py`、`openai_compat.py` |
| 截斷的空白回覆：要求分段寫檔（TRUNCATED_NUDGE），記入失敗報告 `empty_replies` | `agent/loop.py`、`agent/failures.py` |
| ReadTimeout 說明改為「模型在時限內沒有回完」 | `agent/errors.py`、`agent/failures.py` |

注意：Gemini 的工具呼叫要整個生成完才送出，串流期間仍可能沉默（實測 100.9 秒），所以串流不能取代分段。

## 階段 1（6e4d908）

### U8 依模型給輸出上限（`providers/limits.py`）
- 最高值：供應商模型 API（Gemini `models.get` 的 `outputTokenLimit`，金鑰放標頭），記在 `model_caps.json`；否則用已查證清單（claude-opus-5-5 128,000、gemini *-latest 65,536）。
- 預設值：`model.max_output_tokens`／`SINOBEE_MAX_OUTPUT_TOKENS`（裁到最高值）；auto 時取最高值的一半，最多 32k，且不低於舊值；未知模型沿用 `model.max_tokens`／`reasoning_max_tokens`。
- 上下文只保留預設值；截斷後下一次給模型最高值（不超過上下文剩餘空間）。
- 設定頁「每次輸出上限」顯示目前值、來源與取捨。

### U1 思維模型呼叫給足上限（`agent/loop.py` `thinking_room`）
- 推理模型的思維呼叫（規劃、請教、重新規劃、預算檢查、事實裁決）至少 8192。
- 規劃被截斷時加大重問一次；仍沒有計畫就在任務中顯示，並記入失敗報告 `plan_failed`、`thinking_skipped`。

### U3 截斷後降低思考（`providers/base.py` `REASONING_EFFORT`）
- 截斷後接下來 `agent.low_effort_steps`（5）次呼叫對推理模型送 `reasoning_effort: low`（實驗 E1：一次寫完整頁 0/8 → 3/3）。
- 不接受的模型由相容層學起來，之後不送。E7（視覺品質）驗證前不預設套用到所有寫檔步驟。

### U2 依元件切區塊（`agent/prompt.py`、`tools/files.py`）
- 規則：骨架中每個元件一個佔位、一次只填一塊、骨架之後不再整檔重寫、單次約 3 萬字元內（E3：最大寫入 32k → 15k 字元）。
- `write_file(append=true)`。
- 單次寫入超過 30,000 字元，或輸出用掉上限 70% 以上時，下一次請求附提醒（不拒絕）。

測試：`tests/test_output_limits.py`、`tests/test_long_output.py`。

## 階段 2（2026-10-21）

### U4 輸出被截斷後的逾時不結束任務（`agent/loop.py`、`providers/base.py` `TOO_LONG`）
- 上一個回覆停在輸出上限（`finish=length`）時，下一次呼叫標為「可能太長」：逾時就不重送同一個請求，也不換備援模型重送。
- 迴圈把這次逾時當成「一次要寫的內容太長」：發分段提示（TRUNCATED_NUDGE）、接下來幾步降低思考，任務繼續；同一回合最多兩次，第三次照常結束。
- 失敗報告記在 `too_long_timeouts`。

### U5 模型暫時錯誤自動續跑（`work/recovery.py`、`agent/errors.py` `transient`、`providers/fallback.py`）
- 暫時性錯誤：網路、供應商 5xx、逾時、429 速率限制；金鑰、模型名稱、400、`insufficient_quota`、上下文超長不算。
- 失敗的對話回合記下 `model_error`；復原檢查依序等 1、5、15 分鐘後用 `[自動續跑]` 續跑（有里程碑時從第一個未完成的里程碑接著做），每段對話每天最多 `work.auto_resume_model_max`（3）次，與程式重啟的 `work.auto_resume_max` 分開計。
- SinoBee.ai 額度用完：等到額度恢復（`retry_at` + 30 秒；沒有時間就 10 分鐘），不計次。
- 之後已有新的對話、使用者停止、錯誤不會自己消失時不續跑，原因寫在回合卡片。
- 對話中顯示「SinoBee 會在 HH:MM 自動續跑（今天第 n/3 次）」。
- 沒有備援模型時，失敗卡片提示一次「加入備援」：優先另一家已有金鑰、同級（快速／平衡／最強）的模型，否則已登入的 SinoBee.ai 中繼；按「不用」後不再提示（`fallback_offer_declined`）。

### U6 失敗報告列出已寫的檔案（`agent/failures.py` `written_files`）
- 這一輪寫入／修改過的每個檔案：路徑、現在的大小（不在了也標出）、寫入次數與成功次數；思維模型的診斷與報告畫面都看得到。

### U7 技能的大檔規範（`skills/largefiles.py`）
- 技能檢查：SKILL.md 承諾產出網頁或大型單一檔案，卻沒寫怎麼分段寫入時，在技能詳情「格式提醒」與 `sinobee skills validate` 標出 `large-file`。
- 這類技能啟用時，SinoBee 在技能內容後面附上自己的 `<sinobee_note>`（先寫骨架、一次填一塊、單次約 3 萬字元）；技能原文不改（anatomy）。
- 內建技能 frontend-design（1.0.1）、engineering-feasibility（1.1.1）在 SKILL.md 寫明分段寫法。

測試：`tests/test_large_tasks_phase2.py`。

## 實驗 E7、E6（2026-10-21）

gemini-flash-latest 用區塊規則建同一個風力發電機頁面，每組 3 次；頁面用評鑑的離線瀏覽器截圖並自動檢查（程式錯誤、會不會動、滑桿是否改變讀數），gemini-pro-latest 看截圖兩兩盲評（左右對調各一次）。腳本：`scripts/exp_e7_e6.py`、`scripts/exp_e7_check.py`。

| 組別 | 完成 | 中位時間 | 中位花費 | 頁面字元（中位） | 控制元件 | 最長單次呼叫 | 截斷 |
|---|---|---|---|---|---|---|---|
| E7 預設思考，上限 32k | 3/3 | 202 秒 | US$0.25 | 57k | 11–14 | 36 秒 | 0 |
| E7 低思考，上限 32k | 2/3 | 113 秒 | US$0.12 | 29k | 1–9 | 26 秒 | 0 |
| E6 上限 65,536＋低思考 | 2/3 | 123 秒 | US$0.13 | 32k | 1–15 | 28 秒 | 0 |
| E6 上限 16k，預設思考 | 3/3 | 207 秒 | US$0.27 | 58k | 9–13 | 37 秒 | 0 |

- 所有頁面都沒有程式錯誤以外的大問題：都會動、滑桿都會改變讀數（低思考有 1 頁有一個 SVG 屬性錯誤）。
- 低思考的頁面約小一半、互動元件少；6 次中有 2 次留下 11–14 個沒填的區塊就宣告完成。
- 盲評：預設思考平均 6.2 分、低思考 5.2 分；3 對中 1 對預設思考兩個順序都勝，2 對兩個順序都判「先看到的那頁」勝（位置偏差，視為平手）。
- 有區塊規則時，單次輸出最多約 4.8k tokens，16k 與 65k 上限都沒有截斷；最長的沉默 37 秒，遠低於呼叫等待時間。

結論：
- **U3 維持現狀**：低思考只在輸出被截斷之後的 5 步使用，不預設套用到寫檔步驟（省一半時間與花費，但頁面較陽春、較常提早收工）。
- **U8 維持現狀**：平常用預設上限（模型最高值的一半，最多 32k），被截斷後才給最高值；有區塊規則時上限不是瓶頸。
- 建議追加：完成檢查把「骨架中還沒填的區塊佔位」當成未完成（低思考的兩次失敗都是這種）。
