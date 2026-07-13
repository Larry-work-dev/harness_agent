"""Skill 的統一介面。

一個 skill 不只是裸 tool：它會自我描述（description + when_to_use），
讓 harness 能把它列進系統提示、被前端探索，並統一轉成可執行的 tool。
把新的 .py 檔放進這個資料夾、匯出一個 SKILL 物件，就會被自動載入。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from langchain_core.tools import StructuredTool


@dataclass
class Skill:
    name: str
    description: str            # 這個 skill 做什麼
    when_to_use: str            # 什麼情況下該用（會注入系統提示）
    parameters: dict[str, str]  # 給人看 / 前端顯示用的參數說明
    run: Callable[..., Any]     # 實際執行的函式
    returns_artifact: bool = False  # True 表示 run 回傳 (給模型的文字, 給前端的結構化資料)

    def as_tool(self) -> StructuredTool:
        """轉成 LLM 可呼叫的 tool。"""
        desc = f"{self.description} 何時使用：{self.when_to_use}"
        if self.returns_artifact:
            # 工具回傳 (content, artifact)：content 給模型讀，artifact 走 ToolMessage.artifact 給前端
            return StructuredTool.from_function(
                func=self.run, name=self.name, description=desc,
                response_format="content_and_artifact",
            )
        return StructuredTool.from_function(func=self.run, name=self.name, description=desc)
