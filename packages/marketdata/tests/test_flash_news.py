"""快訊(7×24)vendor 測試:cls/sina/eastmoney 均離線構造樣例,monkeypatch 各自模組內 market_get。

沙箱代理會攔真實端點,欄位對映按檔案盡力構造,未檔案化欄位(cls 的 level/stock_list、
sina 的 ext.stocks)用防禦性樣例覆蓋,斷言不崩且儘量抽取正確。
"""
from __future__ import annotations

from datetime import datetime, timezone

import marketdata.vendors.flash_news as fn
from marketdata.client import MarketData
from marketdata.defaults import StaticConfigProvider
from marketdata.ports import SourceConfig
from marketdata.types import FlashNews


# ---------------------------------------------------------------------------
# cls(財聯社)
# ---------------------------------------------------------------------------

def test_cls_sign_is_deterministic():
    params = {
        "appName": "CailianpressWeb",
        "os": "web",
        "sv": "7.7.5",
        "last_time": "",
        "refresh_type": 1,
        "rn": 50,
    }
    sign1 = fn._cls_sign(params)
    sign2 = fn._cls_sign(params)
    # 同樣的 params -> 同樣的 sign(確定性,便於離線測試與除錯)
    assert sign1 == sign2
    assert len(sign1) == 32  # md5 hex digest 長度固定
    assert all(c in "0123456789abcdef" for c in sign1)

    # 手算校驗:sha1(qs).hexdigest() 再 md5
    import hashlib
    qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
    expected = hashlib.md5(hashlib.sha1(qs.encode()).hexdigest().encode()).hexdigest()
    assert sign1 == expected


def test_cls_parses_and_maps_importance_and_symbols(monkeypatch):
    payload = {
        "data": {
            "roll_data": [
                {
                    "id": 1001,
                    "title": "央行公開市場操作",
                    "content": "央行今日開展逆回購操作",
                    "brief": "",
                    "ctime": 1752652800,  # 2025-07-16 08:00:00 UTC 附近的固定戳(可復現)
                    "level": "A",
                    "stock_list": [{"code": "600519", "name": "貴州茅臺"}, {"SecurityCode": "000001"}],
                    "shareurl": "https://www.cls.cn/detail/1001",
                },
                {
                    # 無 title/content,回退 brief;level 為未識別字串 -> importance=0
                    "id": 1002,
                    "title": "",
                    "content": "",
                    "brief": "簡訊內容",
                    "ctime": "1752652900",
                    "level": "Z",
                    "stock_list": [],
                    "shareurl": "",
                },
            ]
        }
    }
    monkeypatch.setattr(fn, "market_get", lambda *a, **k: payload)

    out = fn.ClsFlashNewsVendor().fetch([], {"days": 20})
    assert len(out) == 2
    assert all(isinstance(x, FlashNews) for x in out)

    first = out[0]
    assert first.source == "cls"
    assert first.external_id == "1001"
    assert first.title == "央行公開市場操作"
    assert first.content == "央行今日開展逆回購操作"
    assert first.publish_time == datetime.fromtimestamp(1752652800, tz=timezone.utc)
    assert first.importance == 3  # level="A" -> 3
    assert first.symbols == ["600519", "000001"]
    assert first.url == "https://www.cls.cn/detail/1001"

    second = out[1]
    assert second.title == "簡訊內容"  # 回退 brief
    assert second.content == "簡訊內容"
    assert second.importance == 0  # 未識別 level
    assert second.symbols == []


def test_cls_empty_response(monkeypatch):
    monkeypatch.setattr(fn, "market_get", lambda *a, **k: None)
    assert fn.ClsFlashNewsVendor().fetch([], {}) == []

    monkeypatch.setattr(fn, "market_get", lambda *a, **k: {"data": {"roll_data": []}})
    assert fn.ClsFlashNewsVendor().fetch([], {}) == []


def test_cls_tolerates_broken_item(monkeypatch):
    """單條解析異常應被跳過而非整體失敗。"""
    payload = {"data": {"roll_data": [{"id": 1, "title": None, "content": None, "brief": None}]}}
    monkeypatch.setattr(fn, "market_get", lambda *a, **k: payload)
    assert fn.ClsFlashNewsVendor().fetch([], {}) == []


# ---------------------------------------------------------------------------
# sina(新浪直播)
# ---------------------------------------------------------------------------

