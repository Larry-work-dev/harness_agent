"""引用標記（[FileID]）的格式定義與清理。

knowledge_search / web_search / read_url 這類檢索工具回傳的每段內容前面都帶一個
方括號代號，系統提示要求模型把它照抄到句尾當來源標註；orchestrator.cited_sources()
再依這些標記篩出「答案真的引用到」的來源。

放在 module 層是因為兩邊都要用：harness（module）在送出 final 事件前清理，
orchestrator（services）用同一份格式定義去比對來源。格式只定義一次，不要兩邊各寫
一份正則然後慢慢走鐘。
"""
from __future__ import annotations

import re

# 合格的引用標記：RAG 服務 metadata 的 FileID（32 碼 hex 或含 dash 的 UUID）。
# 方括號內外加 \s* 是因為實測模型偶爾會多打空白（例如 "[ b04d50a0...]"），
# 沒有這個容錯，來源會整批被判定「沒有引用」而濾光。
CITATION_RE = re.compile(r"\[\s*([0-9a-fA-F-]{8,40})\s*\]")

# 句尾那顆方括號。模型偶爾會把「句尾標註來源」這條指示套用到根本沒有 FileID 的
# 工具結果上，硬湊出 [ProjectCode: PDP3-...]、[查無資料。] 這種假標記——它們不符合
# CITATION_RE，不會被當成來源，但會留在答案裡變成雜訊。
#
# 只鎖定「句尾」是刻意的：內文裡的中括號（程式碼的 arr[0]、引文、[註]）要留著。
# 代價是句尾剛好有 [註]。這種寫法也會被清掉——在有引用機制的前提下，句尾的方括號
# 幾乎都是模型在嘗試標來源，這個取捨划算。
_TRAILING_BRACKET_RE = re.compile(r"\[[^\[\]]{1,60}\](?=\s*(?:[。．.!?！？]|$|\n))")


def strip_bogus_citations(text: str) -> str:
    """清掉句尾那些不符合 FileID 格式的假引用標記，合格的引用原樣保留。

    系統提示已經講明引用規則只適用檢索類工具，但那是「請模型配合」、不保證；
    這裡做確定性的清除，讓結構化資料庫查詢的答案不會帶著假標記送到前端。
    """
    if not text:
        return text
    return _TRAILING_BRACKET_RE.sub(
        lambda m: m.group(0) if CITATION_RE.fullmatch(m.group(0)) else "", text)
