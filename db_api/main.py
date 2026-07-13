"""db_api —— 唯一連 Postgres 的服務，只做 CRUD / 查詢，不含任何主邏輯。

backend 透過 HTTP 呼叫這裡；embedding 由 backend 算好後把向量傳進來，
這裡只負責存取與 pgvector 相似度查詢。此服務應只在內部網路開放。
"""
from fastapi import FastAPI
from pydantic import BaseModel

import store

app = FastAPI(title="db_api")


@app.on_event("startup")
def _startup():
    store.init_db()


# ---- 請求模型 --------------------------------------------------------
class UserIn(BaseModel):
    username: str
    password_hash: str
    salt: str

class SessionIn(BaseModel):
    token: str
    user_id: int

class WorkspaceIn(BaseModel):
    name: str
    owner_id: int

class MemberIn(BaseModel):
    workspace_id: int
    user_id: int
    role: str = "member"

class ConversationIn(BaseModel):
    workspace_id: int
    title: str = "新對話"

class RenameIn(BaseModel):
    title: str

class SummaryIn(BaseModel):
    summary: str
    summary_until_id: int

class MessageIn(BaseModel):
    conversation_id: str
    role: str
    content: str
    sources: list | None = None

class MemoryIn(BaseModel):
    user_id: int
    content: str
    embedding: list[float] | None = None

class MemorySearchIn(BaseModel):
    user_id: int
    embedding: list[float]
    k: int = 5


# ---- 使用者 / session ------------------------------------------------
@app.post("/users")
def create_user(u: UserIn):
    return {"id": store.create_user(u.username, u.password_hash, u.salt)}

@app.get("/users/by-name")
def user_by_name(username: str):
    return store.get_user_by_name(username)

@app.get("/users/by-token")
def user_by_token(token: str):
    return store.get_user_by_token(token)

@app.post("/sessions")
def create_session(s: SessionIn):
    store.create_session(s.token, s.user_id); return {"ok": True}


# ---- workspace -------------------------------------------------------
@app.post("/workspaces")
def create_workspace(w: WorkspaceIn):
    return store.create_workspace(w.name, w.owner_id)

@app.get("/workspaces")
def list_workspaces(user_id: int):
    return store.list_workspaces(user_id)

@app.post("/workspaces/members")
def add_member(m: MemberIn):
    store.add_member(m.workspace_id, m.user_id, m.role); return {"ok": True}

@app.get("/workspaces/is-member")
def is_member(workspace_id: int, user_id: int):
    return {"member": store.is_member(workspace_id, user_id)}


# ---- 對話 / 訊息 -----------------------------------------------------
@app.post("/conversations")
def create_conversation(c: ConversationIn):
    return store.create_conversation(c.workspace_id, c.title)

@app.get("/conversations")
def list_conversations(workspace_id: int):
    return store.list_conversations(workspace_id)

@app.get("/conversations/{cid}")
def get_conversation(cid: str):
    return store.get_conversation(cid)

@app.post("/conversations/{cid}/rename")
def rename_conversation(cid: str, body: RenameIn):
    store.rename_conversation(cid, body.title); return {"ok": True}

@app.post("/conversations/{cid}/summary")
def set_summary(cid: str, body: SummaryIn):
    store.set_summary(cid, body.summary, body.summary_until_id); return {"ok": True}

@app.delete("/conversations/{cid}")
def delete_conversation(cid: str):
    store.delete_conversation(cid); return {"ok": True}

@app.post("/messages")
def add_message(m: MessageIn):
    store.add_message(m.conversation_id, m.role, m.content, m.sources); return {"ok": True}

@app.get("/messages")
def list_messages(conversation_id: str):
    return store.list_messages(conversation_id)

@app.get("/messages/after")
def list_messages_after(conversation_id: str, after_id: int):
    return store.list_messages_after(conversation_id, after_id)


# ---- 長期記憶 --------------------------------------------------------
@app.post("/memories")
def add_memory(m: MemoryIn):
    store.add_memory(m.user_id, m.content, m.embedding); return {"ok": True}

@app.get("/memories")
def list_memories(user_id: int):
    return store.list_memories(user_id)

@app.post("/memories/search")
def search_memories(q: MemorySearchIn):
    return store.search_memories(q.user_id, q.embedding, q.k)

@app.delete("/memories/{mid}")
def delete_memory(mid: int, user_id: int):
    store.delete_memory(mid, user_id); return {"ok": True}
