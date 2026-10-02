"""PanWatch 內部頁面連結生成：深度分析詳細資訊頁等。

全域性設定 key: panwatch_base_url(公開訪問地址,用於通知裡的詳細資訊頁絕對連結)。
讀取模式與 stock_link.py 一致(AppSettings,miss 回退預設)。
"""

from __future__ import annotations

import logging

from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import AppSettings

logger = logging.getLogger(__name__)

SETTING_KEY = "panwatch_base_url"


def get_base_url() -> str:
    """從 AppSettings 讀取公開訪問地址(去尾部斜槓);未配置 / DB 不可用返回空串。

    包一層兜底:單測或 DB 未初始化(app_settings 表不存在)時,讀取設定不應讓整個
    分析結果對映崩掉 —— 讀不到就降級為空串(不拼詳細資訊連結)。
    """
    try:
        db = SessionLocal()
        try:
            row = db.query(AppSettings).filter(AppSettings.key == SETTING_KEY).first()
            val = (row.value if row and row.value else "").strip()
            return val.rstrip("/")
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001 — DB 未初始化/表缺失等均降級為空
        logger.debug(f"get_base_url 讀取失敗,降級為空: {e}")
        return ""


def analysis_detail_url(symbol: str, date: str, base_url: str = "") -> str:
    """深度分析詳細資訊頁 URL: {base}/analysis/{symbol}/{date}。

    base_url 未配置(空)時返回空串 —— 呼叫方據此決定是否拼接連結。
    """
    if not base_url:
        base_url = get_base_url()
    if not base_url:
        return ""
    return f"{base_url}/analysis/{symbol}/{date}"


def analysis_detail_markdown(
    symbol: str, date: str, label: str = "📊 檢視完整分析詳細資訊", base_url: str = ""
) -> str:
    """Markdown 連結 [label](url);無 base_url 時返回空串。"""
    url = analysis_detail_url(symbol, date, base_url)
    if not url:
        return ""
    return f"[{label}]({url})"
