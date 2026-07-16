"""對話核心：新版路由（查路由表 + Orchestrator 複合任務）+ 附件（圖片/文件）+ 兩層記憶，SSE。

附件流程：
  圖片 → 強制走視覺模型（本地 Qwen3-VL），多模態訊息生成（OCR/圖面理解由此打通）
  文件 → ephemeral：本回合抽字注入；RAG：上傳時已 embedding 進 doc_chunks，開放式問答時檢索注入
"""
import json
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.config import create_embedder, create_model, profile_spec
from app.module import agent_config as cfg
from app.module import attachments as att
from app.module import db_client as db
from app.module.deps import current_user, require_conversation
from app.module.harness import Harness
from app.module.workflows import get_workflow
from app.services import memory, orchestrator, routing

router = APIRouter()


class ChatRequest(BaseModel):
    conversation_id: str
    message: str
    attachments: list[dict] = []


def actual_model_name(decision) -> str:
    if decision.get("spec"):
        return decision["spec"].get("model") or "?"
    return profile_spec(decision.get("profile"))["model"] or "?"


def resolve_override(user, model_str):
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
    embedder = create_embedder()

    atts = req.attachments or []
    images = [a for a in atts if a.get("kind") == "image"]
    docs = [a for a in atts if a.get("kind") == "doc"]

    # 1) 決策：圖片 > 敏感 > 手動 > 自動
    sensitive = routing.detect_sensitive(req.message)
    manual = conv.get("mode") == "manual" and conv.get("model") not in (None, "", "auto")
    if images:
        mid = cfg.local_default()
        decision = {"mode": "vision", "profile": None, "spec": cfg.model_spec(mid),
                    "reason": "含圖片，使用視覺模型", "label": mid}
    elif sensitive:
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

    print(f"[chat] mode={conv.get('mode')!r} imgs={len(images)} docs={len(docs)} → {decision}", flush=True)

    def sse(o):
        return f"data: {json.dumps(o, ensure_ascii=False)}\n\n"

    history_msgs = db.list_messages(req.conversation_id)
    is_first = not history_msgs
    db.add_message(req.conversation_id, "user", req.message, attachments=atts or None)
    if is_first:
        db.rename_conversation(req.conversation_id, (req.message or (images and "圖片") or "附件")[:30])

    # 指導原則 + 文件脈絡（ephemeral 本回合抽字 + RAG 先前上傳片段）
    def build_ctx():
        extra = cfg.claude_md()
        ep, total = [], 0
        for a in docs:
            try:
                t = att.extract_text(a["path"])
            except Exception:
                t = ""
            if not t:
                continue
            snip = t[:4000]
            ep.append(f"【本次附件：{a['name']}】\n{snip}")
            total += len(snip)
            if total > 8000:
                break
        rag = []
        try:
            hits = db.search_doc_chunks(user["id"], embedder.embed_query(req.message), k=4)
            rag = [f"[{h['source_name']}] {h['content']}" for h in hits]
        except Exception:
            pass
        if ep:
            extra += "\n\n" + "\n\n".join(ep)
        if rag:
            extra += "\n\n【你先前上傳文件的相關片段】\n" + "\n\n".join(rag)
        return extra

    def run_single(ctx):
        summary, recent = memory.build_context(req.conversation_id)
        recent = recent[:-1] if recent else recent
        memory_text = memory.recall(embedder, user["id"], req.message)
        hist = ([{"role": "system", "content": "以下是先前對話的摘要：\n" + summary}] if summary else []) + recent
        model = create_model(profile=decision.get("profile"), spec=decision.get("spec"), temperature=0.0)
        harness = Harness(model)
        final_content = None; collected = {}
        for ev in harness.run(req.message, history=hist, memory_context=memory_text, extra_system=ctx):
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

    def run_composite(subtasks, ctx):
        yield sse({"type": "routing", "mode": "composite",
                   "model": f"複合任務 {len(subtasks)} 步", "actual_model": None,
                   "reason": "Orchestrator 拆解並分派 sub-agents"})
        results = []; prior = ""
        for sub in subtasks:
            primary, _fb = cfg.primary_model(sub["task_type"])
            yield sse({"type": "skill_call", "skill": sub["task_type"],
                       "args": {"desc": sub["desc"], "model": primary}})
            mid, out = orchestrator.run_subtask(sub, prior, ctx)
            yield sse({"type": "skill_result", "skill": sub["task_type"], "result": out})
            results.append({"task_type": sub["task_type"], "output": out})
            prior += f"\n[{sub['task_type']}] {out}"
        final = orchestrator.assemble(req.message, results, ctx)
        db.add_message(req.conversation_id, "assistant", final)
        yield sse({"type": "final", "content": final})

    def run_vision(ctx):
        mid = decision["label"]
        yield sse({"type": "routing", "mode": "vision", "model": mid,
                   "actual_model": mid, "reason": decision["reason"]})
        blocks = [{"type": "text", "text": req.message or "請看這張圖片並協助我。"}]
        for a in images:
            try:
                blocks.append({"type": "image_url",
                               "image_url": {"url": att.image_data_url(a["path"], a.get("mime", ""))}})
            except Exception:
                continue
        model = create_model(spec=decision["spec"], temperature=0.0)
        out = model.invoke([SystemMessage(content=ctx), HumanMessage(content=blocks)]).content
        db.add_message(req.conversation_id, "assistant", out)
        yield sse({"type": "final", "content": out})
        try:
            learned = memory.extract_and_store(
                create_model(spec=cfg.model_spec(cfg.local_default())),
                embedder, user["id"], req.message or "(圖片)", out)
            if learned:
                yield sse({"type": "memory_saved", "items": learned})
        except Exception:
            pass

    def event_stream():
        try:
            ctx = build_ctx()
            mode = decision["mode"]

            if mode == "vision":
                yield from run_vision(ctx)
                yield sse({"type": "done"}); return

            if mode == "auto_route":
                plan = orchestrator.classify(req.message)
                if plan["composite"]:
                    yield from run_composite(plan["subtasks"], ctx)
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

            yield from run_single(ctx)
        except Exception as e:  # noqa: BLE001
            yield sse({"type": "error", "message": str(e)})
        yield sse({"type": "done"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
