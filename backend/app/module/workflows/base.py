"""Workflow 基底。

與 skill 不同：skill 是「模型自己決定要不要呼叫的 tool」；
workflow 是「router 依意圖直接觸發、不經模型決策的既定流程（RPA 式）」，
跑完直接產生回應。內部可呼叫共用的 Tool 層（skills）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Workflow:
    name: str
    description: str
    triggers: list[str]                 # 關鍵字（語意服務不可用時的退回比對）
    run: Callable[[str], str]           # 輸入使用者訊息，回傳最終回應文字
    examples: list[str] = field(default_factory=list)  # 意圖範例句（語意比對優先用這個）
