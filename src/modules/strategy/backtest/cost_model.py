"""A 股交易成本模型 —— 回測(Phase 0)與模擬交易(Phase 1)共用。

成本口徑(2023-08-28 印花稅下調後):
- 印花稅:**賣出單邊** 0.05%(萬 5)
- 佣金:雙邊,預設萬 2.5,單筆最低 5 元
- 過戶費:雙邊,成交額 0.001%(滬深統一,2022-04 起)
- 滑點:可配置基點(預設 5bps),買入價上滑 / 賣出價下滑,模擬衝擊成本

滑點體現在實際成交價(fill_price),不重複計入顯式規費;顯式規費 = 佣金+印花稅+過戶費。
現金變動(cash_delta)= 買入為負、賣出為正,已扣全部成本與滑點,PnL 由買賣兩腿 cash_delta 相加得出。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostConfig:
    """成本引數(可配置;預設值貼近 A 股散戶實際)。"""

    commission_rate: float = 0.00025   # 佣金費率(雙邊)萬 2.5
    min_commission: float = 5.0        # 單筆最低佣金(元)
    stamp_duty_rate: float = 0.0005    # 印花稅(僅賣出)萬 5
    transfer_fee_rate: float = 0.00001  # 過戶費(雙邊)十萬分之 1
    slippage_bps: float = 5.0          # 滑點(基點,雙邊;5bps = 0.05%)


@dataclass(frozen=True)
class Fill:
    """一次成交的淨結果(含成本拆解,便於展示與審計)。"""

    side: str            # "buy" | "sell"
    price: float         # 名義價(訊號/行情價,未含滑點)
    fill_price: float    # 實際成交價(含滑點)
    quantity: int
    gross: float         # 實際成交額 = fill_price * quantity
    commission: float
    stamp_duty: float
    transfer_fee: float
    slippage_cost: float  # 滑點損耗 = |fill_price - price| * quantity(僅展示)
    explicit_fees: float  # 顯式規費 = commission + stamp_duty + transfer_fee
    friction: float       # 總摩擦 = explicit_fees + slippage_cost(僅展示)
    cash_delta: float     # 現金變動:buy 為負,sell 為正(已扣顯式規費;滑點含在 fill_price)


class CostModel:
    """A 股交易成本計算器。執行緒無關,可全域性複用。"""

    def __init__(self, config: CostConfig | None = None) -> None:
        self.cfg = config or CostConfig()

    def _apply_slippage(self, price: float, side: str) -> float:
        adj = price * self.cfg.slippage_bps / 10000.0
        return price + adj if side == "buy" else max(0.0, price - adj)

    def fill(self, side: str, price: float, quantity: int) -> Fill:
        """計算一筆成交的成本與現金變動。

        Args:
            side: "buy" 或 "sell"
            price: 名義價(未含滑點)
            quantity: 股數(正整數)
        """
        side = (side or "").strip().lower()
        if side not in ("buy", "sell"):
            raise ValueError(f"side 必須是 buy/sell,得到 {side!r}")
        qty = int(quantity)
        if qty <= 0 or price <= 0:
            raise ValueError(f"price/quantity 必須為正,得到 price={price} qty={quantity}")

        fill_price = self._apply_slippage(price, side)
        gross = fill_price * qty
        commission = max(gross * self.cfg.commission_rate, self.cfg.min_commission)
        stamp_duty = gross * self.cfg.stamp_duty_rate if side == "sell" else 0.0
        transfer_fee = gross * self.cfg.transfer_fee_rate
        slippage_cost = abs(fill_price - price) * qty
        explicit_fees = commission + stamp_duty + transfer_fee

        if side == "buy":
            cash_delta = -(gross + explicit_fees)
        else:
            cash_delta = gross - explicit_fees

        return Fill(
            side=side,
            price=float(price),
            fill_price=round(fill_price, 6),
            quantity=qty,
            gross=round(gross, 4),
            commission=round(commission, 4),
            stamp_duty=round(stamp_duty, 4),
            transfer_fee=round(transfer_fee, 4),
            slippage_cost=round(slippage_cost, 4),
            explicit_fees=round(explicit_fees, 4),
            friction=round(explicit_fees + slippage_cost, 4),
            cash_delta=round(cash_delta, 4),
        )

    def round_trip_pnl(
        self, entry_price: float, exit_price: float, quantity: int
    ) -> dict:
        """一買一賣的完整損益(扣全部成本)。便於單筆回測與對帳。"""
        buy = self.fill("buy", entry_price, quantity)
        sell = self.fill("sell", exit_price, quantity)
        # 現金口徑:買入流出 -cash_delta(正數),賣出流入 cash_delta
        invested = -buy.cash_delta
        proceeds = sell.cash_delta
        pnl = proceeds - invested
        pnl_pct = (pnl / invested * 100.0) if invested > 0 else 0.0
        total_cost = buy.friction + sell.friction
        return {
            "entry_price": float(entry_price),
            "exit_price": float(exit_price),
            "quantity": int(quantity),
            "invested": round(invested, 4),
            "proceeds": round(proceeds, 4),
            "pnl": round(pnl, 4),
            "pnl_pct": round(pnl_pct, 4),
            "total_cost": round(total_cost, 4),
            "buy": buy,
            "sell": sell,
        }


# 全域性預設例項(可被覆蓋配置)
DEFAULT_COST_MODEL = CostModel()