def test_sina_parses_ext_stocks_and_time(monkeypatch):
    payload = {
        "result": {
            "data": {
                "feed": {
                    "list": [
                        {
                            "id": "2001",
                            "rich_text": "滬指高開0.5%,兩市成交額破萬億",
                            "create_time": "2026-07-16 09:31:00",
                            "ext": '{"stocks": [{"symbol": "sh600519", "name": "貴州茅臺"}, {"code": "000001"}]}',
                        },
                        {
                            # 無 rich_text,回退 content;create_time 為秒戳字串
                            "id": "2002",
                            "content": "深成指低開",
                            "create_time": "1752652800",
                            "ext": "",
                        },
                    ]
                }
            }
        }
    }
    monkeypatch.setattr(fn, "market_get", lambda *a, **k: payload)

    out = fn.SinaFlashNewsVendor().fetch([], {"days": 20})
    assert len(out) == 2
    first = out[0]
    assert first.source == "sina"
    assert first.external_id == "2001"
    assert first.content == "滬指高開0.5%,兩市成交額破萬億"
    assert first.publish_time == datetime(2026, 7, 16, 9, 31, 0, tzinfo=timezone.utc)
    assert first.symbols == ["sh600519", "000001"]

    second = out[1]
    assert second.content == "深成指低開"  # 回退 content
    assert second.publish_time == datetime.fromtimestamp(1752652800, tz=timezone.utc)
    assert second.symbols == []


def test_sina_empty_response(monkeypatch):
    monkeypatch.setattr(fn, "market_get", lambda *a, **k: None)
    assert fn.SinaFlashNewsVendor().fetch([], {}) == []

    monkeypatch.setattr(fn, "market_get", lambda *a, **k: {"result": {"data": {"feed": {"list": []}}}})
    assert fn.SinaFlashNewsVendor().fetch([], {}) == []


def test_sina_tolerates_broken_ext_json(monkeypatch):
    """ext 不是合法 JSON 時不應崩,symbols 回退空列表。"""
    payload = {
        "result": {
            "data": {
                "feed": {
                    "list": [
                        {"id": "3", "rich_text": "測試內容", "create_time": "2026-07-16 10:00:00", "ext": "{not json"}
                    ]
                }
            }
        }
    }
    monkeypatch.setattr(fn, "market_get", lambda *a, **k: payload)
    out = fn.SinaFlashNewsVendor().fetch([], {})
    assert len(out) == 1
    assert out[0].symbols == []


# ---------------------------------------------------------------------------
# eastmoney(東財快訊)
# ---------------------------------------------------------------------------

def test_eastmoney_parses_title_summary_time(monkeypatch):
    payload = {
        "data": {
            "fastNewsList": [
                {
                    "id": "4001",
                    "title": "機構:三季度A股有望震盪上行",
                    "summary": "多家機構釋出三季度策略展望",
                    "showTime": "2026-07-16 11:20:00",
                },
                {
                    # showTime 格式不可解析 -> 回退 EPOCH,不崩
                    "id": "4002",
                    "title": "快訊標題",
                    "summary": "摘要內容",
                    "showTime": "not-a-date",
                },
            ]
        }
    }
    monkeypatch.setattr(fn, "market_get", lambda *a, **k: payload)

    out = fn.EastmoneyFlashNewsVendor().fetch([], {"days": 30})
    assert len(out) == 2
    first = out[0]
    assert first.source == "eastmoney"
    assert first.title == "機構:三季度A股有望震盪上行"
    assert first.content == "多家機構釋出三季度策略展望"
    assert first.publish_time == datetime(2026, 7, 16, 11, 20, 0, tzinfo=timezone.utc)
    assert first.symbols == []
    assert first.importance == 0

    second = out[1]
    assert second.publish_time == fn._EPOCH  # 不可解析時間的防禦回退


def test_eastmoney_empty_response(monkeypatch):
    monkeypatch.setattr(fn, "market_get", lambda *a, **k: None)
    assert fn.EastmoneyFlashNewsVendor().fetch([], {}) == []

    monkeypatch.setattr(fn, "market_get", lambda *a, **k: {"data": {"fastNewsList": []}})
    assert fn.EastmoneyFlashNewsVendor().fetch([], {}) == []


# ---------------------------------------------------------------------------
# MarketData.flash_news() —— 走單源 Engine 出數
# ---------------------------------------------------------------------------

def test_marketdata_flash_news_single_source(monkeypatch):
    payload = {
        "data": {
            "roll_data": [
                {
                    "id": "9001",
                    "title": "測試快訊標題",
                    "content": "測試快訊正文包含關鍵字ABC",
                    "brief": "",
                    "ctime": 1752652800,
                    "level": "B",
                    "stock_list": [],
                    "shareurl": "",
                }
            ]
        }
    }
    monkeypatch.setattr(fn, "market_get", lambda *a, **k: payload)

    md = MarketData(
        config=StaticConfigProvider(
            {"flash_news": [SourceConfig(vendor="cls", config={}, enabled=True)]}
        )
    )
    out = md.flash_news(limit=20)
    assert len(out) == 1
    assert out[0].title == "測試快訊標題"
    assert out[0].importance == 2  # level="B" -> 2

    # keyword 過濾:命中 title/content 才保留
    out_kw = md.flash_news(limit=20, keyword="ABC")
    assert len(out_kw) == 1
    out_kw_miss = md.flash_news(limit=20, keyword="不存在的關鍵字")
    assert out_kw_miss == []
