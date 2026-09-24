"""加碼快速評估介面:服務埠徑算攤薄成本 + AI 給 適合/謹慎/不適合 結論。"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from src.modules.research.api import insights
from src.platform.persistence.database import SessionLocal


class _FakeAIClient:
    def __init__(self, reply: str):
        self._reply = reply

    async def chat(self, system_prompt, user_content, temperature=0.3):
        return self._reply


def _run_eval(monkeypatch, req, reply="結論: 適合\n理由:\n- 攤薄明顯\n風險: 大盤轉弱"):
    monkeypatch.setattr(insights, "get_configured_failover_client", lambda db, mid=None: _FakeAIClient(reply))

    async def _empty(*a, **k):
        return ""

    monkeypatch.setattr(insights, "fetch_realtime_context", _empty)
    monkeypatch.setattr(insights, "_fetch_fundamental_context", _empty)
    monkeypatch.setattr(insights, "fetch_technical_context", _empty)
    monkeypatch.setattr(insights, "_fetch_message_context", _empty)
    db = SessionLocal()
    try:
        return asyncio.run(insights.add_position_eval(req, db))
    finally:
        db.close()


def test_add_position_eval_computes_diluted_cost(monkeypatch):
    """加碼 100@8 到 100@10 的持倉:攤薄後成本 9.0(↓10%),並帶 AI 結論。"""
    req = insights.AddPositionEvalRequest(
        symbol="600519", market="CN",
        current_quantity=100, current_cost=10,
        add_quantity=100, add_price=8,
    )
    res = _run_eval(monkeypatch, req)
    assert res["new_cost"] == 9.0
    assert round(res["dilute_pct"], 1) == 10.0
    assert res["total_quantity"] == 200
    assert res["action"] == "加碼"
    assert res["verdict"] == "適合"


def test_build_position_when_empty(monkeypatch):
    """空倉時為建倉:成本=加碼價,攤薄為 0。"""
    req = insights.AddPositionEvalRequest(
        symbol="600519", market="CN",
        current_quantity=0, current_cost=0,
        add_quantity=100, add_price=8,
    )
    res = _run_eval(monkeypatch, req)
    assert res["new_cost"] == 8.0
    assert res["dilute_pct"] == 0
    assert res["action"] == "建倉"


def test_verdict_parse_not_confused_by_substring():
    """'不適合' 含 '適合',解析必須先長後短,不能誤判為 '適合'。"""
    assert insights._parse_verdict("結論: 不適合\n理由: ...") == "不適合"
    assert insights._parse_verdict("結論: 謹慎") == "謹慎"
    assert insights._parse_verdict("結論: 適合") == "適合"
    assert insights._parse_verdict("看不出來") == "未知"


def test_zero_add_quantity_rejected_by_schema():
    """加碼股數必須 > 0(schema 層攔截)。"""
    with pytest.raises(ValidationError):
        insights.AddPositionEvalRequest(
            symbol="600519", add_quantity=0, add_price=8,
        )
