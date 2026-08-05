"""Placeholder workflow：電子簽名／簽章。

意圖路由判定為 sign 時觸發。目前尚無後端實作（簽章服務串接），先回一個友善提示；
之後接真正的電子簽名流程時，把 _run 改成呼叫對應服務即可。
"""
from .base import Workflow


def _run(query: str, keywords: list | None = None) -> str:
    return (
        "「電子簽名／簽章」功能正在規劃中，暫時還沒開放。\n"
        "之後會支援：上傳檔案 → 選擇簽章位置 → 套用電子簽章 → 下載已簽署檔案。\n"
        "目前如果只是想查「簽名規定／要用哪套系統」，可以直接問我，我會幫你查公司知識庫。"
    )


WORKFLOW = Workflow(
    name="sign",
    description="對檔案進行電子簽名／簽章（規劃中）。",
    triggers=["簽名", "簽章", "電子簽章"],
    run=_run,
)
