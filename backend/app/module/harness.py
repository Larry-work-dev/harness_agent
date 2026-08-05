"""Agent Harness — 包在 agent 外層的執行框架。

負責：
  1. 跟獨立的 mcp_server（Streamable HTTP）要 tool 清單，把用途注入系統提示
  2. （若 gateway 支援）把 tool 綁進模型，組出 agent 迴圈（LangGraph）
  3. step budget 限制迴圈次數
  4. 對外發出結構化事件（skill_call / skill_result / final / error）

tool-calling 已經搬到獨立的 mcp_server 服務（見 app/module/mcp_client.py 與
專案根目錄的 mcp_server/），這裡不再有 in-process 的 skill 實作，只負責跟
mcp_server 要 tool、綁進模型、跑 LangGraph 迴圈。

TOOLS_ENABLED 開關
------------------
有些 OpenAI 相容 gateway（如未加 --enable-auto-tool-choice 的 vLLM）不支援
tool calling，送 tools 會回 400。此時把環境變數 TOOLS_ENABLED 設為 false（預設），
harness 就不綁 tools、純文字生成，對話仍可運作（但不會呼叫 tool）。
gateway 支援 tool calling 後設 TOOLS_ENABLED=true 即恢復完整 agent 流程。

model 抽成參數，方便替換成假模型做離線測試。
"""
from __future__ import annotations

import os
from typing import Annotated, Any, Iterator, TypedDict, cast

from langchain_core.messages import BaseMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from app.module import mcp_client


def tools_enabled() -> bool:
    return os.environ.get("TOOLS_ENABLED", "false").lower() == "true"


class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


class Harness:
    def __init__(self, model: Any, max_steps: int = 8, emp_id: str | None = None):
        # emp_id 綁進每次呼叫 mcp_server 的 HTTP header（不是 tool 參數）：
        # 確保「用誰的權限查」只能由後端依登入者身分決定，模型看不到、也改不動
        # （見 mcp_client.build_tools 與 mcp_server/app/tools/knowledge_search.py）。
        self.tools = mcp_client.build_tools(emp_id)
        self.max_steps = max_steps
        self.use_tools = tools_enabled()
        # 只有 gateway 支援 tool calling 時才綁 tools，否則純文字生成
        self.model = model.bind_tools(self.tools) if self.use_tools else model
        self.graph = self._build()

    def system_prompt(self) -> str:
        if not self.use_tools:
            return "你是一個繁體中文助理，請清楚、準確地回答使用者。"
        catalog = "\n".join(f"- {t.name}：{t.description}" for t in self.tools)
        return (
            "你是一個執行框架（harness）中的繁體中文助理。\n"
            "你可以呼叫以下工具完成任務：\n"
            f"{catalog}\n"
            "需要時直接呼叫對應工具，取得結果後再用中文回答使用者。\n"
            "當你根據 knowledge_search 檢索到的資料回答時，"
            "請在每一個句子的結尾、句號之前，原樣照抄該段落開頭方括號內的 FileID"
            "（檢索結果每段最前面的 [FileID]，直接照抄、不要自己編號、不要省略字元、"
            "方括號內外都不要加空格）"
            "標註該句依據的來源；沒有依據的句子則不標註。"
        )

    def _build(self):
        def call_model(state: State):
            return {"messages": [self.model.invoke(state["messages"])]}

        b = StateGraph(State)
        b.add_node("agent", call_model)
        b.add_edge(START, "agent")

        if not self.use_tools:
            # 無 tools：agent 產生回覆後直接結束
            b.add_edge("agent", END)
            return b.compile()

        def should_continue(state: State):
            last = state["messages"][-1]
            return "tools" if getattr(last, "tool_calls", None) else END

        b.add_node("tools", ToolNode(self.tools))
        b.add_conditional_edges("agent", should_continue, ["tools", END])
        b.add_edge("tools", "agent")
        return b.compile()

    def run(
        self,
        user_message: str,
        history: list[dict] | None = None,
        memory_context: str | None = None,
        extra_system: str | None = None,
        images: list[dict] | None = None,
    ) -> Iterator[dict]:
        system = self.system_prompt()
        if extra_system:
            system += "\n\n" + extra_system
        if memory_context:
            system += (
                "\n\n關於這位使用者你已知道的事（可作為回答的參考，但不要生硬複述）：\n"
                + memory_context
            )
        messages: list[dict] = [{"role": "system", "content": system}]
        if history:
            messages += history
        if images:
            # 視覺訊息：文字 + 圖片區塊（OpenAI 相容格式）
            content = [{"type": "text", "text": user_message}]
            for img in images:
                content.append({"type": "image_url", "image_url": {"url": img["url"]}})
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": user_message})

        for chunk in self.graph.stream(
            cast(State, {"messages": messages}),
            stream_mode="updates",
            config={"recursion_limit": self.max_steps * 2},
        ):
            for node, update in chunk.items():
                msg = update["messages"][-1]
                if node == "agent":
                    calls = getattr(msg, "tool_calls", None)
                    if calls:
                        for c in calls:
                            yield {"type": "skill_call", "skill": c["name"], "args": c["args"]}
                    elif msg.content:
                        yield {"type": "final", "content": msg.content}
                elif node == "tools":
                    for m in update["messages"]:
                        event = {"type": "skill_result", "skill": m.name, "result": m.content}
                        # artifact 形狀依 tool 而定：knowledge_search 是來源清單（list），
                        # create_excel/word/ppt 是產生的檔案（dict：filename/mime/data_base64）。
                        # 見 mcp_client.build_tools 的 _SOURCES_TOOLS / _FILE_TOOLS。
                        artifact = getattr(m, "artifact", None)
                        if isinstance(artifact, list) and artifact:
                            event["sources"] = artifact
                        elif isinstance(artifact, dict) and artifact:
                            event["file"] = artifact
                        yield event
