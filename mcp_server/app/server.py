"""獨立 MCP tool server（Streamable HTTP）——把原本 backend 內建的 skill
（calculator/text_stats/weather/knowledge_search）搬出來變成獨立服務，另外
加了 create_excel/create_word/create_ppt 三個文件產生 tool。

backend 用 MCP client 連這裡取得 tool、呼叫執行；knowledge_search 額外靠
X-Emp-Id 這個 HTTP header（不是 tool 參數）帶「目前登入者」的 emp_id，
確保「用誰的權限查」這件事只能由後端依登入者身分決定，模型看不到、也改不動
（見 app/tools/knowledge_search.py 的說明）。

create_excel/create_word/create_ppt 都是用結構化參數（表格資料、公式字串、
圖表定義、段落/投影片內容）驅動 openpyxl/python-docx/python-pptx，不是讓
模型自己寫程式碼執行，所以不需要額外的 code sandbox。
"""
import os

from dotenv import load_dotenv

load_dotenv()

from mcp.server.mcpserver import MCPServer  # noqa: E402

from app.tools import (calculator, create_excel, create_ppt, create_word,  # noqa: E402
                       knowledge_search, read_url, text_stats, web_search)

server = MCPServer("harness-tools", version="1.0.0")

calculator.register(server)
text_stats.register(server)
knowledge_search.register(server)
create_excel.register(server)
create_word.register(server)
create_ppt.register(server)
web_search.register(server)
read_url.register(server)


def main() -> None:
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "9200"))
    server.run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
