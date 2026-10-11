"""Portfolio read-model use cases shared by HTTP and assistant callers."""

from __future__ import annotations

from .repository import PortfolioRepository


class PortfolioService:
    def __init__(self, repository: PortfolioRepository) -> None:
        self._repository = repository

    def build_assistant_summary(self) -> str:
        sections: list[str] = []
        real_lines = [
            f"- {stock.name}({stock.market}:{stock.symbol}) {position.quantity}股 "
            f"成本{position.cost_price} 風格{position.trading_style or '波段'}"
            for position, stock in self._repository.list_real_positions_with_stocks()
        ]
        if real_lines:
            sections.append("實盤持倉：\n" + "\n".join(real_lines))

        paper_lines: list[str] = []
        for position in self._repository.list_open_paper_positions():
            pnl = f" 未實現獲利{position.unrealized_pnl:.1f}" if position.unrealized_pnl else ""
            stop = f" 停損{position.stop_loss}" if position.stop_loss else ""
            target = f" 目標{position.target_price}" if position.target_price else ""
            paper_lines.append(
                f"- {position.stock_name or position.stock_symbol}({position.stock_market}:{position.stock_symbol}) "
                f"{position.quantity}股 入場價{position.entry_price}{stop}{target}{pnl}"
            )
        if paper_lines:
            sections.append("模擬交易持倉：\n" + "\n".join(paper_lines))
        return "\n\n".join(sections)

