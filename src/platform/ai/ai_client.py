import base64
import logging
from pathlib import Path

from openai import AsyncOpenAI
from pan_agent_token_meter import normalize_provider_usage

from src.platform.observability import otel

logger = logging.getLogger(__name__)


class AIClient:
    """OpenAI 協議相容的 AI 使用者端"""

    def __init__(self, base_url: str, api_key: str, model: str = "", proxy: str = ""):
        kwargs = {
            "base_url": base_url,
            "api_key": api_key,
        }
        if proxy:
            kwargs["http_client"] = None  # TODO: 如需代理，用 httpx 配置
        self.client = AsyncOpenAI(**kwargs)
        # 保留原始配置作為例項屬性,供需要橋接到第三方 LLM 框架的 agent 使用
        # (e.g. TradingAgents 需要 base_url+api_key 重新構造 langchain 的 LLM)
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.total_tokens_used = 0
        self.last_usage = None

    async def chat(
        self,
        system_prompt: str,
        user_content: str,
        images: list[str] | None = None,
        temperature: float | None = 0.4,
    ) -> str:
        """
        呼叫 LLM 獲取文本回復。

        Args:
            system_prompt: 系統提示詞
            user_content: 使用者輸入內容
            images: 圖片路徑列表（用於多模態，可選）
            temperature: 生成溫度
        """
        messages = [
            {"role": "system", "content": system_prompt},
        ]

        # 構建 user message
        if images:
            content_parts = [{"type": "text", "text": user_content}]
            for img_path in images:
                img_data = self._encode_image(img_path)
                if img_data:
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{img_data}"}
                    })
            messages.append({"role": "user", "content": content_parts})
        else:
            messages.append({"role": "user", "content": user_content})

        try:
            create_kwargs = {"model": self.model, "messages": messages}
            if temperature is not None:
                create_kwargs["temperature"] = temperature
            # OTel gen_ai span(預設關閉時為 no-op);token 用量在拿到 usage 後回填。
            with otel.llm_span(self.model, operation="chat") as _span:
                response = await self.client.chat.completions.create(**create_kwargs)
                # 記錄 token 用量
                if response.usage:
                    self.last_usage = normalize_provider_usage(response.usage, model=self.model)
                    self.total_tokens_used += response.usage.total_tokens
                    _span.set_response(
                        model=getattr(response, "model", None) or self.model,
                        input_tokens=response.usage.prompt_tokens,
                        output_tokens=response.usage.completion_tokens,
                    )
                    logger.debug(
                        f"Token usage: {response.usage.prompt_tokens} + "
                        f"{response.usage.completion_tokens} = {response.usage.total_tokens}"
                    )

            return response.choices[0].message.content or ""

        except Exception as e:
            logger.error(f"AI 呼叫失敗: {e}")
            raise

    async def chat_multi(
        self,
        messages: list[dict],
        temperature: float | None = 0.4,
        max_tokens: int | None = None,
    ) -> str:
        """
        多輪對話：傳入完整 messages 列表。

        Args:
            messages: [{"role": "system"/"user"/"assistant", "content": "..."}]
            temperature: 生成溫度；傳 None 時不下發該引數
                （用於 failover 對"引數不相容"錯誤的摘參重試）
        """
        try:
            create_kwargs: dict = {"model": self.model, "messages": messages}
            if temperature is not None:
                create_kwargs["temperature"] = temperature
            if max_tokens is not None:
                create_kwargs["max_tokens"] = max_tokens
            with otel.llm_span(self.model, operation="chat") as _span:
                response = await self.client.chat.completions.create(**create_kwargs)
                if response.usage:
                    self.last_usage = normalize_provider_usage(response.usage, model=self.model)
                    self.total_tokens_used += response.usage.total_tokens
                    _span.set_response(
                        model=getattr(response, "model", None) or self.model,
                        input_tokens=response.usage.prompt_tokens,
                        output_tokens=response.usage.completion_tokens,
                    )
                    logger.debug(
                        f"Token usage: {response.usage.prompt_tokens} + "
                        f"{response.usage.completion_tokens} = {response.usage.total_tokens}"
                    )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"AI 多輪對話呼叫失敗: {e}")
            raise

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        temperature: float | None = 0.4,
    ):
        """帶 tool use 的對話呼叫，返回原始 message 物件。

        temperature 傳 None 時不下發該引數（供 failover 摘參重試）。
        """
        try:
            create_kwargs: dict = {
                "model": self.model,
                "messages": messages,
                "tools": tools,
            }
            if temperature is not None:
                create_kwargs["temperature"] = temperature
            with otel.llm_span(self.model, operation="chat") as _span:
                response = await self.client.chat.completions.create(**create_kwargs)
                if response.usage:
                    self.last_usage = normalize_provider_usage(response.usage, model=self.model)
                    self.total_tokens_used += response.usage.total_tokens
                    _span.set_response(
                        model=getattr(response, "model", None) or self.model,
                        input_tokens=response.usage.prompt_tokens,
                        output_tokens=response.usage.completion_tokens,
                    )
            return response.choices[0].message
        except Exception as e:
            logger.error(f"AI tool use 呼叫失敗: {e}")
            raise

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = 0.4,
        tool_choice: str | None = None,
    ):
        """流式對話通道（stream=True），支援可選 tool use。

        非同步生成器，產出二元組事件：
        - ("token", str)：增量文本片段，邊生成邊產出；
        - ("message", dict)：流結束後產出一次完整訊息，
          形如 {"content": 全量文本, "tool_calls": [{"id", "name", "arguments"}, ...]}，
          無工具呼叫時 tool_calls 為空列表。

        呼叫方（如 chat SSE 端點）根據 tool_calls 是否為空決定繼續工具迴圈還是結束。
        """
        create_kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if temperature is not None:
            create_kwargs["temperature"] = temperature
        if tools:
            create_kwargs["tools"] = tools
        if tool_choice is not None:
            create_kwargs["tool_choice"] = tool_choice
        # OpenAI-compatible providers that support streaming usage return a
        # final usage-only chunk. Providers that reject this optional field
        # are retried without it below.
        create_kwargs["stream_options"] = {"include_usage": True}

        try:
            stream = await self.client.chat.completions.create(**create_kwargs)
        except Exception as e:
            message = str(e).lower()
            unsupported_stream_options = any(
                marker in message
                for marker in ("stream_options", "unsupported parameter", "unknown parameter")
            )
            if "stream_options" in create_kwargs and unsupported_stream_options:
                create_kwargs.pop("stream_options")
                try:
                    stream = await self.client.chat.completions.create(**create_kwargs)
                except Exception:
                    logger.error(f"AI 流式呼叫失敗: {e}")
                    raise
            else:
                logger.error(f"AI 流式呼叫失敗: {e}")
                raise

        content_parts: list[str] = []
        # OpenAI 流式協議下 tool_calls 按 index 分片下發（arguments 逐段拼接）
        tool_calls_acc: dict[int, dict] = {}
        provider_usage = None

        async for chunk in stream:
            # 部分相容服務會在末尾單發一個只含 usage 的 chunk
            usage = getattr(chunk, "usage", None)
            if usage:
                self.total_tokens_used += usage.total_tokens
                provider_usage = normalize_provider_usage(usage, model=self.model)
                self.last_usage = provider_usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            if delta.content:
                content_parts.append(delta.content)
                yield ("token", delta.content)
            for tc in delta.tool_calls or []:
                acc = tool_calls_acc.setdefault(
                    tc.index, {"id": "", "name": "", "arguments": ""}
                )
                if tc.id:
                    acc["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        acc["name"] = tc.function.name
                    if tc.function.arguments:
                        acc["arguments"] += tc.function.arguments

        yield (
            "message",
            {
                "content": "".join(content_parts),
                "tool_calls": [tool_calls_acc[i] for i in sorted(tool_calls_acc)],
                "usage": provider_usage.model_dump(mode="json") if provider_usage else None,
            },
        )

    async def list_models(self) -> list[str]:
        """透過 OpenAI 相容的 /v1/models 拉取可用模型 id 列表。"""
        resp = await self.client.models.list()
        return sorted(m.id for m in resp.data)

    def _encode_image(self, image_path: str) -> str | None:
        """將圖片檔案編碼為 base64"""
        path = Path(image_path)
        if not path.exists():
            logger.warning(f"圖片不存在: {image_path}")
            return None
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
