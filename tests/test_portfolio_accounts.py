"""持倉帳戶 HTTP 用例的持久化邊界迴歸測試。"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base
from src.platform.persistence.models import Account, Position, Stock  # noqa: F401 - 註冊關係模型


def test_delete_position_logs_relationship_names_before_commit():
    """刪除持倉後仍能完成回應，日誌不能訪問已脫離會話的關係物件。"""
    from src.modules.portfolio.api.accounts import delete_position

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    account = Account(name="測試帳戶")
    stock = Stock(symbol="600519", name="貴州茅臺", market="CN")
    session.add_all([account, stock])
    session.flush()
    position = Position(
        account_id=account.id,
        stock_id=stock.id,
        cost_price=1500,
        quantity=100,
    )
    session.add(position)
    session.commit()

    result = delete_position(position.id, session)

    assert result == {"success": True}
    assert session.get(Position, position.id) is None
    session.close()
    engine.dispose()


def test_delete_position_does_not_read_detached_relationships_after_delete():
    """關係物件在刪除提交後不可用時，刪除介面仍應正常返回。"""
    from src.modules.portfolio.api.accounts import delete_position

    class Relation:
        def __init__(self, name: str, owner: "FakePosition"):
            self.name = name
            self._owner = owner

        def __getattribute__(self, attribute: str):
            if attribute == "name" and object.__getattribute__(self, "_owner").detached:
                raise AssertionError("刪除提交後不應再訪問懶載入關係")
            return object.__getattribute__(self, attribute)

    class FakePosition:
        id = 7

        def __init__(self):
            self.detached = False
            self.account = Relation("測試帳戶", self)
            self.stock = Relation("貴州茅臺", self)

    class FakeQuery:
        def __init__(self, position):
            self.position = position

        def filter(self, _condition):
            return self

        def first(self):
            return self.position

    class FakeSession:
        def __init__(self, position):
            self.position = position

        def query(self, _model):
            return FakeQuery(self.position)

        def delete(self, position):
            position.detached = True

        def commit(self):
            return None

    position = FakePosition()

    assert delete_position(position.id, FakeSession(position)) == {"success": True}


def test_portfolio_summary_and_holdings_primary_currency_twd(monkeypatch):
    """驗證基準貨幣為 TWD 時，台股持倉為本幣（匯率 1.0），美股正確折算為新台幣。"""
    from src.modules.portfolio.api.accounts import (
        _gather_holdings,
        get_portfolio_summary,
    )

    monkeypatch.setenv("PRIMARY_CURRENCY", "TWD")
    monkeypatch.setattr(
        "src.modules.portfolio.api.accounts.get_usd_twd_rate",
        lambda: 32.0,
    )

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    account = Account(name="新台幣綜合投資帳戶", available_funds=100000.0, enabled=True)
    stock_tw = Stock(symbol="2330", name="台積電", market="TW")
    stock_us = Stock(symbol="NVDA", name="NVIDIA", market="US")
    session.add_all([account, stock_tw, stock_us])
    session.flush()

    # 台股: 成本 1000 TWD, 100 股 => 100,000 TWD
    pos_tw = Position(
        account_id=account.id,
        stock_id=stock_tw.id,
        cost_price=1000.0,
        quantity=100,
    )
    # 美股: 成本 100 USD, 10 股 => 1,000 USD * 32.0 = 32,000 TWD
    pos_us = Position(
        account_id=account.id,
        stock_id=stock_us.id,
        cost_price=100.0,
        quantity=10,
    )
    session.add_all([pos_tw, pos_us])
    session.commit()

    summary = get_portfolio_summary(include_quotes=False, db=session)
    assert summary["base_currency"] == "TWD"
    assert summary["currency_symbol"] == "NT$"
    assert len(summary["accounts"]) == 1
    acc_summary = summary["accounts"][0]
    # 總成本: 100,000 + 32,000 = 132,000 TWD
    assert acc_summary["total_cost"] == 132000.0

    pos_tw_data = next(p for p in acc_summary["positions"] if p["symbol"] == "2330")
    assert pos_tw_data["exchange_rate"] is None  # 台股非外幣

    pos_us_data = next(p for p in acc_summary["positions"] if p["symbol"] == "NVDA")
    assert pos_us_data["exchange_rate"] == 32.0  # 美股折算匯率

    holdings = _gather_holdings(session)
    assert len(holdings) == 2
    h_tw = next(h for h in holdings if h["symbol"] == "2330")
    assert h_tw["fx"] == 1.0
    assert h_tw["market_value"] == 100000.0

    h_us = next(h for h in holdings if h["symbol"] == "NVDA")
    assert h_us["fx"] == 32.0
    assert h_us["market_value"] == 32000.0

    session.close()
    engine.dispose()


def test_portfolio_summary_and_holdings_primary_currency_cny(monkeypatch):
    """驗證基準貨幣為 CNY 時，台股持倉折算為人民幣。"""
    from src.modules.portfolio.api.accounts import (
        _gather_holdings,
        get_portfolio_summary,
    )

    monkeypatch.setenv("PRIMARY_CURRENCY", "CNY")
    monkeypatch.setattr(
        "src.modules.portfolio.api.accounts.get_twd_cny_rate",
        lambda: 0.22,
    )

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    account = Account(name="人民幣帳戶", available_funds=50000.0, enabled=True)
    stock = Stock(symbol="2330", name="台積電", market="TW")
    session.add_all([account, stock])
    session.flush()

    # 成本 1000 TWD, 數量 100 股
    pos = Position(
        account_id=account.id,
        stock_id=stock.id,
        cost_price=1000.0,
        quantity=100,
    )
    session.add(pos)
    session.commit()

    summary = get_portfolio_summary(include_quotes=False, db=session)
    assert summary["base_currency"] == "CNY"
    assert summary["currency_symbol"] == "¥"
    acc_summary = summary["accounts"][0]
    # cost_cny = 1000 * 100 * 0.22 = 22000.0
    assert acc_summary["total_cost"] == 22000.0
    pos_data = acc_summary["positions"][0]
    assert pos_data["market"] == "TW"
    assert pos_data["exchange_rate"] == 0.22

    holdings = _gather_holdings(session)
    assert len(holdings) == 1
    assert holdings[0]["symbol"] == "2330"
    assert holdings[0]["market"] == "TW"
    assert holdings[0]["fx"] == 0.22
    assert holdings[0]["market_value"] == 22000.0

    session.close()
    engine.dispose()
