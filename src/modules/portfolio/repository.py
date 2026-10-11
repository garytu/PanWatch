"""Portfolio persistence queries used by module services."""

from __future__ import annotations

from sqlalchemy.orm import Session

# 表由共享持久化平臺註冊；組合 repository 直接使用它們，避免模組內保留
# 一個不承載任何領域行為的 ``portfolio.models`` re-export 檔案。
from src.platform.persistence.models import PaperTradingPosition, Position, Stock


class PortfolioRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_real_positions_with_stocks(self) -> list[tuple[Position, Stock]]:
        return (
            self._session.query(Position, Stock)
            .join(Stock, Position.stock_id == Stock.id)
            .order_by(Stock.sort_order.asc(), Position.id.asc())
            .all()
        )

    def list_open_paper_positions(self) -> list[PaperTradingPosition]:
        return (
            self._session.query(PaperTradingPosition)
            .filter(PaperTradingPosition.status == "open")
            .all()
        )
