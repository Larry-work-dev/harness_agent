"""統一 logging：所有 tool 都走這裡。

用環境變數 LOG_LEVEL 控制詳細程度（DEBUG / INFO / WARNING，預設 INFO）。
輸出到 stdout，docker compose logs mcp_server 看得到。格式：
    HH:MM:SS LEVEL [mcp.<component>] 訊息
"""
from __future__ import annotations

import logging
import os
import sys

_configured = False


def _setup() -> None:
    global _configured
    if _configured:
        return
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    _configured = True
    for noisy in ("httpx", "httpcore", "urllib3", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get(component: str) -> logging.Logger:
    _setup()
    return logging.getLogger("mcp." + component)
