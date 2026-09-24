"""Model-backed and deterministic context summary adapters for the assistant host."""

from __future__ import annotations

import inspect
import json
from collections.abc import Sequence
from typing import Any

from pan_agent import ContextCompressionMode, ContextSummary, ModelMessage

_SUMMARY_SYSTEM_PROMPT = """你是對話上下文壓縮器。
只輸出一個合法 JSON 物件，不要輸出 Markdown、解釋或額外文本。
JSON 必須包含欄位：goal、constraints、decisions、facts、current_state、open_items、tool_findings。
其中前六個列表欄位使用字串陣列，current_state 使用字串；只保留對後續回答有幫助的事實。
不要補造沒有出現在對話中的價格、日期、人物或決定。
"""


def _strip_json_fence(value: str) -> str:
    text = value.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        return "\n".join(lines[1:-1]).strip()
    return text


class FailoverContextSummarizer:
    """Use the host's configured failover client for structured summaries."""

    def __init__(
        self,
        client: Any,
        *,
        temperature: float = 0.1,
        max_summary_tokens: int = 800,
    ) -> None:
        self._client = client
        self._temperature = temperature
        self._max_summary_tokens = max_summary_tokens

    async def summarize(
        self,
        messages: Sequence[ModelMessage],
        *,
        mode: ContextCompressionMode,
    ) -> ContextSummary:
        transcript = "\n\n".join(
            f"[{message.role}] {message.content}" for message in messages if message.content
        )
        request_messages = [
            {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"壓縮模式：{mode.value}\n"
                    f"摘要最多使用約 {self._max_summary_tokens} 個 token。\n"
                    "請把下面的較早對話整理成可繼續使用的結構化摘要。\n\n"
                    + transcript
                ),
            },
        ]
        parameters = inspect.signature(self._client.chat_multi).parameters
        supports_max_tokens = "max_tokens" in parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        request_kwargs = {"temperature": self._temperature}
        if supports_max_tokens:
            request_kwargs["max_tokens"] = self._max_summary_tokens
        raw = await self._client.chat_multi(request_messages, **request_kwargs)
        payload = raw if isinstance(raw, dict) else json.loads(_strip_json_fence(str(raw)))
        return ContextSummary.model_validate(payload)
