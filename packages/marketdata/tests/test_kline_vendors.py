import marketdata.vendors.kline as kv
from marketdata.symbol import Symbol
from marketdata.types import Bar


def test_tencent_kline_parses(monkeypatch):
    js = 'kline_dayqfq={"data":{"sh600519":{"day":[["2026-07-01","1","3","4","0.5","100"],["2026-07-02","3","5","6","2","200"]]}}};'
    monkeypatch.setattr(kv, "market_get", lambda *a, **k: js)
    out = kv.TencentKlineVendor().fetch([Symbol.parse("600519")], {"days": 60})
    assert len(out) == 2 and isinstance(out[0], Bar)
    assert out[0].date == "2026-07-01" and out[0].close == 3.0 and out[1].volume == 200.0


def test_eastmoney_kline_parses(monkeypatch):
    payload = {"data": {"klines": ["2026-07-01,1,3,4,0.5,100", "2026-07-02,3,5,6,2,200"]}}
    monkeypatch.setattr(kv, "market_get", lambda *a, **k: payload)
    out = kv.EastmoneyKlineVendor().fetch([Symbol.parse("600519")], {"days": 60})
    assert len(out) == 2 and out[1].high == 6.0


def test_stooq_kline_parses(monkeypatch):
    csv = "Date,Open,High,Low,Close,Volume\n2026-07-01,1,4,0.5,3,100\n2026-07-02,3,6,2,5,200\n"
    monkeypatch.setattr(kv, "market_get", lambda *a, **k: csv)
    out = kv.StooqKlineVendor().fetch([Symbol.parse("AAPL")], {})
    assert len(out) == 2 and out[0].close == 3.0 and out[1].close == 5.0


def test_tencent_us_kline_resolves_exchange_suffix(monkeypatch):
    """騰訊美股日K:裸符號只回退化資料(2根)→ 自動試 .OQ/.N 字尾並記憶命中,二次直達。"""
    import json as _j

    kv._US_SUFFIX_CACHE.clear()
    calls = []

    def fake(url, **k):
        param = k["params"]["param"]
        calls.append(param)
        tsym = param.split(",")[0]
        if tsym == "usBABA.N":  # 紐交所字尾才是對的
            days = [[f"2026-07-{i:02d}", "1", "2", "3", "0.5", "10"] for i in range(1, 11)]
        elif tsym in ("usBABA.OQ", "usBABA"):  # 錯字尾/裸符號 → 退化 2 根
            days = [["2011-06-02", "1", "2", "3", "0.5", "10"],
                    ["2026-07-31", "1", "2", "3", "0.5", "10"]]
        else:
            days = []
        return "k=" + _j.dumps({"data": {tsym: {"day": days}}})

    monkeypatch.setattr(kv, "market_get", fake)
    v = kv.TencentKlineVendor()
    out = v.fetch([Symbol.parse("BABA", market="US")], {"days": 30})
    assert len(out) == 10
    assert calls[0].startswith("usBABA.OQ")  # 先試納斯達克
    assert kv._US_SUFFIX_CACHE.get("BABA") == ".N"

    calls.clear()
    out2 = v.fetch([Symbol.parse("BABA", market="US")], {"days": 30})
    assert len(out2) == 10 and len(calls) == 1  # 記憶字尾後一次請求直達
    kv._US_SUFFIX_CACHE.clear()
