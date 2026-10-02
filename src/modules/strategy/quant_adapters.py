"""量化框架介面卡介面(Phase 4 預留,輕量)。

定義統一的回測後端協議,讓未來可插入不同實現而不改上層:
- 內建(預設,永遠可用):src/core/backtest(純 Python 輕量核心,Phase 0)
- 可選升級(按路線圖,預設不安裝,保持自託管輕量):
    · vectorbt —— 向量化批量回測 / 因子網格尋參
    · rqalpha  —— A 股高保真成本撮合(印花稅/漲跌停/交易日曆)
    · qlib     —— ML 因子研究(Alpha158/360 + LightGBM 等)

此處僅宣告介面 + 探測「裝了哪些後端」,真正接入時各寫一個實現本協議的 adapter。
選型依據見 .docs/quant-framework-comparison.md。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BacktestAdapter(Protocol):
    """回測後端統一介面。內建 backtest.engine.Backtester 已滿足 run()。"""

    name: str

    def run(self, signals: list, bars_by_symbol: dict):  # noqa: D401
        """對一批訊號回測,返回帶 metrics 的結果物件。"""
        ...


_OPTIONAL_BACKENDS = (
    ("vectorbt", "vectorbt"),
    ("rqalpha", "rqalpha"),
    ("qlib", "qlib"),
)


def available_backends() -> dict[str, bool]:
    """探測可用回測後端。內建永遠可用;可選重依賴按是否已安裝返回。

    供 UI / 檔案展示當前環境裝了哪些後端,不觸發任何安裝。
    """
    backends: dict[str, bool] = {"builtin": True}
    for module_name, key in _OPTIONAL_BACKENDS:
        try:
            __import__(module_name)
            backends[key] = True
        except Exception:
            backends[key] = False
    return backends
