"""Tool：產生 Excel（.xlsx）檔案。

用結構化參數（rows/公式字串/圖表定義/樣式），不是讓模型自己寫程式碼跑：
- 公式：儲存格內容以 "=" 開頭的字串會被 openpyxl 原樣寫入，Excel 開啟時自己算，
  這裡不執行任何運算。
- 圖表：openpyxl.chart 支援長條/折線/圓餅，用資料範圍字串驅動。
這樣「進階功能」（公式、圖表、樣式）都能在固定 schema 內做到，不需要 sandbox。

參數用 Pydantic model（不是裸 dict/list[dict]）宣告巢狀欄位：MCP 工具的 JSON
schema 只會照函式簽名自動產生，不會讀函式的 docstring——如果只宣告
`sheets: list[dict]`，模型看到的 schema 只知道「一個物件的陣列」，猜不出物件
裡該放什麼欄位，容易亂猜、驗證失敗、重試到 LangGraph 的 recursion limit
（實測發生過）。用 Pydantic model 才能讓每個巢狀欄位的名稱/型別/說明都出現在
schema 裡，模型才填得對。
"""
import base64
import io

from mcp_types import CallToolResult, TextContent
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, PieChart, Reference
from openpyxl.styles import Font
from pydantic import BaseModel, Field

_CHART_TYPES = {"bar": BarChart, "line": LineChart, "pie": PieChart}


class ChartSpec(BaseModel):
    type: str = Field("bar", description="圖表類型：bar（長條）、line（折線）、pie（圓餅）")
    title: str = Field("", description="圖表標題")
    data_range: str = Field(..., description="資料範圍（不含工作表名），例如 'B2:B5'")
    categories_range: str | None = Field(None, description="類別（X 軸標籤）範圍，例如 'A2:A5'")
    anchor: str = Field("E2", description="圖表放置的儲存格位置，例如 'D2'")
    data_has_header: bool = Field(False, description="data_range 第一格是否為標題（欄位名稱）")


class SheetSpec(BaseModel):
    name: str = Field("Sheet1", description="工作表名稱")
    rows: list[list[str | int | float]] = Field(
        default_factory=list,
        description="每一列的儲存格值，由左到右排列（第一列通常是欄位標題）。"
                    "儲存格內容若以 '=' 開頭會被當成 Excel 公式（例如 '=SUM(B2:B3)'），"
                    "Excel 開啟時自己計算，不是這裡先算好結果。",
    )
    header_bold: bool = Field(False, description="是否把第一列（標題列）文字加粗")
    column_widths: dict[str, float] = Field(
        default_factory=dict, description="欄寬設定，key 是欄位字母（例如 'A'），value 是寬度")
    charts: list[ChartSpec] = Field(default_factory=list, description="要加入這個工作表的圖表清單")


def _add_chart(ws, spec: ChartSpec) -> None:
    chart_cls = _CHART_TYPES.get(spec.type, BarChart)
    chart = chart_cls()
    chart.title = spec.title
    data_ref = Reference(ws, range_string=f"{ws.title}!{spec.data_range}")
    chart.add_data(data_ref, titles_from_data=spec.data_has_header)
    if spec.categories_range:
        chart.set_categories(Reference(ws, range_string=f"{ws.title}!{spec.categories_range}"))
    ws.add_chart(chart, spec.anchor)


def _build(sheets: list[SheetSpec]) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for spec in sheets:
        ws = wb.create_sheet(spec.name[:31])
        for r, row in enumerate(spec.rows, 1):
            for c, val in enumerate(row, 1):
                ws.cell(row=r, column=c, value=val)
        if spec.header_bold and spec.rows:
            for c in range(1, len(spec.rows[0]) + 1):
                ws.cell(row=1, column=c).font = Font(bold=True)
        for col, width in spec.column_widths.items():
            ws.column_dimensions[col].width = width
        for chart_spec in spec.charts:
            _add_chart(ws, chart_spec)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def register(server) -> None:
    @server.tool(
        name="create_excel",
        description="產生 Excel（.xlsx）檔案，支援多工作表、公式（儲存格內容以 = 開頭）、"
                    "基本圖表（長條/折線/圓餅）與欄寬/粗體樣式。"
                    "何時使用：使用者要求把資料整理成 Excel、報表、清單、對照表。",
    )
    def create_excel(filename: str, sheets: list[SheetSpec]) -> CallToolResult:
        name = filename if filename.endswith(".xlsx") else filename + ".xlsx"
        data = _build(sheets)
        return CallToolResult(
            content=[TextContent(type="text", text=f"已產生 Excel 檔案：{name}")],
            structured_content={
                "filename": name,
                "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "data_base64": base64.b64encode(data).decode(),
            },
        )
