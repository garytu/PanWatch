"""TradingAgents toolkit 併發資料隔離迴歸測試。

根因 bug:_PANWATCH_DATA_CACHE 曾是模組級全域性 dict,兩隻標的併發深度分析時
(asyncio.to_thread worker 執行緒)互相覆蓋 —— 廣汽 601238 的報告混入賽力斯 601127
的 K線/價格。改成 ContextVar 後,每個併發任務(copy_context)拿獨立副本,互不串臺。

本測試用 contextvars.copy_context() 模擬兩個併發任務,直接復現並驗證修復。
注意:只測 _serve_from_panwatch / _stock_meta_header 的直接路徑,不經過
_patched_route_to_vendor(避免觸發 _emit_toolkit_log → log_context/DB)。
"""

from __future__ import annotations

import contextvars
from types import SimpleNamespace

from src.modules.automation.tradingagents import toolkit_adapter as ta


def _stock(symbol: str, name: str):
    return SimpleNamespace(symbol=symbol, name=name, market=SimpleNamespace(value="CN"))


def _kline(date: str, close: float):
    return {"date": date, "open": close, "high": close, "low": close, "close": close, "volume": 1000}


def _data(symbol: str, name: str, close: float):
    return {
        "stock": _stock(symbol, name),
        "quote": {"name": name, "current_price": close},
        "klines": [_kline("2026-05-01", close), _kline("2026-05-02", close + 1)],
    }


GAC = _data("601238", "廣汽集團", 9.50)        # 廣汽
SERES = _data("601127", "賽力斯", 83.26)        # 賽力斯


def test_stock_meta_header_uses_current_context():
    """_stock_meta_header 讀當前 context 的 stock,而非程式全域性。"""
    def _run():
        with ta.panwatch_data_context(GAC):
            return ta._stock_meta_header("601238")
    header = contextvars.copy_context().run(_run)
    assert "廣汽集團" in header
    assert "賽力斯" not in header
    assert "9.50" in header


def test_two_concurrent_contexts_do_not_cross_talk():
    """復現生產 bug:任務A(廣汽)執行中,任務B(賽力斯)注入資料,
    A 後續工具呼叫必須仍讀到廣汽 —— 舊的全域性 dict 實現這裡會串成賽力斯。"""
    ctx_a = contextvars.copy_context()
    ctx_b = contextvars.copy_context()

    # A 先進入 context(模擬 worker A 開始,資料已注入但還沒跑完工具)
    ctx_a.run(lambda: ta._PANWATCH_DATA.set(dict(GAC)))
    # B 隨後進入 context(併發任務 B 啟動)—— 舊實現此處會覆蓋全域性
    ctx_b.run(lambda: ta._PANWATCH_DATA.set(dict(SERES)))

    # A 繼續跑工具呼叫:get_stock_data(601238) 必須返回廣汽 K線/價格
    out_a = ctx_a.run(lambda: ta._serve_from_panwatch("get_stock_data", "601238", {}, args=("601238",)))
    out_b = ctx_b.run(lambda: ta._serve_from_panwatch("get_stock_data", "601127", {}, args=("601127",)))

    assert "廣汽集團" in out_a and "賽力斯" not in out_a
    assert "9.5" in out_a            # 廣汽收盤價
    assert "83.26" not in out_a      # 不含賽力斯價格

    assert "賽力斯" in out_b and "廣汽集團" not in out_b


def test_context_restored_after_exit():
    """panwatch_data_context 退出後,當前 context 的資料還原為空。"""
    def _run():
        assert ta._cache() == {}
        with ta.panwatch_data_context(SERES):
            assert ta._cache().get("stock").symbol == "601127"
        # 退出後還原
        return ta._cache()
    assert contextvars.copy_context().run(_run) == {}


def test_nested_contexts_restore_outer():
    """巢狀 context:內層退出後外層資料恢復(token reset 語義)。"""
    def _run():
        with ta.panwatch_data_context(GAC):
            assert ta._cache().get("stock").symbol == "601238"
            with ta.panwatch_data_context(SERES):
                assert ta._cache().get("stock").symbol == "601127"
            # 內層退出,外層廣汽恢復
            assert ta._cache().get("stock").symbol == "601238"
    contextvars.copy_context().run(_run)


# ---------------------------------------------------------------------------
# 行業/主題新聞關鍵詞搜尋(B 功能:get_news 非 ticker 詞 → 即時搜中文新聞)
# ---------------------------------------------------------------------------

def test_keyword_news_formats():
    """行業/主題詞搜中文新聞:格式化含關鍵詞 + 標題"""
    from unittest.mock import patch
    from datetime import datetime
    from src.platform.marketdata.collectors.news_collector import NewsItem

    def fake(kw):
        return [NewsItem(source="em", external_id="1", title=f"{kw}動態", content="", publish_time=datetime(2026, 5, 30))]
    with patch("src.platform.marketdata.marketdata_client.md_news_by_keyword", fake):
        r = ta._serve_keyword_news("汽車行業")
    assert "汽車行業" in r and "動態" in r


def test_keyword_news_empty_warns_no_fabrication():
    """搜不到行業新聞時返回防編造提示(避免 LLM 憑空編)"""
    from unittest.mock import patch

    def fake_empty(kw):
        return []
    with patch("src.platform.marketdata.marketdata_client.md_news_by_keyword", fake_empty):
        r = ta._serve_keyword_news("某冷門主題")
    assert "未搜到" in r and "不要編造" in r


if __name__ == "__main__":
    import unittest
    unittest.main()
