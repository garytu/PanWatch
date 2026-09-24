"""時區處理工具 - 統一時間儲存和顯示。

預設時區可透過環境變數覆蓋：
- TZ（推薦）

未設定時預設 Asia/Shanghai。
"""

from datetime import datetime, timezone
import os
from zoneinfo import ZoneInfo


def _get_app_tz() -> ZoneInfo:
    tz_name = os.environ.get("TZ") or os.environ.get("APP_TIMEZONE") or "Asia/Shanghai"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


def utc_now() -> datetime:
    """獲取當前 UTC 時間（帶時區資訊）"""
    return datetime.now(timezone.utc)


def beijing_now() -> datetime:
    """獲取當前預設時區時間（歷史命名保留；帶時區資訊）"""
    return datetime.now(_get_app_tz())


def to_utc(dt: datetime) -> datetime:
    """將時間轉換為 UTC"""
    if dt.tzinfo is None:
        # 假設無時區的時間是預設時區
        dt = dt.replace(tzinfo=_get_app_tz())
    return dt.astimezone(timezone.utc)


def to_beijing(dt: datetime) -> datetime:
    """將時間轉換為預設時區（歷史命名保留）"""
    if dt.tzinfo is None:
        # 假設無時區的時間是 UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_get_app_tz())


def format_beijing(dt: datetime, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """格式化為預設時區字串（歷史命名保留）"""
    return to_beijing(dt).strftime(fmt)


def to_iso_utc(dt: datetime) -> str:
    """轉換為 ISO 格式的 UTC 時間字串（帶 Z 字尾）"""
    utc_dt = to_utc(dt)
    return utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def to_iso_with_tz(dt: datetime) -> str:
    """轉換為 ISO 格式字串（帶時區偏移）"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()
