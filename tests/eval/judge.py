"""LLM-as-judge：語義維度評分框架（相關性/有據性/清晰度）。

設計約束：
- judge 模型配置**只從環境變數讀取**（EVAL_JUDGE_BASE_URL / EVAL_JUDGE_API_KEY /
  EVAL_JUDGE_MODEL），絕不讀使用者資料庫裡的 AI 服務配置；
- 固定模型 + 低溫（temperature=0），保證評分可復現；
- 單測裡注入 mock 使用者端，不發任何真實請求；真實執行由作者
  `EVAL_JUDGE_*=... make eval EVAL_ARGS=--judge` 觸發；
- 評分結果建議抽樣人工校準後再納入門禁參考——規則斷言（framework.py）永遠優先。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

# 評分維度（1-5 分）
JUDGE_DIMENSIONS = {
    "relevance": "相關性：回答是否切中使用者問題",
    "groundedness": "有據性：結論是否僅基於工具返回的資料，未編造價格/指標等具體數值",
    "clarity": "清晰度：表達是否簡潔、結構清楚、結論明確",
}

JUDGE_SYSTEM_PROMPT = """你是嚴格的 AI 投資助手回答評審員。

給你：使用者問題、助手可用的工具返回資料、助手的最終回答。
請按以下維度打 1-5 分（5 最好）：
- relevance（相關性）：回答是否切中使用者問題
- groundedness（有據性）：結論是否僅基於工具返回的資料；出現工具資料裡沒有的具體價格、
  指標數值即視為編造，最多 2 分；工具失敗時如實說明應得高分
- clarity（清晰度）：表達是否簡潔、結構清楚、給出明確觀點

只輸出 JSON，不要任何其他文字：
{"relevance": 1-5, "groundedness": 1-5, "clarity": 1-5, "comment": "一句話點評"}"""


@dataclass
class JudgeConfig:
    """judge 模型配置（固定模型 + 低溫）。"""

    base_url: str
    api_key: str
    model: str
    temperature: float = 0.0

    @classmethod
    def from_env(cls) -> "JudgeConfig | None":
        """從環境變數讀取；不全則返回 None（judge 環節跳過）。"""
        base_url = os.environ.get("EVAL_JUDGE_BASE_URL", "").strip()
        api_key = os.environ.get("EVAL_JUDGE_API_KEY", "").strip()
        model = os.environ.get("EVAL_JUDGE_MODEL", "").strip()
        if not (base_url and api_key and model):
            return None
        return cls(base_url=base_url, api_key=api_key, model=model)


@dataclass
class JudgeScore:
    relevance: int
    groundedness: int
    clarity: int
    comment: str = ""

    @property
    def mean(self) -> float:
        return (self.relevance + self.groundedness + self.clarity) / 3


class LLMJudge:
    """呼叫固定 judge 模型對回答打分。client 可注入（測試用 mock）。"""

    def __init__(self, config: JudgeConfig, client=None):
        self.config = config
        if client is not None:
            self.client = client
        else:
            from src.platform.ai.ai_client import AIClient

            self.client = AIClient(
                base_url=config.base_url,
                api_key=config.api_key,
                model=config.model,
            )

    async def judge(self, question: str, tool_results: list[str], answer: str) -> JudgeScore:
        """對一條 (問題, 工具資料, 回答) 打分。"""
        tool_block = "\n\n".join(tool_results) if tool_results else "（本輪未呼叫工具）"
        user_content = (
            f"## 使用者問題\n{question}\n\n"
            f"## 工具返回資料\n{tool_block}\n\n"
            f"## 助手回答\n{answer}"
        )
        raw = await self.client.chat(
            JUDGE_SYSTEM_PROMPT, user_content, temperature=self.config.temperature
        )
        return self.parse_score(raw)

    @staticmethod
    def parse_score(raw: str) -> JudgeScore:
        """解析 judge 輸出（容忍 ```json 程式碼圍欄），非法輸出拋 ValueError。"""
        text = (raw or "").strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3 and lines[-1].strip().startswith("```"):
                text = "\n".join(lines[1:-1]).strip()
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"judge 輸出不是合法 JSON: {raw[:200]!r}") from e
        if not isinstance(obj, dict):
            raise ValueError(f"judge 輸出不是 JSON 物件: {raw[:200]!r}")

        def clamp(key: str) -> int:
            try:
                return max(1, min(5, int(obj.get(key))))
            except (TypeError, ValueError) as e:
                raise ValueError(f"judge 輸出缺少/非法維度 {key}: {obj!r}") from e

        return JudgeScore(
            relevance=clamp("relevance"),
            groundedness=clamp("groundedness"),
            clarity=clamp("clarity"),
            comment=str(obj.get("comment") or ""),
        )
