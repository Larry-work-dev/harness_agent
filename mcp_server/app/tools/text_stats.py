"""Tool：文字統計。"""


def _text_stats(text: str) -> str:
    """統計一段文字的字元數、字數與行數。"""
    chars = len(text)
    words = len(text.split())
    lines = len(text.splitlines()) or 1
    return f"字元數 {chars}、字數 {words}、行數 {lines}"


def register(server) -> None:
    @server.tool(
        name="text_stats",
        description="統計文字的字元數、字數與行數。何時使用：使用者要求分析或統計一段文字的長度時。",
    )
    def text_stats(text: str) -> str:
        """要統計的文字內容"""
        return _text_stats(text)
