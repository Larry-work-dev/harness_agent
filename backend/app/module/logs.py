"""統一 logging：所有 route 與關鍵 function 都走這裡。

用環境變數 LOG_LEVEL 控制詳細程度（DEBUG / INFO / WARNING，預設 INFO）。
輸出到 stdout，docker compose logs backend 看得到。格式：
    HH:MM:SS LEVEL [harness.<component>] 訊息
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


def get(component: str) -> logging.Logger:
    _setup()
    return logging.getLogger("harness." + component)
