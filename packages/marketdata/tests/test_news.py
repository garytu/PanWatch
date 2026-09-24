"""新聞資訊 vendor 測試:xueqiu/eastmoney_news/eastmoney 均離線構造樣例,
monkeypatch news 模組內 market_get(三個 vendor 共享同一個模組級引用)。

沙箱代理會攔真實端點,欄位對映按 src/collectors/news_collector.py 盡力構造;
雪球已知被阿里雲 WAF 攔截,用構造的 WAF HTML 挑戰頁樣例驗證防禦分支。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import marketdata.vendors.news as news_mod
from marketdata.client import MarketData
from marketdata.defaults import StaticConfigProvider
from marketdata.http import capture_errors
from marketdata.ports import SourceConfig
from marketdata.symbol import Symbol
from marketdata.types import NewsArticle


def _jsonp(payload: dict) -> str:
    return "jQuery(" + json.dumps(payload) + ")"


# ---------------------------------------------------------------------------
# xueqiu(雪球個股新聞)
# ---------------------------------------------------------------------------

def test_xueqiu_parses_item_and_importance(monkeypatch):
    payload = {
        "list": [
            {
                "id": 5001,
                "title": "<b>重磅</b>:公司獲得新訂單",
                "description": "詳細描述內容",
                "created_at": 1752652800000,  # ms -> 2026-07-16 08:00:00 UTC
                "target": "https://xueqiu.com/1234/5001",
            }
        ]
    }
    monkeypatch.setattr(news_mod, "market_get", lambda *a, **k: json.dumps(payload))

    out = news_mod.XueqiuNewsVendor().fetch([Symbol.parse("600519")], {"cookies": "abc=1"})
    assert len(out) == 1
    a = out[0]
    assert isinstance(a, NewsArticle)
    assert a.source == "xueqiu"
    assert a.external_id == "5001"
    assert a.title == "重磅:公司獲得新訂單"  # HTML 標籤被清理
    assert a.content == "詳細描述內容"
    assert a.publish_time == datetime.fromtimestamp(1752652800, tz=timezone.utc)
    assert a.symbols == ["600519"]
    assert a.importance == 2  # 命中"重磅"
    assert a.url == "https://xueqiu.com/1234/5001"


def test_xueqiu_symbol_id_prefix_rules(monkeypatch):
    """SH/SZ 加字首,BJ 保留原值(雪球不識別 BJ 程式碼)。"""
    captured = {}

    def fake(url, *, params, **kwargs):
        captured["symbol_id"] = params["symbol_id"]
        return json.dumps({"list": []})

    monkeypatch.setattr(news_mod, "market_get", fake)

    news_mod.XueqiuNewsVendor().fetch([Symbol.parse("600519")], {})
    assert captured["symbol_id"] == "SH600519"

    news_mod.XueqiuNewsVendor().fetch([Symbol.parse("000001")], {})
    assert captured["symbol_id"] == "SZ000001"

    news_mod.XueqiuNewsVendor().fetch([Symbol.parse("920001")], {})
    assert captured["symbol_id"] == "920001"  # BJ 原值透傳


def test_xueqiu_no_a_share_symbols_returns_empty():
    # 港股/美股程式碼不是 6 位數字 -> 直接返回 [],不發請求
    assert news_mod.XueqiuNewsVendor().fetch([Symbol.parse("00700"), Symbol.parse("AAPL")], {}) == []


def test_xueqiu_waf_blocked_records_error(monkeypatch):
    """WAF 挑戰頁(HTML)命中特徵字串 -> 返回 [] 且 record_error 帶 "WAF" 字樣。"""
    html = "<html><body><textarea>window._waf_challenge = 1;</textarea></body></html>"
    monkeypatch.setattr(news_mod, "market_get", lambda *a, **k: html)

    with capture_errors() as errs:
        out = news_mod.XueqiuNewsVendor().fetch([Symbol.parse("600519")], {})

    assert out == []
    assert any("WAF" in e for e in errs)


def test_xueqiu_non_json_non_waf_response_returns_empty(monkeypatch):
    """既不含 WAF 特徵、也不是合法 JSON 的回應 -> 視為非預期結構,同樣 record_error 且返回 []。"""
    monkeypatch.setattr(news_mod, "market_get", lambda *a, **k: "not json at all")

    with capture_errors() as errs:
        out = news_mod.XueqiuNewsVendor().fetch([Symbol.parse("600519")], {})

    assert out == []
    assert any("WAF" in e for e in errs)


def test_xueqiu_market_get_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(news_mod, "market_get", lambda *a, **k: None)
    assert news_mod.XueqiuNewsVendor().fetch([Symbol.parse("600519")], {}) == []


# ---------------------------------------------------------------------------
# eastmoney_news(東財個股新聞搜尋)
# ---------------------------------------------------------------------------

def _em_news_payload(code="202607170001", title="賽力斯釋出新車型<em>亮點</em>"):
    item = {
        "code": code,
        "title": title,
        "content": "詳細內容",
        "url": f"https://finance.eastmoney.com/a/{code}.html",
        "date": "2026-07-17 09:30:00",
    }
    return {"code": 0, "result": {"cmsArticleWebOld": [item]}}


def test_eastmoney_news_parses_and_uses_names(monkeypatch):
    calls = []

    def fake(url, *, params, **kwargs):
        calls.append(params)
        return _jsonp(_em_news_payload())

    monkeypatch.setattr(news_mod, "market_get", fake)

    vendor = news_mod.EastmoneyStockNewsVendor()
    out = vendor.fetch([Symbol.parse("601127")], {"symbol_names": {"601127": "賽力斯"}})

    assert len(out) == 1
    a = out[0]
    assert a.source == "eastmoney_news"
    assert a.external_id == "202607170001"
    assert a.title == "賽力斯釋出新車型亮點"  # 高亮標籤被清理
    assert a.content == "詳細內容"
    assert a.publish_time == datetime(2026, 7, 17, 9, 30, 0, tzinfo=timezone.utc)
    assert a.symbols == ["601127"]
    assert a.url == "https://finance.eastmoney.com/a/202607170001.html"

    # names 生效:搜尋關鍵詞應為股票名稱而非程式碼
    assert len(calls) == 1
    sent = json.loads(calls[0]["param"])
    assert sent["keyword"] == "賽力斯"


def test_eastmoney_news_falls_back_to_code_without_names(monkeypatch):
    calls = []

    def fake(url, *, params, **kwargs):
        calls.append(params)
        return _jsonp(_em_news_payload())

    monkeypatch.setattr(news_mod, "market_get", fake)

    news_mod.EastmoneyStockNewsVendor().fetch([Symbol.parse("601127")], {})
    sent = json.loads(calls[0]["param"])
    assert sent["keyword"] == "601127"  # 缺名 fallback 用程式碼搜尋


def test_eastmoney_news_dedup_across_symbols(monkeypatch):
    """同一條新聞可能出現在多隻股票的搜尋結果裡,fetch() 內部按 external_id 去重。"""
    monkeypatch.setattr(news_mod, "market_get", lambda *a, **k: _jsonp(_em_news_payload(code="DUP1")))

    out = news_mod.EastmoneyStockNewsVendor().fetch(
        [Symbol.parse("601127"), Symbol.parse("600519")],
        {"symbol_names": {"601127": "賽力斯", "600519": "貴州茅臺"}},
    )
    assert len(out) == 1  # 兩次搜尋都命中同一條 DUP1,去重後只剩 1 條


def test_eastmoney_news_code_not_zero_returns_empty(monkeypatch):
    monkeypatch.setattr(news_mod, "market_get", lambda *a, **k: _jsonp({"code": 1, "result": {}}))
    assert news_mod.EastmoneyStockNewsVendor().fetch([Symbol.parse("600519")], {}) == []


def test_eastmoney_news_non_jsonp_response_returns_empty(monkeypatch):
    monkeypatch.setattr(news_mod, "market_get", lambda *a, **k: "<html>not jsonp</html>")
    assert news_mod.EastmoneyStockNewsVendor().fetch([Symbol.parse("600519")], {}) == []


def test_eastmoney_news_market_get_failure_returns_empty(monkeypatch):
    monkeypatch.setattr(news_mod, "market_get", lambda *a, **k: None)
    assert news_mod.EastmoneyStockNewsVendor().fetch([Symbol.parse("600519")], {}) == []


def test_eastmoney_news_fetch_by_keyword(monkeypatch):
    calls = []

    def fake(url, *, params, **kwargs):
        calls.append(params)
        return _jsonp(_em_news_payload(code="KW1", title="新能源汽車行業週報"))

    monkeypatch.setattr(news_mod, "market_get", fake)

    out = news_mod.EastmoneyStockNewsVendor.fetch_by_keyword("新能源汽車")
    assert len(out) == 1
    assert out[0].title == "新能源汽車行業週報"
    assert out[0].symbols == ["新能源汽車"]  # keyword 本身作為 symbol 標記(照搬原邏輯)

    sent = json.loads(calls[0]["param"])
    assert sent["keyword"] == "新能源汽車"


# ---------------------------------------------------------------------------
# eastmoney(東財公告)
# ---------------------------------------------------------------------------

def _ann_payload():
    return {
        "success": True,
        "data": {
            "list": [
                {
                    "art_code": "AN202607170001",
                    "title": "貴州茅臺關於分紅派息的公告",
                    "notice_date": "2026-07-17 08:00:00",
                    "columns": [{"column_name": "定期公告"}],
                    "codes": [{"stock_code": "600519"}],
                }
            ]
        },
    }


def test_eastmoney_ann_parses(monkeypatch):
    monkeypatch.setattr(news_mod, "market_get", lambda *a, **k: _ann_payload())

    out = news_mod.EastmoneyAnnNewsVendor().fetch([Symbol.parse("600519")], {})
    assert len(out) == 1
    a = out[0]
    assert a.source == "eastmoney"
    assert a.external_id == "AN202607170001"
    assert a.title == "貴州茅臺關於分紅派息的公告"
    assert a.content == ""  # 公告只有標題
    assert a.publish_time == datetime(2026, 7, 17, 8, 0, 0, tzinfo=timezone.utc)
    assert a.symbols == ["600519"]
    assert a.importance == 2  # 命中"分紅"
    assert a.url == "https://data.eastmoney.com/notices/detail/600519/AN202607170001.html"


def test_eastmoney_ann_non_a_share_returns_empty():
    assert news_mod.EastmoneyAnnNewsVendor().fetch([Symbol.parse("00700")], {}) == []


def test_eastmoney_ann_failure_response_returns_empty(monkeypatch):
    monkeypatch.setattr(news_mod, "market_get", lambda *a, **k: {"success": False})
    assert news_mod.EastmoneyAnnNewsVendor().fetch([Symbol.parse("600519")], {}) == []

    monkeypatch.setattr(news_mod, "market_get", lambda *a, **k: None)
    assert news_mod.EastmoneyAnnNewsVendor().fetch([Symbol.parse("600519")], {}) == []


# ---------------------------------------------------------------------------
# MarketData.news() —— 聚合(非失敗轉移):合併多源 + 去重 + 排序 + since 過濾
# ---------------------------------------------------------------------------

def _fake_agg_market_get(url, *, host_key, params=None, **kwargs):
    if host_key == news_mod._EM_NEWS_HOST:
        return _jsonp(
            {
                "code": 0,
                "result": {
                    "cmsArticleWebOld": [
                        {
                            "code": "A1",
                            "title": "個股新聞標題",
                            "content": "個股新聞內容",
                            "url": "https://finance.eastmoney.com/a/A1.html",
                            "date": "2026-07-17 10:00:00",
                        }
                    ]
                },
            }
        )
    if host_key == news_mod._EM_ANN_HOST:
        return {
            "success": True,
            "data": {
                "list": [
                    {
                        "art_code": "AN1",
                        "title": "重大事項公告",
                        "notice_date": "2026-07-17 12:00:00",
                        "columns": [],
                        "codes": [{"stock_code": "600519"}],
                    },
                    {
                        # 36.5 小時前:超出 2h 常規視窗,但在 72h 公告視窗內 —— 驗證寬視窗生效
                        "art_code": "AN2",
                        "title": "季報點評",
                        "notice_date": "2026-07-16 00:00:00",
                        "columns": [],
                        "codes": [{"stock_code": "600519"}],
                    },
                ]
            },
        }
    return None


def _agg_md() -> MarketData:
    return MarketData(
        config=StaticConfigProvider(
            {
                "news": [
                    SourceConfig(vendor="eastmoney_news", priority=1),
                    SourceConfig(vendor="eastmoney", priority=2),
                ]
            }
        )
    )


def test_news_merges_multiple_sources_and_sorts_desc(monkeypatch):
    monkeypatch.setattr(news_mod, "market_get", _fake_agg_market_get)

    md = _agg_md()
    out = md.news(["600519"], since_hours=2, names={"600519": "貴州茅臺"})

    # 按時間倒序:AN1(07-17 12:00) > A1(07-17 10:00) > AN2(07-16 00:00)
    assert [a.external_id for a in out] == ["AN1", "A1", "AN2"]


def test_news_no_now_skips_since_filter(monkeypatch):
    """不傳 now 則不做時間過濾,即便 since_hours 很小,全部合併結果原樣返回。"""
    monkeypatch.setattr(news_mod, "market_get", _fake_agg_market_get)

    out = _agg_md().news(["600519"], since_hours=1)
    assert len(out) == 3


def test_news_since_filter_with_now_uses_wider_announcement_window(monkeypatch):
    """傳入 now 後:常規源用 since_hours(2h)過濾,公告源(eastmoney)用 max(since_hours,72)h。
    AN2 距 now 約 36.5 小時 —— 若按 2h 視窗會被過濾掉,但作為公告應享受 72h 視窗而保留;
    A1(eastmoney_news,~2.5 小時前)按 2h 視窗應被過濾掉。
    """
    monkeypatch.setattr(news_mod, "market_get", _fake_agg_market_get)

    now = datetime(2026, 7, 17, 12, 30, tzinfo=timezone.utc)
    out = _agg_md().news(["600519"], since_hours=2, now=now)

    assert [a.external_id for a in out] == ["AN1", "AN2"]


def test_news_dedup_keeps_first_seen_across_vendors(monkeypatch):
    """跨 vendor 出現相同 external_id 時,按 external_id 去重,保留先處理(優先順序更高)的源。"""

    def fake(url, *, host_key, params=None, **kwargs):
        if host_key == news_mod._EM_NEWS_HOST:
            return _jsonp(
                {
                    "code": 0,
                    "result": {
                        "cmsArticleWebOld": [
                            {
                                "code": "DUP1",
                                "title": "來自東財的標題",
                                "content": "東財內容",
                                "url": "https://finance.eastmoney.com/a/DUP1.html",
                                "date": "2026-07-17 10:00:00",
                            }
                        ]
                    },
                }
            )
        if host_key == news_mod._XUEQIU_HOST:
            return json.dumps(
                {
                    "list": [
                        {
                            "id": "DUP1",
                            "title": "來自雪球的標題",
                            "description": "雪球內容",
                            "created_at": 1752739200000,  # 2026-07-17 12:00:00 UTC(更新,但優先順序更低)
                            "target": "https://xueqiu.com/x/DUP1",
                        }
                    ]
                }
            )
        return None

    monkeypatch.setattr(news_mod, "market_get", fake)

    md = MarketData(
        config=StaticConfigProvider(
            {
                "news": [
                    SourceConfig(vendor="eastmoney_news", priority=1),
                    SourceConfig(vendor="xueqiu", priority=2),
                ]
            }
        )
    )
    out = md.news(["600519"])
    assert len(out) == 1
    assert out[0].source == "eastmoney_news"  # 先處理的優先順序更高的源被保留
    assert out[0].title == "來自東財的標題"


def test_news_unknown_or_disabled_source_skipped(monkeypatch):
    monkeypatch.setattr(news_mod, "market_get", lambda *a, **k: None)

    md = MarketData(
        config=StaticConfigProvider(
            {
                "news": [
                    SourceConfig(vendor="not_a_real_vendor", priority=1),
                    SourceConfig(vendor="eastmoney", priority=2, enabled=False),
                ]
            }
        )
    )
    assert md.news(["600519"]) == []


def test_news_records_metrics(monkeypatch):
    monkeypatch.setattr(news_mod, "market_get", _fake_agg_market_get)

    md = _agg_md()
    md.news(["600519"])
    snap = md.health()
    assert snap["eastmoney_news"]["success_rate"] == 1.0
    assert snap["eastmoney"]["success_rate"] == 1.0


# ---------------------------------------------------------------------------
# MarketData.news_by_keyword()
# ---------------------------------------------------------------------------

def test_marketdata_news_by_keyword(monkeypatch):
    calls = []

    def fake(url, *, params, **kwargs):
        calls.append(params)
        return _jsonp(_em_news_payload(code="KW2", title="光伏板塊行業動態"))

    monkeypatch.setattr(news_mod, "market_get", fake)

    md = MarketData(config=StaticConfigProvider({}))
    out = md.news_by_keyword("光伏")

    assert len(out) == 1
    assert out[0].title == "光伏板塊行業動態"
    sent = json.loads(calls[0]["param"])
    assert sent["keyword"] == "光伏"
