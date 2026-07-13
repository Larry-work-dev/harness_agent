"""backend —— 主邏輯：認證流程、對話、兩層記憶編排。

不直接連 DB：所有存取走 db_client → db_api。
不直接管向量儲存：embedding 在這裡算好，把向量交給 db_api。
"""
import json

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import auth
import db_client as db
import memory
from config import create_embedder, create_model
from harness import Harness
from skills import load_skills

app = FastAPI(title="Agent Harness API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---- 認證 / 授權相依 -------------------------------------------------
def current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "缺少登入憑證")
    user = auth.user_from_token(authorization.split(" ", 1)[1])
    if not user:
        raise HTTPException(401, "登入憑證無效")
    return user


def require_member(workspace_id: int, user) -> None:
    if not db.is_member(workspace_id, user["id"]):
        raise HTTPException(403, "你不是這個 workspace 的成員")


def require_conversation(conversation_id: str, user) -> dict:
    conv = db.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(404, "找不到對話")
    require_member(conv["workspace_id"], user)
    return conv


# ---- 請求模型 --------------------------------------------------------
class Credentials(BaseModel):
    username: str
    password: str

class WorkspaceIn(BaseModel):
    name: str

class MemberIn(BaseModel):
    username: str
    role: str = "member"

class ConversationIn(BaseModel):
    title: str | None = None

class ChatRequest(BaseModel):
    conversation_id: str
    message: str


# ---- 認證 ------------------------------------------------------------
@app.post("/auth/register")
def register(c: Credentials):
    try:
        return {"token": auth.register(c.username, c.password)}
    except ValueError as e:
        raise HTTPException(400, str(e))

@app.post("/auth/login")
def login(c: Credentials):
    try:
        return {"token": auth.login(c.username, c.password)}
    except ValueError as e:
        raise HTTPException(401, str(e))


# ---- workspace -------------------------------------------------------
@app.get("/workspaces")
def get_workspaces(user=Depends(current_user)):
    return db.list_workspaces(user["id"])

@app.post("/workspaces")
def new_workspace(body: WorkspaceIn, user=Depends(current_user)):
    return db.create_workspace(body.name, user["id"])

@app.post("/workspaces/{wid}/members")
def add_member(wid: int, body: MemberIn, user=Depends(current_user)):
    require_member(wid, user)                       # 需為成員才能邀人
    target = db.get_user_by_name(body.username)
    if not target:
        raise HTTPException(404, "找不到該使用者")
    db.add_member(wid, target["id"], body.role)
    return {"ok": True}

@app.get("/workspaces/{wid}/conversations")
def get_conversations(wid: int, user=Depends(current_user)):
    require_member(wid, user)
    return db.list_conversations(wid)

@app.post("/workspaces/{wid}/conversations")
def new_conversation(wid: int, body: ConversationIn, user=Depends(current_user)):
    require_member(wid, user)
    return db.create_conversation(wid, body.title or "新對話")


# ---- 對話 / 訊息 -----------------------------------------------------
@app.get("/conversations/{cid}/messages")
def get_messages(cid: str, user=Depends(current_user)):
    require_conversation(cid, user)
    return db.list_messages(cid)

@app.delete("/conversations/{cid}")
def remove_conversation(cid: str, user=Depends(current_user)):
    require_conversation(cid, user)
    db.delete_conversation(cid)
    return {"ok": True}


# ---- 長期記憶（綁 user）----------------------------------------------
@app.get("/memories")
def get_memories(user=Depends(current_user)):
    return db.list_memories(user["id"])

@app.delete("/memories/{mid}")
def remove_memory(mid: int, user=Depends(current_user)):
    db.delete_memory(mid, user["id"])
    return {"ok": True}


# ---- skills ----------------------------------------------------------
@app.get("/skills")
def list_skills():
    return [{"name": s.name, "description": s.description,
             "when_to_use": s.when_to_use, "parameters": s.parameters} for s in load_skills()]


# ---- 對話（核心，兩層記憶）------------------------------------------
@app.post("/chat")
def chat(req: ChatRequest, user=Depends(current_user)):
    require_conversation(req.conversation_id, user)

    model = create_model()
    embedder = create_embedder()

    # 短期記憶：滾動摘要 + 最近幾輪（在寫入新訊息前先取）
    summary, recent = memory.build_context(req.conversation_id)
    is_first = not recent and not summary
    # 長期記憶：語意召回與這句最相關的幾條
    memory_text = memory.recall(embedder, user["id"], req.message)

    db.add_message(req.conversation_id, "user", req.message)
    if is_first:
        db.rename_conversation(req.conversation_id, req.message[:30])

    history = []
    if summary:
        history.append({"role": "system", "content": "以下是先前對話的摘要：\n" + summary})
    history += recent

    harness = Harness(model)

    def sse(o): return f"data: {json.dumps(o, ensure_ascii=False)}\n\n"

    def event_stream():
        final_content = None
        collected = {}
        try:
            for ev in harness.run(req.message, history=history, memory_context=memory_text):
                if ev["type"] == "skill_result" and ev.get("sources"):
                    for s in ev["sources"]:
                        collected[s["n"]] = s
                if ev["type"] == "final":
                    final_content = ev["content"]
                yield sse(ev)

            if final_content is not None:
                sources = [collected[n] for n in sorted(collected)]
                db.add_message(req.conversation_id, "assistant", final_content, sources or None)
                memory.maybe_summarize(model, req.conversation_id)     # 短期：折疊舊訊息
                learned = memory.extract_and_store(                    # 長期：萃取新事實
                    model, embedder, user["id"], req.message, final_content)
                if learned:
                    yield sse({"type": "memory_saved", "items": learned})
        except Exception as e:  # noqa: BLE001
            yield sse({"type": "error", "message": str(e)})
        yield sse({"type": "done"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
