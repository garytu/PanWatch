from datetime import date, timedelta

import marketdata.vendors.events as ev
from marketdata.symbol import Symbol
from marketdata.types import EventItem


def test_events_parses_and_filters(monkeypatch):
    # 東財 ann 回應結構與欄位名以 src/collectors/events_collector.py 的 _parse_item 實際讀取為準:
    # data.list[].{art_code, title, notice_date, columns[].column_name, codes[].stock_code}
    #
    # 日期相對「今天」生成:since_days 視窗是滾動的,寫死日期的用例會在跨過視窗那天
    # 突然全被過濾掉而失敗(曾發生)。
    recent = date.today() - timedelta(days=2)
    older = date.today() - timedelta(days=4)
    recent_code = f"AN{recent:%Y%m%d}0002"
    older_code = f"AN{older:%Y%m%d}0001"

    payload = {
        "success": True,
        "data": {
            "list": [
                # 較新、"回購" -> event_type=repurchase, importance=2
                {
                    "art_code": recent_code,
                    "title": "貴州茅臺股份有限公司關於回購股份的公告",
                    "notice_date": f"{recent:%Y-%m-%d} 10:00:00",
                    "columns": [{"column_name": "臨時公告"}],
                    "codes": [{"stock_code": "600519"}],
                },
                # 較舊、"重大資產重組" -> event_type=restructuring, importance=3
                {
                    "art_code": older_code,
                    "title": "貴州茅臺股份有限公司關於重大資產重組的公告",
                    "notice_date": f"{older:%Y-%m-%d} 09:00:00",
                    "columns": [{"column_name": "重大事項"}],
                    "codes": [{"stock_code": "600519"}],
                },
                # 與第一條重複的 art_code -> 應被去重
                {
                    "art_code": recent_code,
                    "title": "貴州茅臺股份有限公司關於回購股份的公告(重複)",
                    "notice_date": f"{recent:%Y-%m-%d} 10:00:00",
                    "columns": [{"column_name": "臨時公告"}],
                    "codes": [{"stock_code": "600519"}],
                },
            ]
        },
    }
    monkeypatch.setattr(ev, "market_get", lambda *a, **k: payload)

    # 混入一個非 A 股程式碼(5 位港股),驗證 A 股過濾只對 symbols 生效、不影響返回結構。
    symbols = [Symbol.parse("600519"), Symbol.parse("00700")]
    out = ev.EventsVendor().fetch(symbols, {"since_days": 30})

    assert all(isinstance(x, EventItem) for x in out)
    # 去重生效:3 條輸入 -> 2 條唯一 (source, external_id)
    assert len(out) == 2

    # 排序:按 (publish_time, importance) 降序 -> 較新的回購公告排第一
    assert out[0].external_id == recent_code
    assert out[0].event_type == "repurchase"
    assert out[0].importance == 2
    assert out[0].symbols == ["600519"]
    assert out[0].source == "eastmoney"
    assert (
        out[0].url
        == f"https://data.eastmoney.com/notices/detail/600519/{recent_code}.html"
    )

    assert out[1].external_id == older_code
    assert out[1].event_type == "restructuring"
    assert out[1].importance == 3


def test_events_empty(monkeypatch):
    monkeypatch.setattr(ev, "market_get", lambda *a, **k: {"success": True, "data": {"list": []}})
    assert ev.EventsVendor().fetch([Symbol.parse("600519")], {}) == []
