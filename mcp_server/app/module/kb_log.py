"""KB search 專用的檔案 log：把每次知識庫檢索「模型／後端帶進來的 filter 參數」
跟「檢索出來的結果」完整寫成一行 JSON，一天一個 .jsonl 檔。

跟 app/module/logs.py 的分工：logs.py 是給人看的 stdout 訊息（docker compose
logs mcp_server 看得到），會被 log driver 輪替掉、也不方便機器解析；這裡是要
留存、事後追查用的結構化紀錄——尤其是 knowledge_search_plain 那條路，
filter_criteria 是模型自己填的（可能填錯、亂填、漏填），出事時得能回頭確認
「當時模型到底帶了什麼、因此撈到什麼」。

落點是容器內的 KB_LOG_DIR（預設 /app/log），docker-compose 把它掛到
mcp_server/log，所以在 host 上直接看那個目錄就好。

一切失敗都吞掉：寫 log 絕不能讓檢索本身掛掉（fail-open）。
用 KB_LOG_ENABLED=false 可以整個關掉。
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

from app.module.logs import get as get_logger

log = get_logger("kb_log")

KB_LOG_ENABLED = os.environ.get("KB_LOG_ENABLED", "true").strip().lower() != "false"
KB_LOG_DIR = Path(os.environ.get("KB_LOG_DIR", "/app/log"))

# tool 是同步函式、由 server 的 thread pool 執行，多個檢索可能同時回來；
# 用一把鎖把「開檔 → 寫一整行 → 關檔」框起來，避免兩筆紀錄交錯在同一行。
_lock = threading.Lock()


def _log_path(now: datetime) -> Path:
    return KB_LOG_DIR / f"kb_search-{now:%Y-%m-%d}.jsonl"


def write(record: dict) -> None:
    """append 一筆紀錄。ts 由這裡補上，呼叫端不用管。"""
    if not KB_LOG_ENABLED:
        return
    now = datetime.now().astimezone()
    line = json.dumps(
        {"ts": now.isoformat(timespec="seconds"), **record},
        ensure_ascii=False,
        default=str,  # 不認得的型別（例如 DB 撈出來的 Decimal/datetime）轉字串，不要炸掉
    )
    try:
        with _lock:
            KB_LOG_DIR.mkdir(parents=True, exist_ok=True)
            with _log_path(now).open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as e:  # noqa: BLE001
        log.warning("寫入 KB search log 失敗：%s", e)
