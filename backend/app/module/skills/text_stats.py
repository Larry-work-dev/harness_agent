"""Skill：文字統計。"""
from .base import Skill


def _text_stats(text: str) -> str:
    """統計一段文字的字元數、字數與行數。"""
    chars = len(text)
    words = len(text.split())
    lines = len(text.splitlines()) or 1
    return f"字元數 {chars}、字數 {words}、行數 {lines}"


SKILL = Skill(
    name="text_stats",
    description="統計文字的字元數、字數與行數。",
    when_to_use="使用者要求分析或統計一段文字的長度時。",
    parameters={"text": "要統計的文字內容"},
    run=_text_stats,
)
