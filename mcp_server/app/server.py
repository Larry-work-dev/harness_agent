"""獨立 MCP tool server（Streamable HTTP）——把原本 backend 內建的 skill
（calculator/text_stats/weather/knowledge_search）搬出來變成獨立服務。

backend 用 MCP client 連這裡取得 tool、呼叫執行；knowledge_search 額外靠
X-Emp-Id 這個 HTTP header（不是 tool 參數）帶「目前登入者」的 emp_id，
確保「用誰的權限查」這件事只能由後端依登入者身分決定，模型看不到、也改不動
（見 app/tools/knowledge_search.py 的說明）。
"""
import os

from dotenv import load_dotenv

load_dotenv()

from mcp.server.mcpserver import MCPServer  # noqa: E402

from app.tools import calculator, knowledge_search, text_stats, weather  # noqa: E402

server = MCPServer("harness-tools", version="1.0.0")

calculator.register(server)
text_stats.register(server)
weather.register(server)
knowledge_search.register(server)


def main() -> None:
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "9200"))
    server.run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
