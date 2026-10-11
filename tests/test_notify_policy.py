"""tests for src/core/notify_policy.py"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from src.platform.notifications.notify_policy import NotifyPolicy, _parse_hhmm, parse_dedupe_overrides


# ---------------------------------------------------------------------------
# _parse_hhmm
# ---------------------------------------------------------------------------

class TestParseHHMM:
    def test_normal(self):
        """時間解析 — 正常格式 09:30"""
        assert _parse_hhmm("09:30") == time(9, 30)

    def test_single_digit_hour(self):
        """時間解析 — 單位數小時 0:05"""
        assert _parse_hhmm("0:05") == time(0, 5)

    def test_midnight(self):
        """時間解析 — 23:59 邊界"""
        assert _parse_hhmm("23:59") == time(23, 59)

    def test_invalid_hour(self):
        """時間解析 — 非法小時 25 應報錯"""
        with pytest.raises(ValueError):
            _parse_hhmm("25:00")

    def test_invalid_minute(self):
        """時間解析 — 非法分鐘 60 應報錯"""
        with pytest.raises(ValueError):
            _parse_hhmm("12:60")

    def test_negative_hour(self):
        """時間解析 — 負數小時應報錯"""
        with pytest.raises(ValueError):
            _parse_hhmm("-1:00")


# ---------------------------------------------------------------------------
# is_quiet_now
# ---------------------------------------------------------------------------

class TestIsQuietNow:
    def test_empty_quiet_hours(self):
        """靜默時段 — 空配置返回 False"""
        p = NotifyPolicy(quiet_hours="")
        assert p.is_quiet_now() is False

    def test_normal_range_inside(self):
        """靜默時段 — 23:00 在 22:00-06:00 區間內"""
        p = NotifyPolicy(timezone="Asia/Shanghai", quiet_hours="22:00-06:00")
        now = datetime(2024, 1, 1, 23, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert p.is_quiet_now(now) is True

    def test_normal_range_outside(self):
        """靜默時段 — 12:00 在 22:00-06:00 區間外"""
        p = NotifyPolicy(timezone="Asia/Shanghai", quiet_hours="22:00-06:00")
        now = datetime(2024, 1, 1, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert p.is_quiet_now(now) is False

    def test_crosses_midnight_before(self):
        """靜默時段 — 跨午夜 23:30 在區間內"""
        p = NotifyPolicy(timezone="UTC", quiet_hours="22:00-06:00")
        now = datetime(2024, 1, 1, 23, 30, tzinfo=ZoneInfo("UTC"))
        assert p.is_quiet_now(now) is True

    def test_crosses_midnight_after(self):
        """靜默時段 — 跨午夜 03:00 在區間內"""
        p = NotifyPolicy(timezone="UTC", quiet_hours="22:00-06:00")
        now = datetime(2024, 1, 2, 3, 0, tzinfo=ZoneInfo("UTC"))
        assert p.is_quiet_now(now) is True

    def test_same_start_end_always_quiet(self):
        """靜默時段 — 起止相同視為全天靜默"""
        p = NotifyPolicy(timezone="UTC", quiet_hours="08:00-08:00")
        now = datetime(2024, 1, 1, 15, 0, tzinfo=ZoneInfo("UTC"))
        assert p.is_quiet_now(now) is True

    def test_non_crossing_boundary_start(self):
        """靜默時段 — 恰好等於起始時間應在區間內"""
        p = NotifyPolicy(timezone="UTC", quiet_hours="09:00-17:00")
        now = datetime(2024, 1, 1, 9, 0, tzinfo=ZoneInfo("UTC"))
        assert p.is_quiet_now(now) is True

    def test_non_crossing_boundary_end(self):
        """靜默時段 — 恰好等於結束時間應在區間外"""
        p = NotifyPolicy(timezone="UTC", quiet_hours="09:00-17:00")
        now = datetime(2024, 1, 1, 17, 0, tzinfo=ZoneInfo("UTC"))
        assert p.is_quiet_now(now) is False

    def test_invalid_format(self):
        """靜默時段 — 非法格式返回 False"""
        p = NotifyPolicy(quiet_hours="invalid")
        assert p.is_quiet_now() is False


# ---------------------------------------------------------------------------
# parse_dedupe_overrides
# ---------------------------------------------------------------------------

class TestParseDedupeOverrides:
    def test_normal(self):
        """去重覆蓋解析 — 正常 JSON"""
        assert parse_dedupe_overrides('{"agent_a": 10, "agent_b": 20}') == {
            "agent_a": 10,
            "agent_b": 20,
        }

    def test_empty_string(self):
        """去重覆蓋解析 — 空字串返回空 dict"""
        assert parse_dedupe_overrides("") == {}

    def test_none(self):
        """去重覆蓋解析 — None 返回空 dict"""
        assert parse_dedupe_overrides(None) == {}

    def test_invalid_json(self):
        """去重覆蓋解析 — 非法 JSON 返回空 dict"""
        assert parse_dedupe_overrides("not json") == {}

    def test_non_dict_json(self):
        """去重覆蓋解析 — 非 dict 型別返回空 dict"""
        assert parse_dedupe_overrides("[1,2,3]") == {}

    def test_non_int_values_skipped(self):
        """去重覆蓋解析 — 非整數值被跳過"""
        result = parse_dedupe_overrides('{"a": 10, "b": "not_int"}')
        assert result == {"a": 10}


# ---------------------------------------------------------------------------
# dedupe_ttl_minutes
# ---------------------------------------------------------------------------

class TestDedupeTTLMinutes:
    def test_hit(self):
        """TTL 查詢 — 命中覆蓋配置"""
        p = NotifyPolicy(dedupe_ttl_overrides={"agent_a": 30})
        assert p.dedupe_ttl_minutes("agent_a", default=10) == 30

    def test_miss(self):
        """TTL 查詢 — 未命中返回預設值"""
        p = NotifyPolicy(dedupe_ttl_overrides={"agent_a": 30})
        assert p.dedupe_ttl_minutes("agent_b", default=10) == 10

    def test_none_overrides(self):
        """TTL 查詢 — 無覆蓋配置返回預設值"""
        p = NotifyPolicy(dedupe_ttl_overrides=None)
        assert p.dedupe_ttl_minutes("agent_a", default=5) == 5
