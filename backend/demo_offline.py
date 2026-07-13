import sys
sys.path.insert(0, ".")
from langchain_core.messages import AIMessage
from harness import Harness

class FakeModel:
    def __init__(self): self.turn = 0
    def bind_tools(self, tools):
        self.tool_names = [t.name for t in tools]; return self
    def invoke(self, messages):
        self.turn += 1
        if self.turn == 1:
            return AIMessage(content="", tool_calls=[
                {"name": "get_weather", "args": {"city": "台北"}, "id": "a"},
                {"name": "calculator", "args": {"expression": "(12+8)*3"}, "id": "b"},
            ])
        results = [m.content for m in messages if getattr(m, "type", "") == "tool"]
        return AIMessage(content=f"台北{results[0]}，(12+8)*3 = {results[1]}。")

h = Harness(FakeModel())
print("自動載入的 skills：", [s.name for s in h.skills])
print("harness 綁定的 tools：", [t.name for t in h.tools])
print("\n--- 執行事件串流 ---")
for ev in h.run("台北天氣如何？順便算 (12+8)*3"):
    print(ev)
