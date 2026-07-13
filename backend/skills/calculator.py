"""Skill：計算機。"""
from .base import Skill


def _calculate(expression: str) -> str:
    """計算數學算式，只接受數字與 + - * / ( ) 符號。"""
    allowed = set("0123456789+-*/(). ")
    if not set(expression) <= allowed:
        return "錯誤：算式含有不允許的字元"
    try:
        return str(eval(expression, {"__builtins__": {}}, {}))
    except Exception as e:  # noqa: BLE001
        return f"計算錯誤：{e}"


SKILL = Skill(
    name="calculator",
    description="計算數學算式並回傳結果。",
    when_to_use="使用者需要做算術或數值運算時。",
    parameters={"expression": "算式字串，例如 '(12 + 8) * 3'"},
    run=_calculate,
)
