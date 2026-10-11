"""structured_output 解析 golden set（純規則，無需模型，隨 make test 常跑）。

複用並擴充 tests/test_structured_output.py 的既有資產：
圍欄/字首容錯、別名歸一、動作白名單拒絕、標籤塊提取/剝離的邊界情況。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.modules.research.signals.structured_output import (
    TAG_END,
    TAG_START,
    strip_tagged_json,
    try_extract_tagged_json,
    try_parse_action_json,
)


@dataclass
class StructuredEvalCase:
    """一條結構化輸出解析用例。

    kind:
    - action: try_parse_action_json（JSON-only 輸出）
    - tagged: try_extract_tagged_json（長文末尾標籤塊）
    - strip:  strip_tagged_json（剝離標籤塊後的正文）
    """

    id: str
    text: str
    kind: str = "action"
    expect_parsed: bool = True
    # 欄位斷言：值為普通值時做相等比較，為 callable 時做謂詞校驗
    expect_fields: dict = field(default_factory=dict)
    # kind=strip 時的期望正文
    expect_stripped: str | None = None
    notes: str = ""


def check_structured_case(case: StructuredEvalCase) -> list[str]:
    """跑一條用例，返回失敗原因列表（空即透過）。"""
    failures: list[str] = []

    if case.kind == "strip":
        actual = strip_tagged_json(case.text)
        if case.expect_stripped is not None and actual != case.expect_stripped:
            failures.append(f"剝離結果不符: 期望 {case.expect_stripped!r}, 實際 {actual!r}")
        return failures

    if case.kind == "tagged":
        obj = try_extract_tagged_json(case.text)
    else:
        obj = try_parse_action_json(case.text)

    if case.expect_parsed and obj is None:
        failures.append("期望解析成功，實際返回 None")
        return failures
    if not case.expect_parsed:
        if obj is not None:
            failures.append(f"期望解析失敗(None)，實際得到 {obj!r}")
        return failures

    for key, expected in case.expect_fields.items():
        actual = obj.get(key)
        if callable(expected):
            if not expected(actual):
                failures.append(f"欄位 {key} 校驗失敗: 實際 {actual!r}")
        elif actual != expected:
            failures.append(f"欄位 {key} 不符: 期望 {expected!r}, 實際 {actual!r}")
    return failures


STRUCTURED_CASES: list[StructuredEvalCase] = [
    # ──────── try_parse_action_json ────────
    StructuredEvalCase(
        id="s-json-prefix",
        text='\njson\n{"action":"add","action_label":"建倉","reason":"突破"}\n',
        expect_fields={"action": "add", "action_label": "建倉"},
        notes="裸 json 字首行容錯",
    ),
    StructuredEvalCase(
        id="s-fenced-json",
        text='```json\n{"action":"reduce","action_label":"減碼"}\n```',
        expect_fields={"action": "reduce"},
        notes="```json 程式碼圍欄容錯",
    ),
    StructuredEvalCase(
        id="s-fenced-nolang",
        text='```\n{"action":"hold","confidence":0.7}\n```',
        expect_fields={"action": "hold", "confidence": 0.7},
        notes="無語言標註的程式碼圍欄",
    ),
    StructuredEvalCase(
        id="s-alias-build",
        text='{"action":"build","action_label":"建倉"}',
        expect_fields={"action": "add"},
        notes="build 別名歸一化為 add",
    ),
    StructuredEvalCase(
        id="s-action-upper",
        text='{"action":"ADD","action_label":"建倉"}',
        expect_fields={"action": lambda v: str(v).lower() == "add"},
        notes="大寫 action 透過白名單校驗（保留原大小寫）",
    ),
    StructuredEvalCase(
        id="s-illegal-action",
        text='{"action":"yolo","reason":"梭哈"}',
        expect_parsed=False,
        notes="白名單外動作必須拒絕",
    ),
    StructuredEvalCase(
        id="s-json-array",
        text='[{"action":"add"}]',
        expect_parsed=False,
        notes="非 dict（陣列）必須拒絕",
    ),
    StructuredEvalCase(
        id="s-empty",
        text="",
        expect_parsed=False,
        notes="空輸入返回 None",
    ),
    StructuredEvalCase(
        id="s-broken-json",
        text='{"action":"add",',
        expect_parsed=False,
        notes="截斷 JSON 返回 None 而非拋異常",
    ),
    StructuredEvalCase(
        id="s-no-action-field",
        text='{"signal":"volume_spike","note":"放量"}',
        expect_fields={"signal": "volume_spike"},
        notes="無 action 欄位的合法 JSON 允許透過（action 為空不校驗白名單）",
    ),
    StructuredEvalCase(
        id="s-prose-not-json",
        text="今天大盤震盪，建議觀望。",
        expect_parsed=False,
        notes="純自然語言返回 None",
    ),
    # ──────── try_extract_tagged_json ────────
    StructuredEvalCase(
        id="t-tagged-ok",
        text=f'前面是分析正文。\n{TAG_START}\n{{"action":"watch","score":72}}\n{TAG_END}',
        kind="tagged",
        expect_fields={"action": "watch", "score": 72},
        notes="長文末尾標籤塊提取",
    ),
    StructuredEvalCase(
        id="t-tagged-take-last",
        text=(
            f'{TAG_START}\n{{"v":1}}\n{TAG_END}\n中間正文\n'
            f'{TAG_START}\n{{"v":2}}\n{TAG_END}'
        ),
        kind="tagged",
        expect_fields={"v": 2},
        notes="多個標籤塊取最後一個（rfind）",
    ),
    StructuredEvalCase(
        id="t-tagged-missing-end",
        text=f'正文\n{TAG_START}\n{{"v":1}}',
        kind="tagged",
        expect_parsed=False,
        notes="缺結束標籤返回 None",
    ),
    StructuredEvalCase(
        id="t-tagged-empty-payload",
        text=f"正文\n{TAG_START}\n{TAG_END}",
        kind="tagged",
        expect_parsed=False,
        notes="空 payload 返回 None",
    ),
    StructuredEvalCase(
        id="t-tagged-broken-payload",
        text=f"正文\n{TAG_START}\n{{bad json}}\n{TAG_END}",
        kind="tagged",
        expect_parsed=False,
        notes="標籤內非法 JSON 返回 None",
    ),
    # ──────── strip_tagged_json ────────
    StructuredEvalCase(
        id="strip-ok",
        text=f'結論正文。\n{TAG_START}\n{{"action":"hold"}}\n{TAG_END}',
        kind="strip",
        expect_stripped="結論正文。",
        notes="剝離標籤塊只留正文",
    ),
    StructuredEvalCase(
        id="strip-no-tag",
        text="沒有標籤塊的正文",
        kind="strip",
        expect_stripped="沒有標籤塊的正文",
        notes="無標籤塊原樣返回",
    ),
]
