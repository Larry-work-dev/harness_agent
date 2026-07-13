"""Skill：查詢天氣（示範用假資料，實務換成真實 API）。"""
from .base import Skill

_DATA = {"台北": "晴，31°C", "台中": "多雲，30°C", "高雄": "午後陣雨，29°C"}


def _get_weather(city: str) -> str:
    """查詢城市目前天氣，輸入中文城市名稱。"""
    return _DATA.get(city, f"查無「{city}」的天氣資料")


SKILL = Skill(
    name="get_weather",
    description="查詢指定城市目前的天氣。",
    when_to_use="使用者詢問某地天氣、氣溫、要不要帶傘等。",
    parameters={"city": "城市名稱（中文），例如 '台北'"},
    run=_get_weather,
)
