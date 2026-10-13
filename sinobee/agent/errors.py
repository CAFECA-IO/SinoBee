"""Turn provider/tool exceptions into a short, actionable explanation for the user."""
from __future__ import annotations

import re

RULES = [
    (r"本地模型尚未下載|ollama/\S+ .*model .*not found", "這個本地模型還沒下載",
     "到「設定 → 本地模型」按「下載」，完成後再試。"),
    (r"does not support tools", "這個本地模型不支援工具呼叫，無法操作電腦",
     "到「設定 → 本地模型」改選標示「工具」的模型。"),
    (r"^ollama/\S+ network error", "本地模型服務沒有在執行",
     "到「設定 → 本地模型」按「啟動本地服務」，或確認 Ollama 已開啟。"),
    (r"thought_signature", "Gemini 要求回傳思考簽章（thought_signature），舊版 SinoBee 沒有處理",
     "已在新版修正：重建容器後重試即可（docker compose up -d --build）。"),
    (r"HTTP 401|invalid[_ ]api[_ ]key|unauthorized|API key not valid|PERMISSION_DENIED|HTTP 403",
     "API 金鑰無效、過期或沒有這個模型的權限", "到「設定 → API 金鑰」重新貼上金鑰，並按「測試連線」。"),
    (r"HTTP 404|model.*not found|does not exist|NOT_FOUND", "找不到這個模型名稱",
     "到「設定」按「載入可用模型清單」挑選正確名稱。"),
    (r"HTTP 429|rate.?limit|quota|RESOURCE_EXHAUSTED|insufficient_quota", "超過供應商的速率限制或額度",
     "稍後再試，或到供應商後台確認額度；也可以在設定加一個備援模型。"),
    (r"context.{0,20}(length|window)|too many tokens|maximum context|prompt is too long",
     "對話內容超過模型的上下文長度", "開一個新對話，或改用上下文較長的模型。"),
    (r"model not configured|no model selected", "還沒有選擇模型", "到「設定」選擇主要模型並填入金鑰。"),
    (r"network error|ConnectError|Timeout|timed out|Name or service not known|HTTP 50[234]",
     "連不到模型服務（網路或供應商暫時故障）", "確認網路與供應商狀態後重試；可設定備援模型自動切換。"),
    (r"HTTP 400|INVALID_ARGUMENT|invalid_request", "模型服務拒絕了這次請求",
     "通常是模型與 SinoBee 的格式不相容；請把下方詳細內容回報給開發者，或先改用其他模型。"),
]


def explain_error(err: Exception | str) -> dict:
    detail = str(err)
    for pattern, summary, hint in RULES:
        if re.search(pattern, detail, re.I):
            break
    else:
        summary, hint = "執行時發生未預期的錯誤", "可以重試一次；若持續發生，請把詳細內容回報給開發者。"
    m = re.match(r"^([\w.-]+/[\w.:-]+)\s", detail)
    return {"summary": summary, "hint": hint, "detail": detail[:3000], "model": m.group(1) if m else None}
