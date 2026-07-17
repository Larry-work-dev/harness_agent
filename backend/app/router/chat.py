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
from app.module.logs import get as get_logger
from app.module.workflows import get_workflow
from app.services import memory, orchestrator, routing

router = APIRouter()
log = get_logger("chat")


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

    # 1) 決策：手動 > 圖片(白名單) > 敏感 > 自動
    #    manual＝使用者說了算：照送指定模型（含圖也送給它）、不 fallback、失敗回真錯誤。
    #    auto＝系統保護：敏感走本地、圖走視覺白名單（含 fallback）。
    sensitive = routing.detect_sensitive(req.message)
    manual = conv.get("mode") == "manual" and conv.get("model") not in (None, "", "auto")
    if manual:
        r = resolve_override(user, conv["model"])
        if images:
            spec = r.get("spec") or profile_spec(r.get("profile"))
            decision = {"mode": "vision", "profile": None, "spec": spec, "fallback_spec": None,
                        "task_type": "manual", "label": r["label"],
                        "reason": f"手動指定（含圖，照送不 fallback）：{r['label']}"}
        else:
            decision = {"mode": "generate", "profile": r.get("profile"), "spec": r.get("spec"),
                        "reason": "手動指定：" + r["label"], "label": r["label"]}
    elif images:
        itask = orchestrator.classify_image_task(req.message)
        if sensitive:
            primary, fallback = cfg.primary_vision_model(itask, local_only=True)
            reason = f"含圖片且敏感（{itask}）→ 本地視覺模型 {primary}"
        else:
            primary, fallback = cfg.primary_vision_model(itask)
            reason = f"含圖片（{itask}）→ 依路由表+視覺白名單 {primary}"
        decision = {"mode": "vision", "profile": None, "spec": cfg.model_spec(primary),
                    "fallback_spec": cfg.model_spec(fallback), "task_type": itask,
                    "reason": reason, "label": primary}
    elif sensitive:
        mid = cfg.local_default()
        decision = {"mode": "generate", "profile": None, "spec": cfg.model_spec(mid),
                    "reason": "含敏感資料，限本地模型", "label": mid}
    else:
        d = routing.route(req.message, embedder=embedder)
        decision = {**d, "spec": None, "label": d.get("workflow") or "路由中"}

    log.info("decision: user=%s conv_mode=%s imgs=%d docs=%d → mode=%s label=%s (%s)",
             user["id"], conv.get("mode"), len(images), len(docs),
             decision["mode"], decision.get("label"), decision.get("reason"))

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
            if hits:
                log.info("doc-RAG: 檢索到 %d 筆先前上傳文件片段", len(hits))
        except Exception as e:  # noqa: BLE001
            log.warning("doc-RAG: 檢索失敗(%s)", e)
        if ep:
            log.info("doc-ephemeral: 本回合注入 %d 份附件文字", len(ep))
            extra += "\n\n" + "\n\n".join(ep)
        if rag:
            extra += "\n\n【你先前上傳文件的相關片段】\n" + "\n\n".join(rag)
        return extra

    def run_single(ctx):
        """給『手動指定模型』與『敏感資料強制本地模型』用：decision 已經定死 spec/profile，
        不經過 Planner/Worker/Critic（敏感資料絕不能被送進分類器或依路由表挑到雲端模型）。"""
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

    def run_plan(subtasks, ctx):
        """Planner 已把需求拆成 >=1 個 subtask；逐一用 Harness（Worker）執行 + Critic 審核，
        單一 subtask 時直接用其輸出，多個 subtask 時用 assemble() 組裝成一份回覆。"""
        yield sse({"type": "routing",
                   "mode": "composite" if len(subtasks) > 1 else "single",
                   "model": f"複合任務 {len(subtasks)} 步" if len(subtasks) > 1 else subtasks[0]["task_type"],
                   "actual_model": None,
                   "reason": (f"Planner 拆解 {len(subtasks)} 步"
                              if len(subtasks) > 1 else f"單一任務：{subtasks[0]['task_type']}")})

        # 記憶只在整個 turn 處理一次（不分單一/複合）
        summary, recent = memory.build_context(req.conversation_id)
        recent = recent[:-1] if recent else recent
        memory_text = memory.recall(embedder, user["id"], req.message)
        hist = ([{"role": "system", "content": "以下是先前對話的摘要：\n" + summary}] if summary else []) + recent

        critic_on = os.environ.get("CRITIC_ENABLED", "true").lower() == "true"
        max_retries = int(os.environ.get("CRITIC_MAX_RETRIES", "1"))

        results = []; prior = ""; all_sources: dict = {}

        for i, sub in enumerate(subtasks):
            is_first = (i == 0)
            primary, fallback = cfg.primary_model(sub["task_type"])
            harness = Harness(create_model(spec=cfg.model_spec(primary), temperature=0.0))

            # 檢索 safety net：只在沒有 tool-calling 能力時才做，且只做一次、不隨 retry 重做
            retrieved, retrieved_sources = "", []
            if not harness.use_tools and sub["task_type"] in orchestrator.RETRIEVAL_TASK_TYPES:
                q = sub["desc"] if not prior else f"{sub['desc']}（脈絡：{prior[:400]}）"
                retrieved, retrieved_sources = orchestrator.retrieve(q)
                log.info("subtask[%s] 檢索公司知識庫: 命中 %d 筆來源", sub["task_type"], len(retrieved_sources))

            retry_feedback, attempt, tried_fallback = None, 0, False
            while True:
                extra_system = orchestrator.build_worker_prompt(sub, prior, ctx, retrieved, retry_feedback)
                events_yielded, final_text, attempt_sources = 0, None, []
                try:
                    for ev in harness.run(sub["desc"], history=hist if is_first else None,
                                          memory_context=memory_text if is_first else None,
                                          extra_system=extra_system):
                        events_yielded += 1
                        if ev["type"] == "final":
                            final_text = ev["content"]; continue  # 吞掉，不當整輪 SSE final 轉發
                        if ev["type"] == "skill_result" and ev.get("sources"):
                            attempt_sources.extend(ev["sources"])
                        ev = {**ev, "subtask_index": i, "attempt": attempt}
                        yield sse(ev)
                except Exception as e:  # noqa: BLE001
                    log.warning("subtask[%s] Harness 執行例外(%s)", sub["task_type"], e)
                    if events_yielded == 0 and not tried_fallback and fallback != primary:
                        # 還沒吐出任何事件：整個換 fallback 模型重來一次（只試一次，不算 critic attempt）
                        tried_fallback = True
                        try:
                            harness = Harness(create_model(spec=cfg.model_spec(fallback), temperature=0.0))
                            continue
                        except Exception:
                            pass
                    yield sse({"type": "error", "subtask_index": i,
                               "message": f"子任務「{sub['task_type']}」執行失敗：{e}"})
                    final_text = None

                if final_text is None:
                    final_text = "（此子任務執行失敗）"
                    break

                if not critic_on:
                    break
                verdict = orchestrator.review(sub, final_text, retrieved_sources + attempt_sources, req.message)
                yield sse({"type": "critic", "task_type": sub["task_type"], "subtask_index": i,
                           "verdict": "pass" if verdict["pass"] else "retry", "reason": verdict["reason"]})
                if verdict["pass"] or attempt >= max_retries:
                    break
                retry_feedback, attempt = verdict["feedback"], attempt + 1

            results.append({"task_type": sub["task_type"], "output": final_text})
            prior += f"\n[{sub['task_type']}] {final_text}"
            for s in retrieved_sources + attempt_sources:
                all_sources.setdefault((s.get("name"), s.get("url")), s)

        final = orchestrator.assemble(req.message, results, ctx) if len(subtasks) > 1 else results[0]["output"]

        db.add_message(req.conversation_id, "assistant", final, list(all_sources.values()) or None)
        yield sse({"type": "final", "content": final})

        mem_model = create_model(spec=cfg.model_spec(cfg.local_default()))
        memory.maybe_summarize(mem_model, req.conversation_id)
        learned = memory.extract_and_store(mem_model, embedder, user["id"], req.message, final)
        if learned:
            yield sse({"type": "memory_saved", "items": learned})

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
        specs = [decision["spec"]]
        fb = decision.get("fallback_spec")
        if fb and fb.get("model") != decision["spec"].get("model"):
            specs.append(fb)
        out, err = None, None
        for sp in specs:
            try:
                out = create_model(spec=sp, temperature=0.0).invoke(
                    [SystemMessage(content=ctx), HumanMessage(content=blocks)]).content
                if sp is not specs[0]:
                    log.info("vision: 主模型失敗，改用 fallback %s", sp.get("model"))
                break
            except Exception as e:  # noqa: BLE001
                err = e
                log.warning("vision: 模型 %s 失敗(%s)", sp.get("model"), e)
        if out is None:
            # 手動模式或全部失敗：把真正的錯誤丟回給使用者
            yield sse({"type": "error",
                       "message": f"模型 {specs[-1].get('model')} 呼叫失敗：{err}"})
            return
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
                log.info("→ 走視覺路徑（%d 張圖）模型=%s", len(images), decision["label"])
                yield from run_vision(ctx)
                yield sse({"type": "done"}); return

            if mode == "auto_route":
                subtasks = orchestrator.plan(req.message)
                log.info("→ 走 Planner→Worker→Critic（%d 步）", len(subtasks))
                yield from run_plan(subtasks, ctx)
                yield sse({"type": "done"}); return

            yield sse({"type": "routing", "mode": mode, "model": decision["label"],
                       "actual_model": decision["label"] if mode == "generate" else None,
                       "reason": decision["reason"]})

            if mode == "workflow":
                log.info("→ 走 workflow: %s", decision["workflow"])
                wf = get_workflow(decision["workflow"])
                result = wf.run(req.message) if wf else "找不到對應的流程。"
                db.add_message(req.conversation_id, "assistant", result)
                yield sse({"type": "final", "content": result})
                yield sse({"type": "done"}); return

            log.info("→ 一般生成（手動/敏感強制模型），模型=%s", decision.get("label"))
            yield from run_single(ctx)
        except Exception as e:  # noqa: BLE001
            log.exception("event_stream 例外: %s", e)
            yield sse({"type": "error", "message": str(e)})
        yield sse({"type": "done"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
