"""動態技能：把使用者自建的 http/code 技能掛成 mcp tool。

ToolManager 本身就是純記憶體的 dict[str, Tool]，add_tool/remove_tool 隨時可
呼叫、不用重啟 mcp_server；db_api 的 skills 表才是唯一真相來源，這裡開機時
（hydrate）跟每次 CRUD 之後（backend 呼叫 /admin/skills/{id}/sync）都會整份
重新跟 db_api 核對一次。

mcp 的 Tool.from_function() 是照 inspect.signature(func, eval_str=True) 產生
JSON schema——inspect.signature() 本來就認 fn.__signature__（有設就直接回傳，
不會再走 eval_str/字串解析，因為 Parameter 裡已經是真正的型別物件），所以可以
動態組一個 __signature__ 塞給 closure，讓沒有具名參數的泛用函式也能生出跟手寫
工具一樣完整的 JSON schema。但要不要注入 Context（ctx）是另一套機制：
find_context_parameter() 讀的是 typing.get_type_hints(fn)，也就是
fn.__annotations__——跟 __signature__ 是兩個獨立屬性，兩個都要設對，
否則 ctx 拿不到（這裡用 ctx 核對呼叫者身分，見 _check_owner）。
"""
from __future__ import annotations

import asyncio
import inspect
import ipaddress
import os
import socket
from typing import Any
from urllib.parse import urlparse

import httpx
from mcp.server.mcpserver.context import Context

from app.module import db_client as db
from app.module.logs import get as get_logger

log = get_logger("dynamic_skills")

_TYPE_MAP = {"str": str, "int": int, "float": float, "bool": bool}

SKILL_RUNNER_URL = os.environ.get("SKILL_RUNNER_URL", "http://skill_runner:8100")
SKILL_RUNNER_TOKEN = os.environ.get("SKILL_RUNNER_TOKEN", "")

# SSRF 防護：http 技能不能打這些主機——都是這包服務自己人，使用者的技能沒有
# 理由需要打得到。deny-list、不是完整證明（例如 DNS rebinding 沒有完全擋掉），
# 但比完全不擋好很多；配合 follow_redirects=False，擋掉「打公開網址、302 轉
# 內部位址」這種最常見的繞法。
_DENY_HOSTNAMES = {"db_api", "backend", "mcp_server", "db", "skill_runner", "localhost"}


def _host_is_blocked(host: str) -> bool:
    if not host or host.lower() in _DENY_HOSTNAMES:
        return True
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False  # 解析不到就讓 httpx 自己去踩失敗，不用在這裡先擋
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return True
    return False


def _build_signature(params: list[dict]) -> tuple[inspect.Signature, dict]:
    """依技能 spec.params 動態組出 inspect.Signature + 對應的 __annotations__，
    讓 func_metadata() 讀出跟手寫具名參數一樣完整的 JSON schema。全部用
    KEYWORD_ONLY——呼叫端本來就是 fn(**kwargs)，用 KEYWORD_ONLY 也不用管
    「必填參數不能排在有預設值的參數後面」那條 Python 簽名規則。"""
    sig_params, annotations = [], {}
    for p in params:
        py_type = _TYPE_MAP.get(p.get("type", "str"), str)
        required = p.get("required", True)
        annotation = py_type if required else (py_type | None)
        default = inspect.Parameter.empty if required else None
        sig_params.append(inspect.Parameter(
            p["name"], inspect.Parameter.KEYWORD_ONLY, annotation=annotation, default=default))
        annotations[p["name"]] = annotation
    sig_params.append(inspect.Parameter("ctx", inspect.Parameter.KEYWORD_ONLY, annotation=Context))
    annotations["ctx"] = Context
    return inspect.Signature(sig_params), annotations


def _describe(skill: dict) -> str:
    """參數名稱/型別/說明摺進純字串的 description——mcp 工具的 JSON schema
    只照函式簽名自動產生，不讀 docstring；比照這個 repo 現有工具（web_search
    的 query、create_excel 的 filename 等扁平參數）本來就沒有逐欄位 schema
    描述、只靠 description 這個慣例，不用為了動態技能另外生一套。"""
    params = skill["spec"].get("params") or []
    lines = [skill["description"]]
    if params:
        lines.append("\n參數：")
        for p in params:
            req = "必填" if p.get("required", True) else "選填"
            lines.append(f"- {p['name']}（{p.get('type', 'str')}，{req}）：{p.get('description', '')}")
    return "\n".join(lines)


def _check_owner(ctx: Context, owner_user_id: int) -> str | None:
    """defense-in-depth：backend 的 build_tools() 已經依 owner 過濾過工具清單，
    理論上不會有別人的呼叫打到這裡——這裡多核對一次呼叫者帶的 X-User-Id header。"""
    caller = (ctx.headers or {}).get("x-user-id")
    if caller is None or caller != str(owner_user_id):
        return f"權限不符：這個技能不屬於呼叫者（caller={caller}）"
    return None


