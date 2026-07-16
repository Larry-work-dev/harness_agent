"""backend 進入點：掛上各 router。"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.router import (auth, chat, conversations, memories, models, skills,
                        uploads, workspaces)

app = FastAPI(title="Agent Harness API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

for m in (auth, workspaces, conversations, memories, skills, models, uploads, chat):
    app.include_router(m.router)
