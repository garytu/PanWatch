"""組合基準/歸因結果快取:按持倉指紋快取,持倉變動即失效,空結果不快取。

重建全持倉 NAV(逐只拉 K 線)很貴,首頁又頻繁請求基準/歸因。這裡按持倉指紋快取結果,
命中時跳過行情/K 線;持倉變化指紋即變 → 重算;失敗/空結果不快取,避免凍住瞬時故障。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.modules.portfolio.portfolio_benchmark as pb
import src.platform.persistence.models as M
from src.modules.portfolio.api import accounts as accounts_api
from src.platform.persistence.database import Base

_HOLDINGS = [{"symbol": "600519", "market": "CN", "quantity": 100, "market_value": 100.0, "fx": 1.0}]


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    accounts_api._PORTFOLIO_RESULT_CACHE.clear()
    try:
        yield s
    finally:
        s.close()
        accounts_api._PORTFOLIO_RESULT_CACHE.clear()


def _add_position(db, symbol: str, qty: float):
    acc = db.query(M.Account).first()
    if not acc:
        acc = M.Account(name="t", available_funds=0, enabled=True)
        db.add(acc)
        db.flush()
    st = M.Stock(symbol=symbol, name=symbol, market="CN")
    db.add(st)
    db.flush()
    db.add(M.Position(account_id=acc.id, stock_id=st.id, cost_price=1.0, quantity=qty))
    db.commit()


def test_benchmark_result_cached(db, monkeypatch):
    """同一持倉的基準請求只計算一次,第二次命中快取。"""
    _add_position(db, "600519", 100)
    calls = {"n": 0}

    def fake_build(holdings, days=60, benchmark_code="000300"):
        calls["n"] += 1
        return {"excess_return": 1.23}

    monkeypatch.setattr(accounts_api, "_gather_holdings", lambda d: list(_HOLDINGS))
    monkeypatch.setattr(pb, "build_portfolio_benchmark", fake_build)

    r1 = accounts_api.portfolio_benchmark(days=60, benchmark="000300", db=db)
    r2 = accounts_api.portfolio_benchmark(days=60, benchmark="000300", db=db)
    assert calls["n"] == 1, f"第二次應命中快取,實際計算 {calls['n']} 次"
    assert r1 == r2 == {"excess_return": 1.23}


def test_benchmark_empty_not_cached(db, monkeypatch):
    """資料不足(build 返回空)不快取,下次仍會重算。"""
    _add_position(db, "600519", 100)
    calls = {"n": 0}

    def fake_build(*a, **k):
        calls["n"] += 1
        return None

    monkeypatch.setattr(accounts_api, "_gather_holdings", lambda d: list(_HOLDINGS))
    monkeypatch.setattr(pb, "build_portfolio_benchmark", fake_build)

    r1 = accounts_api.portfolio_benchmark(db=db)
    accounts_api.portfolio_benchmark(db=db)
    assert calls["n"] == 2, "空結果不應快取,應重算"
    assert r1.get("empty") is True


def test_benchmark_cache_invalidates_on_holdings_change(db, monkeypatch):
    """持倉變化(指紋變)後應重新計算,不返回舊快取。"""
    _add_position(db, "600519", 100)
    calls = {"n": 0}

    def fake_build(*a, **k):
        calls["n"] += 1
        return {"excess_return": float(calls["n"])}

    monkeypatch.setattr(accounts_api, "_gather_holdings", lambda d: list(_HOLDINGS))
    monkeypatch.setattr(pb, "build_portfolio_benchmark", fake_build)

    accounts_api.portfolio_benchmark(db=db)  # 計算 1,寫快取
    _add_position(db, "000001", 50)  # 持倉變化 → 指紋變
    accounts_api.portfolio_benchmark(db=db)  # 應重算
    assert calls["n"] == 2, "持倉變化後快取應失效"


def test_attribution_result_cached(db, monkeypatch):
    """歸因結果同樣按持倉指紋快取。"""
    _add_position(db, "600519", 100)
    calls = {"n": 0}

    def fake_attr(holdings, days=60, benchmark_code="000300"):
        calls["n"] += 1
        return [{"symbol": "600519", "contribution_pct": 1.0}]

    monkeypatch.setattr(accounts_api, "_gather_holdings", lambda d: list(_HOLDINGS))
    monkeypatch.setattr(pb, "build_attribution", fake_attr)

    r1 = accounts_api.portfolio_attribution(db=db)
    r2 = accounts_api.portfolio_attribution(db=db)
    assert calls["n"] == 1, f"第二次應命中快取,實際 {calls['n']} 次"
    assert r1 == r2
