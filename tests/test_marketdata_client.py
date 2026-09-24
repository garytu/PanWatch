from types import SimpleNamespace

from marketdata import Quote
import src.platform.marketdata.marketdata_client as mc


def test_quote_to_row_keys():
    q = Quote(symbol="600519", market="CN", current_price=1700.0, name="貴州茅臺",
              change_pct=1.2, volume_ratio=1.1, pe_ratio=35.0)
    row = mc._quote_to_row(q)
    assert row["symbol"] == "600519" and row["name"] == "貴州茅臺"
    assert row["current_price"] == 1700.0 and row["change_pct"] == 1.2
    assert row["volume_ratio"] == 1.1 and row["pe_ratio"] == 35.0
    # 相容舊 orchestrator dict 的關鍵鍵都在
    for k in ("symbol", "name", "market", "current_price", "change_pct",
              "change_amount", "prev_close", "open_price", "high_price",
              "low_price", "volume", "turnover", "turnover_rate",
              "circulating_market_value", "total_market_value"):
        assert k in row


def test_md_quote_rows_uses_marketdata(monkeypatch):
    class _MD:
        def quotes(self, symbols, *, market):
            return [Quote(symbol=s, market=market, current_price=9.0) for s in symbols]

    monkeypatch.setattr(mc, "get_market_data", lambda: _MD())
    rows = mc.md_quote_rows(["600519", "000001"], "CN")
    assert [r["symbol"] for r in rows] == ["600519", "000001"]
    assert all(r["current_price"] == 9.0 and r["market"] == "CN" for r in rows)


def test_db_config_provider_maps_rows(monkeypatch):
    rows = [
        SimpleNamespace(provider="tencent", priority=1, config={"k": "v"}, supports_batch=True),
        SimpleNamespace(provider="yfinance", priority=2, config=None, supports_batch=False),
    ]
    cp = mc.DbConfigProvider()
    monkeypatch.setattr(cp, "_query_rows", lambda datatype: rows)
    got = cp.sources_for("quote", "CN")
    assert [(s.vendor, s.priority, s.enabled, s.config) for s in got] == [
        ("tencent", 1, True, {"k": "v"}),
        ("yfinance", 2, True, {}),
    ]


def test_db_config_provider_skips_tencent_us_kline(monkeypatch):
    """美股 K 線跳過騰訊探測，避免每隻股票固定產生 501 後再回退。"""
    rows = [
        SimpleNamespace(provider="tencent", priority=0, config={}, supports_batch=False),
        SimpleNamespace(provider="stooq", priority=15, config={}, supports_batch=False),
    ]
    cp = mc.DbConfigProvider()
    monkeypatch.setattr(cp, "_query_rows", lambda datatype: rows)

    got = cp.sources_for("kline", "US")

    assert [s.vendor for s in got] == ["stooq"]


def test_db_config_provider_tw_market_routing(monkeypatch):
    monkeypatch.setenv("EXTERNAL_QUOTE_FEED_URL", "http://tw-feed:8088")
    monkeypatch.setenv("EXTERNAL_QUOTE_FEED_TOKEN", "test-token")
    monkeypatch.setenv("FINMIND_API_TOKEN", "test-fm-token")

    cp = mc.DbConfigProvider()

    quote_sources = cp.sources_for("quote", "TW")
    assert len(quote_sources) == 1
    assert quote_sources[0].vendor == "external_quote"
    assert quote_sources[0].config["base_url"] == "http://tw-feed:8088"
    assert quote_sources[0].config["token"] == "test-token"

    intraday_sources = cp.sources_for("intraday_kline", "TW")
    assert len(intraday_sources) == 1
    assert intraday_sources[0].vendor == "external_kline"
    assert intraday_sources[0].config["base_url"] == "http://tw-feed:8088"

    kline_sources = cp.sources_for("kline", "TW")
    assert len(kline_sources) == 1
    assert kline_sources[0].vendor == "finmind"
    assert kline_sources[0].config["token"] == "test-fm-token"
