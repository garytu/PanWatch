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