def _run_http_skill(skill: dict, kwargs: dict) -> str:
    spec = skill["spec"]
    url = spec["url"]
    method = spec.get("method", "GET").upper()
    if _host_is_blocked(urlparse(url).hostname or ""):
        return "技能執行失敗：目標主機不允許呼叫。"

    try:
        headers = {k: str(v).format(**kwargs) for k, v in (spec.get("headers_template") or {}).items()}
    except Exception:
        headers = {}
    body = spec.get("body_template")
    if body:
        try:
            body = body.format(**kwargs)
        except Exception:
            pass
    static_values = spec.get("static_values") or {}
    merged = {**kwargs, **static_values}

    try:
        resp = httpx.request(
            method, url,
            params=merged if method == "GET" else None,
            json=merged if method != "GET" and not body else None,
            content=body if body else None,
            headers=headers or None,
            timeout=20, follow_redirects=False,
        )
        return f"HTTP {resp.status_code}\n{resp.text[:8000]}"
    except Exception as e:  # noqa: BLE001
        return f"技能執行失敗：{e}"


def _run_code_skill(skill: dict, kwargs: dict) -> str:
    spec = skill["spec"]
    try:
        resp = httpx.post(
            f"{SKILL_RUNNER_URL}/run",
            json={"language": spec["language"], "source": spec["source"], "args": kwargs, "timeout_s": 10},
            headers={"X-Internal-Token": SKILL_RUNNER_TOKEN},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        return f"技能執行失敗：{e}"
    if data.get("error"):
        return f"執行錯誤：{data['error']}"
    parts = []
    if data.get("stdout"):
        parts.append(f"輸出：\n{data['stdout']}")
    if "result" in data:
        parts.append(f"結果：{data['result']}")
    return "\n\n".join(parts) or "（沒有輸出）"


def build_tool_fn(skill: dict):
    def _fn(**kwargs):
        ctx = kwargs.pop("ctx", None)
        if ctx is not None:
            err = _check_owner(ctx, skill["user_id"])
            if err:
                return err
        if skill["kind"] == "http":
            return _run_http_skill(skill, kwargs)
        return _run_code_skill(skill, kwargs)

    sig, annotations = _build_signature(skill["spec"].get("params") or [])
    _fn.__signature__ = sig
    _fn.__annotations__ = annotations
    _fn.__name__ = f"skill_{skill['id']}"
    return _fn


def register_skill(server, skill: dict) -> None:
    name = f"skill_{skill['id']}"
    try:
        server.remove_tool(name)  # add_tool 對已存在的名字是 no-op（只警告），要先移除才能真的更新
    except Exception:
        pass
    server.add_tool(
        build_tool_fn(skill), name=name, description=_describe(skill),
        meta={"owner_user_id": skill["user_id"], "skill_id": skill["id"], "kind": skill["kind"]},
        structured_output=False,
    )
    log.info("掛上動態技能 %s（kind=%s, owner=%s）", name, skill["kind"], skill["user_id"])


def unregister_skill(server, skill_id: int) -> None:
    try:
        server.remove_tool(f"skill_{skill_id}")
    except Exception:
        pass


_known: dict[int, str] = {}  # skill_id -> updated_at，用來比對要不要重新掛


def sync_all(server) -> None:
    """全量整理：跟 db_api 要目前所有啟用中的 http/code 技能，缺的補上、
    多的（刪除/關閉）拔掉。hydrate()（開機時）跟 admin sync route（backend
    CRUD 後立即推播）都呼叫這個——技能數量在自服務場景不會多到全量整理有
    效能問題，不用為了「只重新整理一個 id」多維護一條查單筆的路徑。"""
    try:
        skills = [s for s in db.list_executable_skills() if s["kind"] in ("http", "code")]
    except Exception as e:  # noqa: BLE001
        log.warning("跟 db_api 拉技能清單失敗(%s)，維持現狀", e)
        return

    current = {s["id"]: s["updated_at"] for s in skills}
    for sid in list(_known):
        if sid not in current:
            unregister_skill(server, sid)
            del _known[sid]
    for s in skills:
        if _known.get(s["id"]) != s["updated_at"]:
            register_skill(server, s)
            _known[s["id"]] = s["updated_at"]


def register_admin_routes(server) -> None:
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    @server.custom_route("/admin/skills/{skill_id}/sync", methods=["POST"])
    async def _sync_one(request: Request) -> JSONResponse:
        sync_all(server)
        return JSONResponse({"ok": True})

    @server.custom_route("/admin/skills/{skill_id}", methods=["DELETE"])
    async def _delete_one(request: Request) -> JSONResponse:
        skill_id = int(request.path_params["skill_id"])
        unregister_skill(server, skill_id)
        _known.pop(skill_id, None)
        return JSONResponse({"ok": True})


def hydrate(server) -> None:
    """開機時跑一次——ToolManager 是純記憶體 dict，重啟就清空，db_api 才是唯一真相來源。
    db_api 這時可能還沒 ready（docker-compose depends_on 只等容器啟動，不等健康），
    失敗就留給後面的 reconcile_loop 重試，不擋 mcp_server 自己的啟動。"""
    sync_all(server)


async def reconcile_loop(server, interval_s: int = 60) -> None:
    """安全網：backend 推播失敗（例如剛好那一刻 mcp_server 在重啟）時，
    最多等這個週期就會自動修正回來。"""
    while True:
        await asyncio.sleep(interval_s)
        sync_all(server)
