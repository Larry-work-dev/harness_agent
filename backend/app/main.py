"""backend 進入點：掛上各 router + 請求 log middleware。"""
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.module.logs import get as get_logger
from app.router import (auth, chat, conversations, memories, models, skills,
                        uploads, workspaces)

log = get_logger("http")
app = FastAPI(title="Agent Harness API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """記錄每個 route 的方法、路徑、狀態、耗時。"""
    t0 = time.time()
    log.info("→ %s %s", request.method, request.url.path)
    try:
        resp = await call_next(request)
    except Exception as e:  # noqa: BLE001
        log.exception("✗ %s %s raised %s", request.method, request.url.path, e)
        raise
    log.info("← %s %s %s (%.0fms)", request.method, request.url.path,
             resp.status_code, (time.time() - t0) * 1000)
    return resp


for m in (auth, workspaces, conversations, memories, skills, models, uploads, chat):
    app.include_router(m.router)
