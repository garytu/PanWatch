import marketdata.vendors.discovery as dv
from marketdata.types import HotBoard, HotStock


def test_hot_stocks_maps_fcodes(monkeypatch):
    # 東財 clist diff:f12=程式碼 f14=名稱 f2=最新價 f3=漲跌幅 f6=成交額 f5=成交量
    payload = {
        "data": {
            "diff": [
                {"f12": "600519", "f14": "貴州茅臺", "f2": 1700.5, "f3": 3.2, "f6": 85000000, "f5": 50000},
            ]
        }
    }
    captured = {}

    def fake_market_get(*args, **kwargs):
        captured["url"] = args[0]
        return payload

    monkeypatch.setattr(dv, "market_get", fake_market_get)
    out = dv.DiscoveryVendor().hot_stocks(market="CN", mode="turnover", limit=20)
    assert captured["url"] == "https://push2delay.eastmoney.com/api/qt/clist/get"
    assert len(out) == 1 and isinstance(out[0], HotStock)
    hs = out[0]
    assert hs.symbol == "600519"
    assert hs.market == "CN"
    assert hs.name == "貴州茅臺"
    assert hs.price == 1700.5
    assert hs.change_pct == 3.2
    assert hs.turnover == 85000000
    assert hs.volume == 50000


def test_hot_stocks_normalizes_dict_diff(monkeypatch):
    # 東財 diff 有時是 dict(按 index 為 key)而非 list,需歸一化。
    payload = {
        "data": {
            "diff": {
                "0": {"f12": "000001", "f14": "平安銀行", "f2": 12.3, "f3": -1.1, "f6": 900000, "f5": 4000},
            }
        }
    }
    monkeypatch.setattr(dv, "market_get", lambda *a, **k: payload)
    out = dv.DiscoveryVendor().hot_stocks(market="CN", mode="turnover", limit=20)
    assert len(out) == 1
    assert out[0].symbol == "000001" and out[0].name == "平安銀行"


def test_hot_boards_maps_fcodes(monkeypatch):
    # 板塊 diff:f12=板塊程式碼 f14=板塊名稱 f3=漲跌幅 f4=漲跌額 f6=成交額
    payload = {
        "data": {
            "diff": [
                {"f12": "BK0448", "f14": "白酒", "f2": 1.0, "f3": 2.5, "f4": 0.3, "f6": 12000000},
            ]
        }
    }
    monkeypatch.setattr(dv, "market_get", lambda *a, **k: payload)
    out = dv.DiscoveryVendor().hot_boards(market="CN", mode="gainers", limit=12)
    assert len(out) == 1 and isinstance(out[0], HotBoard)
    hb = out[0]
    assert hb.code == "BK0448"
    assert hb.name == "白酒"
    assert hb.change_pct == 2.5
    assert hb.change_amount == 0.3
    assert hb.turnover == 12000000


def test_board_stocks_maps_fcodes(monkeypatch):
    payload = {
        "data": {
            "diff": [
                {"f12": "600809", "f14": "山西汾酒", "f2": 200.1, "f3": 5.0, "f6": 3000000, "f5": 12000},
            ]
        }
    }
    monkeypatch.setattr(dv, "market_get", lambda *a, **k: payload)
    out = dv.DiscoveryVendor().board_stocks(board_code="BK0448", mode="gainers", limit=20)
    assert len(out) == 1 and isinstance(out[0], HotStock)
    hs = out[0]
    assert hs.symbol == "600809"
    assert hs.market == "CN"
    assert hs.name == "山西汾酒"
    assert hs.price == 200.1
    assert hs.change_pct == 5.0
    assert hs.turnover == 3000000
    assert hs.volume == 12000


def test_board_stocks_empty_code_returns_empty(monkeypatch):
    called = []
    monkeypatch.setattr(dv, "market_get", lambda *a, **k: called.append(1))
    assert dv.DiscoveryVendor().board_stocks(board_code="", mode="gainers", limit=20) == []
    assert called == []
