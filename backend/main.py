"""backend —— 主邏輯：認證、對話、兩層記憶、模型路由、模型管理。"""
import json

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import auth
import db_client as db
import memory
import router
from config import PROFILES, create_embedder, create_model, list_gateway_models
from harness import Harness
from skills import load_skills
from workflows import get_workflow

app = FastAPI(title="Agent Harness API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "缺少登入憑證")
    user = auth.user_from_token(authorization.split(" ", 1)[1])
    if not user:
        raise HTTPException(401, "登入憑證無效")
    return user

def require_member(workspace_id, user):
    if not db.is_member(workspace_id, user["id"]):
        raise HTTPException(403, "你不是這個 workspace 的成員")

def require_conversation(cid, user):
    conv = db.get_conversation(cid)
    if not conv:
        raise HTTPException(404, "找不到對話")
    require_member(conv["workspace_id"], user)
    return conv


class Credentials(BaseModel):
    username: str; password: str
class WorkspaceIn(BaseModel):
    name: str
class MemberIn(BaseModel):
    username: str; role: str = "member"
class ConversationIn(BaseModel):
    title: str | None = None
class ChatRequest(BaseModel):
    conversation_id: str
    message: str
    model: str | None = None            # 手動覆寫；None/"auto" = 自動路由
class ModelProfileIn(BaseModel):
    name: str; base_url: str; model: str; api_key: str | None = None


# ---- 認證 ----
@app.post("/auth/register")
def register(c: Credentials):
    try: return {"token": auth.register(c.username, c.password)}
    except ValueError as e: raise HTTPException(400, str(e))

@app.post("/auth/login")
def login(c: Credentials):
    try: return {"token": auth.login(c.username, c.password)}
    except ValueError as e: raise HTTPException(401, str(e))


# ---- workspace ----
@app.get("/workspaces")
def get_workspaces(user=Depends(current_user)):
    return db.list_workspaces(user["id"])

@app.post("/workspaces")
def new_workspace(body: WorkspaceIn, user=Depends(current_user)):
    return db.create_workspace(body.name, user["id"])

@app.post("/workspaces/{wid}/members")
def add_member(wid: int, body: MemberIn, user=Depends(current_user)):
    require_member(wid, user)
    target = db.get_user_by_name(body.username)
    if not target: raise HTTPException(404, "找不到該使用者")
    db.add_member(wid, target["id"], body.role); return {"ok": True}

@app.get("/workspaces/{wid}/conversations")
def get_conversations(wid: int, user=Depends(current_user)):
    require_member(wid, user); return db.list_conversations(wid)

@app.post("/workspaces/{wid}/conversations")
def new_conversation(wid: int, body: ConversationIn, user=Depends(current_user)):
    require_member(wid, user); return db.create_conversation(wid, body.title or "新對話")


# ---- 對話 / 訊息 ----
@app.get("/conversations/{cid}/messages")
def get_messages(cid: str, user=Depends(current_user)):
    require_conversation(cid, user); return db.list_messages(cid)

@app.delete("/conversations/{cid}")
def remove_conversation(cid: str, user=Depends(current_user)):
    require_conversation(cid, user); db.delete_conversation(cid); return {"ok": True}


# ---- 記憶 ----
@app.get("/memories")
def get_memories(user=Depends(current_user)):
    return db.list_memories(user["id"])

@app.delete("/memories/{mid}")
def remove_memory(mid: int, user=Depends(current_user)):
    db.delete_memory(mid, user["id"]); return {"ok": True}


# ---- skills ----
@app.get("/skills")
def list_skills():
    return [{"name": s.name, "description": s.description,
             "when_to_use": s.when_to_use, "parameters": s.parameters} for s in load_skills()]


# ---- 模型管理 ----
@app.get("/models")
def models(user=Depends(current_user)):
    """列出可選模型：內建分級 profile、gateway 上的模型、使用者自訂 profile。"""
    return {
        "profiles": PROFILES,                                  # local/cloud/mid/cheap
        "gateway": list_gateway_models(),                      # /v1/models
        "custom": db.list_model_profiles(user["id"]),          # 個人自訂
    }

@app.post("/models/custom")
def add_custom_model(body: ModelProfileIn, user=Depends(current_user)):
    return db.create_model_profile(user["id"], body.name, body.base_url, body.model, body.api_key)

@app.delete("/models/custom/{pid}")
def del_custom_model(pid: int, user=Depends(current_user)):
    db.delete_model_profile(pid, user["id"]); return {"ok": True}


# ---- 手動覆寫解析 ----
def resolve_override(user, model_str):
    """把前端選的 model 字串解析成 {profile|spec, label}。"""
    if model_str.startswith("profile:"):
        name = model_str.split(":", 1)[1]
        return {"profile": name, "label": name}
    if model_str.startswith("gateway:"):
        mid = model_str.split(":", 1)[1]
        import os
        spec = {"base_url": os.environ.get("LLM_BASE_URL", ""), "model": mid,
                "api_key": os.environ.get("LLM_API_KEY", "sk-noauth")}
        return {"spec": spec, "label": mid}
    if model_str.startswith("custom:"):
        pid = int(model_str.split(":", 1)[1])
        p = db.get_model_profile(pid, user["id"])
        if not p: raise HTTPException(404, "找不到自訂模型")
        return {"spec": {"base_url": p["base_url"], "model": p["model"], "api_key": p.get("api_key")},
                "label": p["name"]}
    return {"profile": None, "label": model_str}


# ---- 對話（核心：路由 + 兩層記憶）----
@app.post("/chat")
def chat(req: ChatRequest, user=Depends(current_user)):
    require_conversation(req.conversation_id, user)

    # 1) 路由決策（敏感守則最優先，連手動覆寫都蓋不過）
    sensitive = router.detect_sensitive(req.message)
    override = req.model and req.model not in ("", "auto")
    if sensitive:
        decision = {"mode": "generate", "profile": "local", "spec": None,
                    "reason": "含敏感資料，限本地模型", "label": "local"}
    elif override:
        r = resolve_override(user, req.model)
        decision = {"mode": "generate", "profile": r.get("profile"), "spec": r.get("spec"),
                    "reason": "手動指定：" + r["label"], "label": r["label"]}
    else:
        d = router.route(req.message)
        decision = {**d, "spec": None, "label": d.get("profile") or d.get("workflow")}

    def sse(o): return f"data: {json.dumps(o, ensure_ascii=False)}\n\n"

    # 先存使用者訊息（第一則當標題）
    history_msgs = db.list_messages(req.conversation_id)
    is_first = not history_msgs
    db.add_message(req.conversation_id, "user", req.message)
    if is_first:
        db.rename_conversation(req.conversation_id, req.message[:30])

    def event_stream():
        # 先告知路由結果，前端可顯示
        yield sse({"type": "routing", "mode": decision["mode"],
                   "model": decision["label"], "reason": decision["reason"]})
        try:
            # 2a) 走 workflow：既定流程，不經模型生成
            if decision["mode"] == "workflow":
                wf = get_workflow(decision["workflow"])
                result = wf.run(req.message) if wf else "找不到對應的流程。"
                db.add_message(req.conversation_id, "assistant", result)
                yield sse({"type": "final", "content": result})
                yield sse({"type": "done"}); return

            # 2b) 走生成：兩層記憶 + harness
            summary, recent = memory.build_context(req.conversation_id)
            # 注意：使用者訊息已寫入，需扣掉最後一則避免重複
            recent = recent[:-1] if recent else recent
            embedder = create_embedder()
            memory_text = memory.recall(embedder, user["id"], req.message)
            hist = ([{"role": "system", "content": "以下是先前對話的摘要：\n" + summary}] if summary else []) + recent

            model = create_model(profile=decision.get("profile"), spec=decision.get("spec"))
            harness = Harness(model)

            final_content = None; collected = {}
            for ev in harness.run(req.message, history=hist, memory_context=memory_text):
                if ev["type"] == "skill_result" and ev.get("sources"):
                    for s in ev["sources"]: collected[s["n"]] = s
                if ev["type"] == "final": final_content = ev["content"]
                yield sse(ev)

            if final_content is not None:
                sources = [collected[n] for n in sorted(collected)]
                db.add_message(req.conversation_id, "assistant", final_content, sources or None)
                memory.maybe_summarize(model, req.conversation_id)
                learned = memory.extract_and_store(model, embedder, user["id"], req.message, final_content)
                if learned:
                    yield sse({"type": "memory_saved", "items": learned})
        except Exception as e:  # noqa: BLE001
            yield sse({"type": "error", "message": str(e)})
        yield sse({"type": "done"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
