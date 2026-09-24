"""模擬交易分市場投資比例 / 子池資金 單元測試。"""

import math
import unittest
from types import SimpleNamespace

from src.modules.paper_trading.paper_trading_engine import (
    ALL_MARKETS,
    DEFAULT_ALLOCATIONS,
    allocations_from_excluded,
    compute_market_cash,
    market_allocations_or_default,
    normalize_allocations,
)


class TestNormalizeAllocations(unittest.TestCase):
    def test_fill_missing_markets(self):
        """歸一化 — 缺失市場補 0 且覆蓋三市場"""
        out = normalize_allocations({"CN": 0.6})
        self.assertEqual(set(out.keys()), set(ALL_MARKETS))
        self.assertAlmostEqual(out["CN"], 0.6)
        self.assertEqual(out["HK"], 0.0)
        self.assertEqual(out["US"], 0.0)

    def test_clamp_negative_to_zero(self):
        """歸一化 — 負比例 clamp 為 0"""
        out = normalize_allocations({"HK": -0.2, "CN": 0.5})
        self.assertEqual(out["HK"], 0.0)
        self.assertAlmostEqual(out["CN"], 0.5)

    def test_clamp_over_one(self):
        """歸一化 — 超過 1 的比例 clamp 為 1"""
        out = normalize_allocations({"US": 1.5})
        self.assertEqual(out["US"], 1.0)

    def test_none_input(self):
        """歸一化 — 入參為 None 時三市場全 0"""
        out = normalize_allocations(None)
        self.assertEqual(out, {"CN": 0.0, "HK": 0.0, "US": 0.0})


class TestAllocationsFromExcluded(unittest.TestCase):
    def test_exclude_us_renormalizes_rest(self):
        """遷移 — 排除美股後 A/港 歸一化到合計 1"""
        out = allocations_from_excluded(["US"])
        self.assertEqual(out["US"], 0.0)
        self.assertAlmostEqual(out["CN"] + out["HK"], 1.0, places=4)
        # 0.5 / 0.3 → 0.625 / 0.375
        self.assertAlmostEqual(out["CN"], 0.625, places=4)
        self.assertAlmostEqual(out["HK"], 0.375, places=4)

    def test_empty_returns_default(self):
        """遷移 — 無排除時回落預設 50/30/20"""
        out = allocations_from_excluded([])
        for m in ALL_MARKETS:
            self.assertAlmostEqual(out[m], DEFAULT_ALLOCATIONS[m], places=4)

    def test_all_excluded_fallback_cn(self):
        """遷移 — 全部排除時兜底投 A 股"""
        out = allocations_from_excluded(["CN", "HK", "US"])
        self.assertEqual(out, {"CN": 1.0, "HK": 0.0, "US": 0.0})


class TestComputeMarketCash(unittest.TestCase):
    def test_basic(self):
        """子池現金 — 初始×比例 + 已實現 − 持倉成本"""
        # 100萬×50% + 5000 − 300000 = 205000
        self.assertAlmostEqual(
            compute_market_cash(1_000_000, 0.5, 5000, 300000), 205000.0
        )

    def test_zero_ratio(self):
        """子池現金 — 比例 0 時初始資金為 0"""
        self.assertEqual(compute_market_cash(1_000_000, 0.0, 0, 0), 0.0)

    def test_over_allocated_negative(self):
        """子池現金 — 持倉超出額度時返回負（無新倉空間）"""
        # 100萬×10% − 20萬持倉 = -10萬
        self.assertLess(compute_market_cash(1_000_000, 0.1, 0, 200000), 0)


class TestMarketAllocationsOrDefault(unittest.TestCase):
    def test_empty_falls_back_default(self):
        """帳戶比例 — 未配置時回落預設配置"""
        acc = SimpleNamespace(market_allocations=None, initial_capital=1_000_000)
        out = market_allocations_or_default(acc)
        self.assertEqual(out, dict(DEFAULT_ALLOCATIONS))

    def test_configured_is_normalized(self):
        """帳戶比例 — 已配置則歸一化返回"""
        acc = SimpleNamespace(
            market_allocations={"CN": 1.0, "HK": 0, "US": 0}, initial_capital=1_000_000
        )
        out = market_allocations_or_default(acc)
        self.assertEqual(out, {"CN": 1.0, "HK": 0.0, "US": 0.0})

    def test_sum_le_one_invariant(self):
        """帳戶比例 — 合理配置合計不超過 1"""
        acc = SimpleNamespace(
            market_allocations={"CN": 0.5, "HK": 0.3, "US": 0.2}, initial_capital=1_000_000
        )
        out = market_allocations_or_default(acc)
        self.assertTrue(math.isclose(sum(out.values()), 1.0, abs_tol=1e-6))


if __name__ == "__main__":
    unittest.main()
