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
from datetime import date, datetime
from math import isfinite
from typing import Any, Mapping


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


@dataclass(frozen=True)
class DatedCostResolution:
    """A cost model resolved from explicit instrument/date authority."""

    status: str
    reason_code: str
    instrument_id: str
    session_date: str
    tax_category: str
    authority: dict[str, Any]
    cost_model: CostModel | None

    @property
    def net_available(self) -> bool:
        return self.status == "AVAILABLE" and self.cost_model is not None


def _policy_value(policy: Mapping[str, Any] | object, name: str, default=None):
    if isinstance(policy, Mapping):
        return policy.get(name, default)
    return getattr(policy, name, default)


def _strict_policy_date(value: Any, label: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        raise ValueError(f"{label}_invalid")
    if isinstance(value, date):
        return value
    raw = str(value)
    try:
        parsed = date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{label}_invalid") from exc
    if parsed.isoformat() != raw:
        raise ValueError(f"{label}_invalid")
    return parsed


def resolve_dated_cost_model(
    *,
    instrument_id: str,
    session_date: date | str,
    authority: Mapping[str, Any] | object | None,
) -> DatedCostResolution:
    """Resolve net-cost inputs only from complete, dated classification authority.

    This strict v2 path never falls back to `security_type`, symbol suffixes, or
    today's date. Legacy callers can continue using `cost_model_for_market`.
    """
    canonical_id = str(instrument_id or "").strip()
    try:
        day = _strict_policy_date(session_date, "session_date")
    except ValueError:
        day = None
    day_text = day.isoformat() if day else str(session_date or "")
    if not canonical_id or not day:
        return DatedCostResolution(
            "UNKNOWN", "cost_authority_missing", canonical_id, day_text,
            "UNKNOWN", {}, None,
        )
    if authority is None:
        return DatedCostResolution(
            "UNKNOWN", "cost_authority_missing", canonical_id, day_text,
            "UNKNOWN", {}, None,
        )

    values = {
        name: _policy_value(authority, name)
        for name in (
            "instrument_id", "tax_category", "classification_authority",
            "classification_source", "policy_version", "effective_from",
            "effective_to", "stamp_duty_rate", "commission_rate",
            "minimum_commission", "transfer_fee_rate", "slippage_bps",
            "evidence_hash",
        )
    }
    if str(values["instrument_id"] or "").strip() != canonical_id:
        return DatedCostResolution(
            "UNKNOWN", "cost_authority_instrument_mismatch", canonical_id,
            day_text, str(values["tax_category"] or "UNKNOWN"), values, None,
        )
    category = str(values["tax_category"] or "UNKNOWN").strip().upper()
    if category in {"", "UNKNOWN"}:
        return DatedCostResolution(
            "UNKNOWN", "cost_authority_missing", canonical_id,
            day_text, "UNKNOWN", values, None,
        )
    try:
        effective_from = _strict_policy_date(values["effective_from"], "effective_from")
        effective_to = _strict_policy_date(values["effective_to"], "effective_to")
    except ValueError:
        effective_from = effective_to = None
    if (
        not str(values["classification_authority"] or "").strip()
        or not str(values["classification_source"] or "").strip()
        or not str(values["policy_version"] or "").strip()
        or effective_from is None
        or (effective_to is not None and effective_to < effective_from)
    ):
        return DatedCostResolution(
            "UNKNOWN", "cost_authority_invalid", canonical_id, day_text,
            category or "UNKNOWN", values, None,
        )
    if day < effective_from or (effective_to is not None and day > effective_to):
        return DatedCostResolution(
            "UNKNOWN", "cost_authority_date_mismatch", canonical_id,
            day_text, category, values, None,
        )

    rates: dict[str, float] = {}
    for key in (
        "stamp_duty_rate", "commission_rate", "minimum_commission",
        "transfer_fee_rate", "slippage_bps",
    ):
        value = values[key]
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = float("nan")
        if not isfinite(parsed) or parsed < 0:
            return DatedCostResolution(
                "UNKNOWN", "cost_authority_invalid", canonical_id,
                day_text, category, values, None,
            )
        rates[key] = parsed

    captured = {
        **values,
        "tax_category": category,
        "effective_from": effective_from.isoformat(),
        "effective_to": effective_to.isoformat() if effective_to else None,
    }
    return DatedCostResolution(
        "AVAILABLE",
        "",
        canonical_id,
        day_text,
        category,
        captured,
        CostModel(CostConfig(
            stamp_duty_rate=rates["stamp_duty_rate"],
            commission_rate=rates["commission_rate"],
            min_commission=rates["minimum_commission"],
            transfer_fee_rate=rates["transfer_fee_rate"],
            slippage_bps=rates["slippage_bps"],
        )),
    )


def load_dated_cost_authority(db, *, instrument_id: str, session_date: date | str):
    """Load a single effective policy row; ambiguous/missing rows stay unknown."""
    try:
        day = _strict_policy_date(session_date, "session_date")
    except ValueError:
        return None
    if day is None:
        return None
    from src.platform.persistence.models import InstrumentCostAuthority

    day_text = day.isoformat()
    rows = (
        db.query(InstrumentCostAuthority)
        .filter(
            InstrumentCostAuthority.instrument_id == str(instrument_id or "").strip(),
            InstrumentCostAuthority.effective_from <= day_text,
            (InstrumentCostAuthority.effective_to.is_(None)
             | (InstrumentCostAuthority.effective_to >= day_text)),
        )
        .order_by(InstrumentCostAuthority.effective_from.desc(), InstrumentCostAuthority.id.desc())
        .limit(2)
        .all()
    )
    return rows[0] if len(rows) == 1 else None


# 全域性預設例項(可被覆蓋配置)
DEFAULT_COST_MODEL = CostModel()


def cost_model_for_market(market: str, symbol: str = "", *, security_type: str = "EQUITY") -> CostModel:
    """TW stock swing-trade assumptions; broker commissions are configurable.

    Tax: https://www.twse.com.tw/en/about/company/guide.html
    ETF: https://www.twse.com.tw/en/products/securities/etf/overview/rules.html
    Same-day stock-tax relief is deliberately not assumed by the swing simulator.
    """
    if market != "TW":
        return DEFAULT_COST_MODEL
    from datetime import date
    from src.platform.runtime.config import Settings
    settings = Settings()
    code = symbol.split(":")[-1].split(".")[0]
    tax = 0.001 if security_type == "ETF" else 0.003
    # Taiwan bond ETF codes end in B; the exemption currently runs through 2026.
    if security_type == "ETF" and code.endswith("B") and date.today().year <= 2026:
        tax = 0.0
    return CostModel(CostConfig(
        commission_rate=settings.tw_commission_rate,
        min_commission=settings.tw_min_commission,
        stamp_duty_rate=tax, transfer_fee_rate=0.0,
    ))


def trading_lot(market: str) -> int:
    if market == "TW":
        from src.platform.runtime.config import Settings
        lot = Settings().tw_paper_lot_size
        if lot not in {1, 1000}:
            raise ValueError("TW_PAPER_LOT_SIZE must be 1 (odd lots) or 1000 (regular lots)")
        return lot
    return 100
