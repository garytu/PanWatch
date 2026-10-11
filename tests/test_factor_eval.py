"""因子評估相關係數(Phase 2)單元測試 —— 純函式,不觸發 DB。"""

from src.modules.strategy.factor_eval import pearson, spearman


def test_pearson_perfect_positive():
    """完全正線性相關 = +1。"""
    assert abs(pearson([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]) - 1.0) < 1e-9


def test_pearson_perfect_negative():
    """完全負線性相關 = -1。"""
    assert abs(pearson([1, 2, 3, 4, 5], [10, 8, 6, 4, 2]) + 1.0) < 1e-9


def test_pearson_too_few_returns_none():
    """樣本不足 3 返回 None。"""
    assert pearson([1, 2], [1, 2]) is None


def test_pearson_zero_variance_none():
    """零方差(常量序列)返回 None。"""
    assert pearson([1, 1, 1, 1], [1, 2, 3, 4]) is None


def test_spearman_monotonic_nonlinear():
    """單調非線性關係下 Spearman = +1(秩相關)。"""
    assert abs(spearman([1, 2, 3, 4, 5], [1, 4, 9, 16, 25]) - 1.0) < 1e-9


def test_spearman_handles_ties():
    """含並列值時仍返回合法相關係數(平均秩)。"""
    r = spearman([1, 1, 2, 3], [1, 2, 2, 3])
    assert r is not None and -1.0 <= r <= 1.0
