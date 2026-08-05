"""Placeholder workflow：公司內部資訊系統／軟體的開發提案。

意圖路由判定為 system_dev 時觸發（僅限「資訊系統／軟體」的新開發提案，實體產品／
機構／工程設計會被歸到 kbchat）。目前尚無提案收件後端，先回友善提示並回顯提案內容；
之後接上提案單／工單系統時，把 _run 改成寫入該系統即可。
"""
from .base import Workflow


def _run(query: str, keywords: list | None = None) -> str:
    topics = "、".join(keywords) if keywords else ""
    summary = f"提案重點：{topics}\n" if topics else ""
    return (
        "已收到你的「資訊系統／軟體開發提案」，這個功能的自動收件流程正在規劃中，"
        "暫時還沒接上提案／工單系統。\n"
        f"{summary}"
        "之後會支援：填寫提案 → 自動建立工單 → 指派給 IT 評估。\n"
        "在那之前，也可以先把需求整理好寄給 IT 窗口。"
    )


WORKFLOW = Workflow(
    name="system_dev",
    description="公司內部資訊系統／軟體的新開發提案（規劃中）。",
    triggers=["開發提案", "新系統", "新功能", "數位化"],
    run=_run,
)
