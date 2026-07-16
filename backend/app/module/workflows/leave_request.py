"""範例 workflow：請假申請流程（示範既定流程，不經模型生成）。"""
from .base import Workflow


def _run(query: str) -> str:
    # 實務上這裡會呼叫共用 Tool 層（例如查假別、寫入 HR 系統、寄通知信）。
    return (
        "已為你啟動「請假申請」流程：\n"
        "1. 選擇假別與日期\n"
        "2. 系統檢核剩餘時數\n"
        "3. 送出給主管簽核\n"
        "請回覆你要請的假別與起訖日期，我接著幫你送出。"
    )


WORKFLOW = Workflow(
    name="leave_request",
    description="請假申請的既定流程。",
    triggers=["請假", "休假", "特休", "事假", "病假"],
    examples=[
        "我要請假",
        "幫我請三天特休",
        "我想申請病假",
        "下週一我要休假，幫我送假單",
        "我要請事假一天",
        "幫我跑請假流程",
    ],
    run=_run,
)
