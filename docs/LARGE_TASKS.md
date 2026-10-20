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

## 待辦

- 階段 2：U4 逾時不結束任務、U5 模型錯誤自動續跑（`work.auto_resume_model_max`，每天 3 次，等 1／5／15 分鐘；失敗卡片提示一次設定備援）、U7 技能大檔規範、U6 失敗報告列出已寫檔案。
- 實驗 E7（降低思考對視覺品質）、E6（寫檔步驟用模型最高值）。
