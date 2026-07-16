"""對話核心：新版路由（查路由表 + Orchestrator 複合任務）+ 兩層記憶，SSE 串流。

流程（對應 routing_flow mermaid）：
  敏感 → 限本地模型（單一生成）
  手動指定 → 用指定模型（單一生成）
  否則（自動）：
    命中意圖 → Workflow
    開放式 → 便宜 LLM 分類：
       單一任務 → 查路由表取最佳模型 → 生成
       複合任務 → Orchestrator 拆解 → sub-agents 逐一查表執行 → 組裝
"""
import json
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import create_embedder, create_model, profile_spec
from app.module import agent_config as cfg
from app.module import db_client as db
from app.module.deps import current_user, require_conversation
from app.module.harness import Harness
from app.module.workflows import get_workflow
from app.services import memory, orchestrator, routing

router = APIRouter()


class ChatRequest(BaseModel):
    conversation_id: str
    message: str


def actual_model_name(decision) -> str:
    if decision.get("spec"):
        return decision["spec"].get("model") or "?"
    return profile_spec(decision.get("profile"))["model"] or "?"


def resolve_override(user, model_str):
    """把對話選定的 model 字串解析成 {profile|spec, label}。"""
    if model_str.startswith("profile:"):
        name = model_str.split(":", 1)[1]
        return {"profile": name, "label": name}
    if model_str.startswith("gateway:"):
        mid = model_str.split(":", 1)[1]
        return {"spec": cfg.model_spec(mid), "label": mid}
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
    embedder = create_embedder()   # 語意意圖比對 + 記憶召回共用

    # 1) 決策：敏感 > 手動 > 自動（意圖 / 開放式查表）
    sensitive = routing.detect_sensitive(req.message)
    manual = conv.get("mode") == "manual" and conv.get("model") not in (None, "", "auto")
    if sensitive:
        mid = cfg.local_default()
        decision = {"mode": "generate", "profile": None, "spec": cfg.model_spec(mid),
                    "reason": "含敏感資料，限本地模型", "label": mid}
    elif manual:
        r = resolve_override(user, conv["model"])
        decision = {"mode": "generate", "profile": r.get("profile"), "spec": r.get("spec"),
                    "reason": "手動指定：" + r["label"], "label": r["label"]}
    else:
        d = routing.route(req.message, embedder=embedder)
        decision = {**d, "spec": None, "label": d.get("workflow") or "路由中"}

    print(f"[chat] mode={conv.get('mode')!r} model={conv.get('model')!r} → decision={decision}", flush=True)

    def sse(o):
        return f"data: {json.dumps(o, ensure_ascii=False)}\n\n"

    history_msgs = db.list_messages(req.conversation_id)
    is_first = not history_msgs
    db.add_message(req.conversation_id, "user", req.message)
    if is_first:
        db.rename_conversation(req.conversation_id, req.message[:30])

    # ---- 單一任務生成（sensitive / manual / auto單一 共用）----
    def run_single(claude):
        summary, recent = memory.build_context(req.conversation_id)
        recent = recent[:-1] if recent else recent
        memory_text = memory.recall(embedder, user["id"], req.message)
        hist = ([{"role": "system", "content": "以下是先前對話的摘要：\n" + summary}] if summary else []) + recent
        model = create_model(profile=decision.get("profile"), spec=decision.get("spec"), temperature=0.0)
        harness = Harness(model)
        final_content = None; collected = {}
        for ev in harness.run(req.message, history=hist, memory_context=memory_text, extra_system=claude):
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

    # ---- 複合任務：Orchestrator 拆解 → sub-agents 執行 → 組裝 ----
    def run_composite(subtasks, claude):
        yield sse({"type": "routing", "mode": "composite",
                   "model": f"複合任務 {len(subtasks)} 步", "actual_model": None,
                   "reason": "Orchestrator 拆解並分派 sub-agents"})
        results = []; prior = ""
        for sub in subtasks:
            primary, _fb = cfg.primary_model(sub["task_type"])
            yield sse({"type": "skill_call", "skill": sub["task_type"],
                       "args": {"desc": sub["desc"], "model": primary}})
            mid, out = orchestrator.run_subtask(sub, prior, claude)
            yield sse({"type": "skill_result", "skill": sub["task_type"], "result": out})
            results.append({"task_type": sub["task_type"], "output": out})
            prior += f"\n[{sub['task_type']}] {out}"
        final = orchestrator.assemble(req.message, results, claude)
        db.add_message(req.conversation_id, "assistant", final)
        yield sse({"type": "final", "content": final})
        try:
            learned = memory.extract_and_store(
                create_model(spec=cfg.model_spec(cfg.local_default())),
                embedder, user["id"], req.message, final)
            if learned:
                yield sse({"type": "memory_saved", "items": learned})
        except Exception:
            pass

    def event_stream():
        claude = cfg.claude_md()
        mode = decision["mode"]
        try:
            # 開放式：先用便宜 LLM 分類（單一 / 複合）
            if mode == "auto_route":
                plan = orchestrator.classify(req.message)
                if plan["composite"]:
                    yield from run_composite(plan["subtasks"], claude)
                    yield sse({"type": "done"}); return
                tt = plan["task_type"]
                primary, _fb = cfg.primary_model(tt)
                decision["spec"] = cfg.model_spec(primary)
                decision["label"] = primary
                decision["reason"] = f"查路由表：{tt} → {primary}"
                mode = "generate"

            actual = decision["label"] if mode == "generate" else None
            yield sse({"type": "routing", "mode": mode, "model": decision["label"],
                       "actual_model": actual, "reason": decision["reason"]})

            if mode == "workflow":
                wf = get_workflow(decision["workflow"])
                result = wf.run(req.message) if wf else "找不到對應的流程。"
                db.add_message(req.conversation_id, "assistant", result)
                yield sse({"type": "final", "content": result})
                yield sse({"type": "done"}); return

            yield from run_single(claude)
        except Exception as e:  # noqa: BLE001
            yield sse({"type": "error", "message": str(e)})
        yield sse({"type": "done"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
