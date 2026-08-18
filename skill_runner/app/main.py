"""隔離執行使用者 code 技能的沙盒服務。見 app/runner.py 開頭說明實際隔離程度。

只有 mcp_server 會呼叫這裡（見 skill-net 網路拓樸），用一個共享密鑰
（X-Internal-Token）當輕量防線——網路拓樸才是真正的隔離，密鑰只是順手加的
第二層防線，不是主要防護。
"""
import os

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from app.runner import run_code

app = FastAPI(title="skill_runner")

INTERNAL_TOKEN = os.environ.get("INTERNAL_TOKEN", "")
MAX_TIMEOUT_S = int(os.environ.get("SKILL_RUNNER_MAX_TIMEOUT_S", "30"))


class RunIn(BaseModel):
    language: str
    source: str
    args: dict = {}
    timeout_s: int = 10


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/run")
def run(body: RunIn, x_internal_token: str | None = Header(None)):
    if INTERNAL_TOKEN and x_internal_token != INTERNAL_TOKEN:
        raise HTTPException(401, "invalid internal token")
    if body.language not in ("python", "javascript"):
        raise HTTPException(400, "language 必須是 python 或 javascript")
    timeout_s = max(1, min(body.timeout_s, MAX_TIMEOUT_S))
    return run_code(body.language, body.source, body.args, timeout_s)
