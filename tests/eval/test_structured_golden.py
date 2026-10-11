"""structured_output 解析 golden set 全量回歸（純規則，隨 make test 常跑）。"""

import pytest

from tests.eval.cases.structured_cases import STRUCTURED_CASES, check_structured_case


@pytest.mark.parametrize("case", STRUCTURED_CASES, ids=[c.id for c in STRUCTURED_CASES])
def test_structured_golden_case(case):
    """結構化輸出解析 golden set 用例逐條迴歸"""
    failures = check_structured_case(case)
    assert failures == [], f"[{case.id}] {case.notes}: {failures}"
