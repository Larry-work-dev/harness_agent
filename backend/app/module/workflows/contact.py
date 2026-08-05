"""Placeholder workflow：查詢員工聯絡資訊（電話／分機／信箱／部門）。

意圖路由判定為 contact 時觸發，keywords 會帶入要查的員工編號／姓名／email。
目前尚無員工通訊錄資料源，先回友善提示並回顯抓到的查詢對象；之後接上員工通訊錄
（db_api 或公司 HR 系統）時，把 _run 改成依 keywords 查詢即可。
"""
from .base import Workflow


def _run(query: str, keywords: list | None = None) -> str:
    who = "、".join(keywords) if keywords else ""
    target = f"你要查的對象：{who}\n" if who else ""
    return (
        "「員工聯絡資訊查詢」功能正在規劃中，暫時還沒接上公司通訊錄。\n"
        f"{target}"
        "之後會支援：用員工編號／姓名／email 查詢電話、分機、信箱與所屬部門。"
    )


WORKFLOW = Workflow(
    name="contact",
    description="查詢員工聯絡資訊：電話／分機／信箱／部門（規劃中）。",
    triggers=["分機", "聯絡方式", "電話", "信箱"],
    run=_run,
)
