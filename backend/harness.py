"""Agent Harness — 包在 agent 外層的執行框架。

和單純的 ReAct agent 差別在於，harness 負責：
  1. 探索並載入 skills，把它們的用途注入系統提示
  2. 把 skills 綁定成 tools，組出 agent 迴圈（LangGraph）
  3. 用 step budget 限制迴圈次數，避免失控
  4. 執行時對外發出「結構化事件」（可觀測性），前端可即時呈現：
       skill_call   → agent 決定呼叫某個 skill
       skill_result → skill 實際執行完的結果
       final        → 最終回覆
       error        → 發生錯誤

model 抽成參數，方便替換成假模型做離線測試。
"""
from __future__ import annotations

from typing import Annotated, Any, Iterator, TypedDict, cast

from langchain_core.messages import BaseMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from skills import load_skills


class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


class Harness:
    def __init__(self, model: Any, max_steps: int = 8):
        self.skills = load_skills()
        self.tools = [s.as_tool() for s in self.skills]
        self.max_steps = max_steps
        self.model = model.bind_tools(self.tools)
        self.graph = self._build()

    def system_prompt(self) -> str:
        catalog = "\n".join(
            f"- {s.name}：{s.description}（{s.when_to_use}）" for s in self.skills
        )
        return (
            "你是一個執行框架（harness）中的繁體中文助理。\n"
            "你可以呼叫以下 skills 完成任務：\n"
            f"{catalog}\n"
            "需要時直接呼叫對應 skill，取得結果後再用中文回答使用者。\n"
            "當你根據 knowledge_search 檢索到的資料回答時，"
            "請在每一個句子的結尾、句號之前，用 [n] 標註該句依據的來源編號"
            "（n 對應檢索結果中每段前面的編號）；沒有依據的句子則不標註。"
        )

    def _build(self):
        def call_model(state: State):
            return {"messages": [self.model.invoke(state["messages"])]}

        def should_continue(state: State):
            last = state["messages"][-1]
            return "tools" if getattr(last, "tool_calls", None) else END

        b = StateGraph(State)
        b.add_node("agent", call_model)
        b.add_node("tools", ToolNode(self.tools))
        b.add_edge(START, "agent")
        b.add_conditional_edges("agent", should_continue, ["tools", END])
        b.add_edge("tools", "agent")
        return b.compile()

    def run(
        self,
        user_message: str,
        history: list[dict] | None = None,
        memory_context: str | None = None,
    ) -> Iterator[dict]:
        system = self.system_prompt()
        if memory_context:
            system += (
                "\n\n關於這位使用者你已知道的事（可作為回答的參考，但不要生硬複述）：\n"
                + memory_context
            )
        messages: list[dict] = [{"role": "system", "content": system}]
        if history:
            messages += history
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
                        sources = getattr(m, "artifact", None)
                        if sources:
                            event["sources"] = sources
                        yield event
