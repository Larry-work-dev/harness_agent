"""db_api 進入點：掛上各資源 router。建表由 Alembic 負責。"""
from fastapi import FastAPI

from app.router import (conversations, doc_chunks, memories, messages,
                        model_profiles, permissions, users, workspaces)

app = FastAPI(title="db_api")
for m in (users, workspaces, conversations, messages, memories, model_profiles, doc_chunks, permissions):
    app.include_router(m.router)
