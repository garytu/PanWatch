"""twmd 0.2.12 instrument catalog 分頁的消費端回歸測試。

twmd 0.2.12 起 ``GET /api/v1/instruments`` 預設只回 ``limit=1000`` 的第一頁，全量目錄
有 12 萬筆以上，符號按字母排序，因此普通股（例如 TWSE:2303）不在第一頁內。本檔驗證三
個呼叫點都走 client 層的完整分頁：研究服務 ``collect``、股票清單、以及它們共用的
``TwmdClient.instruments`` 快取。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from marketdata.vendors import twmd
from src.modules.research.taiwan_research import TaiwanResearchService

FIXTURES = Path(__file__).parent.parent / "packages/marketdata/tests/fixtures/twmd"
PAGING = json.loads((FIXTURES / "synthetic" / "instrument_catalog_paging.json").read_text())
SCENARIO = PAGING["scenario"]


def catalog_rows():
    """Build the fixture catalog: every 00-prefixed warrant sorts before the target."""
    rows = [
        {
            "instrument_id": f"{SCENARIO['filler_venue']}:{SCENARIO['filler_symbol_format'].format(index=index)}",
            "symbol": SCENARIO["filler_symbol_format"].format(index=index),
            "venue": SCENARIO["filler_venue"],
            "security_type": SCENARIO["filler_security_type"],
            "is_active": True,
            "name": "認購权证",
        }
        for index in range(SCENARIO["filler_count"])
    ]
    rows.append({
        "instrument_id": SCENARIO["target_instrument_id"],
        "symbol": "2303",
        "venue": "TWSE",
        "security_type": SCENARIO["target_security_type"],
        "is_active": True,
        "name": "聯詠",
    })
    rows.append({
        "instrument_id": "TPEX:6488",
        "symbol": "6488",
        "venue": "TPEX",
        "security_type": "EQUITY",
        "is_active": True,
        "name": "網龍",
    })
    return sorted(rows, key=lambda row: row["symbol"])


class PagedTwmd:
    """Fake twmd 0.2.12 HTTP surface: paged instruments plus empty dataset reads."""

    def __init__(self, rows):
        self.rows = rows
        self.requests = []

    def get_response(self, path, **params):
        if path != "instruments":
            return [], {}
        contract = PAGING["contract"]
        limit = min(int(params.get("limit") or contract["default_limit"]), contract["max_limit"])
        offset = int(params.get("offset") or 0)
        self.requests.append((offset, limit))
        headers = {contract["total_count_header"]: str(len(self.rows))}
        return self.rows[offset:offset + limit], headers

    def patch(self, monkeypatch):
        monkeypatch.setattr(twmd.TwmdClient, "get_response", lambda _self, path, **kw: self.get_response(path, **kw))


def test_collect_resolves_an_instrument_outside_the_first_catalog_page(monkeypatch):
    """production 重現場景：TWSE:2303 不在第一頁時，十個 block 不得全是 instrument_not_found。"""
    server = PagedTwmd(catalog_rows())
    server.patch(monkeypatch)
    service = TaiwanResearchService(config={"base_url": "http://paged-catalog-research"})

    payload = service.collect(
        SCENARIO["target_instrument_id"],
        start_date="2026-10-02", end_date="2026-10-02",
        start_month="2026-08", end_month="2026-08",
        today_taipei=date(2026, 10, 7),
    )

    assert payload["instrument_id"] == SCENARIO["target_instrument_id"]
    assert server.requests == [(0, 5_000), (5_000, 5_000), (10_000, 5_000)]
    assert SCENARIO["expected_target_page_index"] == len(server.requests) - 1
    reasons = {name: block["reason"] for name, block in payload["blocks"].items()}
    assert all(reason != "instrument_not_found" for reason in reasons.values()), reasons
    assert len(payload["blocks"]) >= 10  # the whole block set resolved against the catalog


def test_collect_reports_a_truncated_catalog_as_an_error_instead_of_not_found(monkeypatch):
    """目錄被截斷時要明確失敗，不能再伪装成「這個股票不存在」。"""
    class ShortCatalog(PagedTwmd):
        def get_response(self, path, **params):
            rows, headers = super().get_response(path, **params)
            if path == "instruments":
                headers[PAGING["contract"]["total_count_header"]] = "124940"  # 宣稱比實際更多
            return rows, headers

    server = ShortCatalog(catalog_rows()[:5_000])
    server.patch(monkeypatch)
    service = TaiwanResearchService(config={"base_url": "http://truncated-catalog"})

    payload = service.collect(
        SCENARIO["target_instrument_id"], today_taipei=date(2026, 10, 7),
    )

    reasons = {block["reason"] for block in payload["blocks"].values()}
    # 研究介面的 block reason 是固定詞表，目錄截斷在該層歸為 provider_error；
    # 實際的 TwmdReadError.reason_code == "incomplete_catalog" 由 vendor 層測試斷言。
    assert reasons == {"provider_error"}
    assert all(block["status"] == "error" for block in payload["blocks"].values())


def test_stock_list_includes_instruments_outside_the_first_catalog_page(monkeypatch):
    from src.platform.marketdata import marketdata_client, stock_list

    server = PagedTwmd(catalog_rows())
    server.patch(monkeypatch)
    monkeypatch.setattr(marketdata_client, "twmd_config", lambda: {"base_url": "http://paged-catalog-list"})

    stocks = stock_list._fetch_tw_from_twmd()

    assert [stock["symbol"] for stock in stocks] == [SCENARIO["target_instrument_id"], "TPEX:6488"]
    assert all(stock["security_type"] == "EQUITY" for stock in stocks)  # 權證不進股票清單
    assert server.requests == [(0, 5_000), (5_000, 5_000), (10_000, 5_000)]


def test_research_and_stock_list_share_one_paged_catalog(monkeypatch):
    """同一個 base_url 的第二次目錄讀取應沿用 client 層 TTL 快取。"""
    from src.platform.marketdata import marketdata_client, stock_list

    server = PagedTwmd(catalog_rows())
    server.patch(monkeypatch)
    config = {"base_url": "http://paged-catalog-shared"}
    monkeypatch.setattr(marketdata_client, "twmd_config", lambda: dict(config))

    first = TaiwanResearchService(config=dict(config)).collect(
        "TPEX:6488", today_taipei=date(2026, 10, 7),
    )
    requests_after_research = list(server.requests)
    stocks = stock_list._fetch_tw_from_twmd()

    assert first["instrument_id"] == "TPEX:6488"
    assert "TPEX:6488" in [stock["symbol"] for stock in stocks]
    assert server.requests == requests_after_research  # no second paged catalog read


@pytest.mark.parametrize("instrument_id", ["TWSE:2303", "2303"])
def test_bare_and_canonical_ids_resolve_from_the_paged_catalog(monkeypatch, instrument_id):
    server = PagedTwmd(catalog_rows())
    server.patch(monkeypatch)
    service = TaiwanResearchService(config={"base_url": f"http://paged-catalog-{instrument_id}"})

    payload = service.collect(instrument_id, today_taipei=date(2026, 10, 7))

    assert payload["instrument_id"] == SCENARIO["target_instrument_id"]
    assert all(block["reason"] != "instrument_not_found" for block in payload["blocks"].values())
