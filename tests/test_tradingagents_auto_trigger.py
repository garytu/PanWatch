"""TradingAgents 聯動觸發單測。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.modules.automation.tradingagents import operations as auto_trigger


def _make_agent(raw_config: dict):
    agent = MagicMock()
    agent.raw_config = raw_config
    return agent


def test_no_change_pct_skips():
    """漲跌幅缺失 → 不觸發"""
    ok, reason = auto_trigger.should_auto_trigger("601238", None)
    assert ok is False
    assert "無漲跌幅" in reason


def test_disabled_in_config_skips():
    """auto_trigger.enabled=false → 不觸發"""
    with patch("src.modules.automation.tradingagents.operations.SessionLocal") as session_factory:
        db = MagicMock()
        session_factory.return_value = db
        db.query.return_value.filter.return_value.first.return_value = _make_agent(
            {"auto_trigger": {"enabled": False, "change_pct_threshold": 5.0}}
        )
        ok, reason = auto_trigger.should_auto_trigger("601238", 8.0)
    assert ok is False
    assert "未啟用" in reason


def test_no_agent_config_skips():
    """tradingagents agent 未註冊 → 不觸發"""
    with patch("src.modules.automation.tradingagents.operations.SessionLocal") as session_factory:
        db = MagicMock()
        session_factory.return_value = db
        db.query.return_value.filter.return_value.first.return_value = None
        ok, reason = auto_trigger.should_auto_trigger("601238", 8.0)
    assert ok is False


def test_below_threshold_skips():
    """漲跌幅低於閾值 → 不觸發"""
    with patch("src.modules.automation.tradingagents.operations.SessionLocal") as session_factory:
        db = MagicMock()
        session_factory.return_value = db
        # query(AgentConfig) 第一次返回 agent
        db.query.return_value.filter.return_value.first.return_value = _make_agent(
            {"auto_trigger": {"enabled": True, "change_pct_threshold": 5.0}}
        )
        ok, reason = auto_trigger.should_auto_trigger("601238", 3.0)
    assert ok is False
    assert "未達閾值" in reason


def test_above_threshold_within_cooldown_skips():
    """達閾值但 24h 內已觸發 → 不觸發"""
    with patch("src.modules.automation.tradingagents.operations.SessionLocal") as session_factory, \
         patch("src.modules.automation.tradingagents.operations._within_cooldown", return_value=True), \
         patch("src.modules.automation.tradingagents.operations._budget_allows", return_value=True):
        db = MagicMock()
        session_factory.return_value = db
        db.query.return_value.filter.return_value.first.return_value = _make_agent(
            {"auto_trigger": {"enabled": True, "change_pct_threshold": 5.0, "cooldown_hours": 24}}
        )
        ok, reason = auto_trigger.should_auto_trigger("601238", 8.0)
    assert ok is False
    assert "冷卻" in reason


def test_above_threshold_budget_exceeded_skips():
    """達閾值但月度預算已用完 → 不觸發"""
    with patch("src.modules.automation.tradingagents.operations.SessionLocal") as session_factory, \
         patch("src.modules.automation.tradingagents.operations._within_cooldown", return_value=False), \
         patch("src.modules.automation.tradingagents.operations._budget_allows", return_value=False):
        db = MagicMock()
        session_factory.return_value = db
        db.query.return_value.filter.return_value.first.return_value = _make_agent(
            {"auto_trigger": {"enabled": True, "change_pct_threshold": 5.0}}
        )
        ok, reason = auto_trigger.should_auto_trigger("601238", 8.0)
    assert ok is False
    assert "預算" in reason


def test_above_threshold_all_pass_triggers():
    """達閾值 + 不在冷卻 + 預算足 → 觸發"""
    with patch("src.modules.automation.tradingagents.operations.SessionLocal") as session_factory, \
         patch("src.modules.automation.tradingagents.operations._within_cooldown", return_value=False), \
         patch("src.modules.automation.tradingagents.operations._budget_allows", return_value=True):
        db = MagicMock()
        session_factory.return_value = db
        db.query.return_value.filter.return_value.first.return_value = _make_agent(
            {"auto_trigger": {"enabled": True, "change_pct_threshold": 5.0}}
        )
        ok, reason = auto_trigger.should_auto_trigger("601238", 8.0)
    assert ok is True
    assert "達閾值" in reason


def test_negative_change_pct_uses_abs():
    """跌 8% 也應該觸發(用 |change_pct|)"""
    with patch("src.modules.automation.tradingagents.operations.SessionLocal") as session_factory, \
         patch("src.modules.automation.tradingagents.operations._within_cooldown", return_value=False), \
         patch("src.modules.automation.tradingagents.operations._budget_allows", return_value=True):
        db = MagicMock()
        session_factory.return_value = db
        db.query.return_value.filter.return_value.first.return_value = _make_agent(
            {"auto_trigger": {"enabled": True, "change_pct_threshold": 5.0}}
        )
        ok, _ = auto_trigger.should_auto_trigger("601238", -8.0)
    assert ok is True


def test_try_auto_trigger_returns_none_when_disabled():
    """try_auto_trigger 在不滿足條件時返回 None"""
    stock = MagicMock()
    stock.symbol = "601238"
    stock.change_pct = 8.0
    with patch("src.modules.automation.tradingagents.operations.should_auto_trigger", return_value=(False, "test")):
        result = auto_trigger.try_auto_trigger(stock)
    assert result is None


def test_try_auto_trigger_fires_when_should():
    """try_auto_trigger 在滿足條件時調 fire_and_forget_trigger"""
    stock = MagicMock()
    stock.symbol = "601238"
    stock.change_pct = 8.0
    with patch("src.modules.automation.tradingagents.operations.should_auto_trigger", return_value=(True, "test")), \
         patch("src.modules.automation.tradingagents.operations.fire_and_forget_trigger", return_value="trace-abc") as fire:
        result = auto_trigger.try_auto_trigger(stock)
    assert result == "trace-abc"
    fire.assert_called_once()
