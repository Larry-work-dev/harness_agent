"""對話核心：路由決策 + 兩層記憶 + harness，SSE 串流。"""
import json
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import create_embedder, create_model
from app.module import db_client as db
from app.module.deps import current_user, require_conversation
from app.module.harness import Harness
from app.module.workflows import get_workflow
from app.services import memory, routing

router = APIRouter()


class ChatRequest(BaseModel):
    conversation_id: str
    message: str


def resolve_override(user, model_str):
    """把對話選定的 model 字串解析成 {profile|spec, label}。"""
    if model_str.startswith("profile:"):
        name = model_str.split(":", 1)[1]
        return {"profile": name, "label": name}
    if model_str.startswith("gateway:"):
        mid = model_str.split(":", 1)[1]
        spec = {"base_url": os.environ.get("LLM_BASE_URL", ""), "model": mid,
                "api_key": os.environ.get("LLM_API_KEY", "sk-noauth")}
        return {"spec": spec, "label": mid}
    if model_str.startswith("custom:"):
        pid = int(model_str.split(":", 1)[1])
        p = db.get_model_profile(pid, user["id"])
        if not p:
            raise HTTPException(404, "找不到自訂模型")
        return {"spec": {"base_url": p["base_url"], "model": p["model"], "api_key": p.get("api_key")},
                "label": p["name"]}
    return {"profile": None, "label": model_str}


@router.post("/chat")
def chat(req: ChatRequest, user=Depends(current_user)):
    conv = require_conversation(req.conversation_id, user)

    # 1) 路由決策：敏感守則最優先，其次看對話 manual 設定，否則自動路由
    sensitive = routing.detect_sensitive(req.message)
    manual = conv.get("mode") == "manual" and conv.get("model") not in (None, "", "auto")
    if sensitive:
        decision = {"mode": "generate", "profile": "local", "spec": None,
                    "reason": "含敏感資料，限本地模型", "label": "local"}
    elif manual:
        r = resolve_override(user, conv["model"])
        decision = {"mode": "generate", "profile": r.get("profile"), "spec": r.get("spec"),
                    "reason": "手動指定：" + r["label"], "label": r["label"]}
    else:
        d = routing.route(req.message)
        decision = {**d, "spec": None, "label": d.get("profile") or d.get("workflow")}

    def sse(o):
        return f"data: {json.dumps(o, ensure_ascii=False)}\n\n"

    history_msgs = db.list_messages(req.conversation_id)
    is_first = not history_msgs
    db.add_message(req.conversation_id, "user", req.message)
    if is_first:
        db.rename_conversation(req.conversation_id, req.message[:30])

    def event_stream():
        yield sse({"type": "routing", "mode": decision["mode"],
                   "model": decision["label"], "reason": decision["reason"]})
        try:
            if decision["mode"] == "workflow":
                wf = get_workflow(decision["workflow"])
                result = wf.run(req.message) if wf else "找不到對應的流程。"
                db.add_message(req.conversation_id, "assistant", result)
                yield sse({"type": "final", "content": result})
                yield sse({"type": "done"}); return

            summary, recent = memory.build_context(req.conversation_id)
            recent = recent[:-1] if recent else recent
            embedder = create_embedder()
            memory_text = memory.recall(embedder, user["id"], req.message)
            hist = ([{"role": "system", "content": "以下是先前對話的摘要：\n" + summary}] if summary else []) + recent

            model = create_model(profile=decision.get("profile"), spec=decision.get("spec"))
            harness = Harness(model)

            final_content = None; collected = {}
            for ev in harness.run(req.message, history=hist, memory_context=memory_text):
                if ev["type"] == "skill_result" and ev.get("sources"):
                    for s in ev["sources"]:
                        collected[s["n"]] = s
                if ev["type"] == "final":
                    final_content = ev["content"]
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
