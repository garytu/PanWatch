"""AI 對話 API 端點。"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.modules.assistant.chat_planner import (
    run_portfolio_diagnosis,
    should_use_planning,
)
from src.modules.assistant.legacy_chat_tools import (
    CHAT_TOOLS as LEGACY_CHAT_TOOLS,
)
from src.modules.assistant.legacy_chat_tools import (
    build_portfolio_context,
    build_stock_context,
    build_watchlist_context,
    execute_chat_tool,
    fetch_realtime_context,
    fetch_technical_context,
)
from src.modules.assistant.prompt import ASSISTANT_SYSTEM_PROMPT as SYSTEM_PROMPT
from src.modules.assistant.repository import AssistantRepository
from src.platform.ai.ai_failover import get_configured_failover_client
from src.platform.events.sse import SSEStream, chat_stream_hub
from src.platform.persistence.database import SessionLocal, get_db
from src.platform.persistence.models import (
    ChatConversation,
    ChatMessage,
    Position,
    Stock,
    StockSuggestion,
)

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_HISTORY_MESSAGES = 20
MAX_TOOL_ROUNDS = 5

# ──────────────── Shared legacy tool boundary ────────────────
# The route retains private aliases so its stream code and tests keep their
# contract; reusable implementations live outside this HTTP router.
CHAT_TOOLS = LEGACY_CHAT_TOOLS
_build_watchlist_context = build_watchlist_context
_execute_tool = execute_chat_tool
_get_ai_client = get_configured_failover_client
_build_stock_context = build_stock_context
_build_portfolio_context = build_portfolio_context
_fetch_realtime_context = fetch_realtime_context
_fetch_technical_context = fetch_technical_context


class CreateConversationBody(BaseModel):
    stock_symbol: str | None = None
    stock_market: str | None = None
    initial_context: str | None = None


class SendMessageBody(BaseModel):
    content: str

@router.get("/suggested-questions")
def suggested_questions(
    symbol: str = Query(..., description="股票程式碼"),
    market: str = Query("CN", description="市場"),
    db: Session = Depends(get_db),
):
    """根據股票當前狀態生成推薦問題（純模板，不調 AI）。"""
    questions: list[str] = []

    # 查最近建議
    latest_suggestion = (
        db.query(StockSuggestion)
        .filter(
            StockSuggestion.stock_symbol == symbol,
            StockSuggestion.stock_market == market,
        )
        .order_by(StockSuggestion.created_at.desc())
        .first()
    )
    if latest_suggestion:
        action = (latest_suggestion.action or "").lower()
        label = latest_suggestion.action_label or latest_suggestion.action or ""
        if action in ("buy", "add"):
            questions.append(f"最新的「{label}」訊號可靠嗎？入場時機如何？")
        elif action in ("sell", "reduce"):
            questions.append(f"最新給出了「{label}」建議，現在該操作嗎？")
        elif action == "alert":
            questions.append("最近的異動提醒是什麼情況？需要關注嗎？")

    # 查持倉（Position 透過 stock_id 關聯 Stock 表）
    has_position = (
        db.query(Position)
        .join(Stock, Position.stock_id == Stock.id)
        .filter(Stock.symbol == symbol, Stock.market == market)
        .first()
    ) is not None
    if has_position:
        questions.append("當前持倉該繼續持有還是考慮減碼？")
    else:
        questions.append("現在適合建倉嗎？")

    # 通用問題
    questions.append("分析近期走勢和關鍵支撐壓力位")
    questions.append("有什麼值得關注的訊息或事件？")

    return {"questions": questions[:5]}


@router.post("/conversations")
def create_conversation(
    body: CreateConversationBody | None = None,
    db: Session = Depends(get_db),
):
    conv = ChatConversation(
        stock_symbol=body.stock_symbol if body else None,
        stock_market=body.stock_market if body else None,
        initial_context=body.initial_context if body else None,
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return {
        "id": conv.id,
        "title": conv.title or "",
        "stock_symbol": conv.stock_symbol,
        "stock_market": conv.stock_market,
        "created_at": str(conv.created_at or ""),
    }


@router.get("/conversations")
def list_conversations(
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(ChatConversation)
        .order_by(ChatConversation.updated_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": c.id,
            "title": c.title or "",
            "stock_symbol": c.stock_symbol,
            "stock_market": c.stock_market,
            "created_at": str(c.created_at or ""),
        }
        for c in rows
    ]


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, db: Session = Depends(get_db)):
    conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(404, "對話不存在")
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    return {
        "conversation": {
            "id": conv.id,
            "title": conv.title or "",
            "stock_symbol": conv.stock_symbol,
            "stock_market": conv.stock_market,
            "created_at": str(conv.created_at or ""),
        },
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": str(m.created_at or ""),
            }
            for m in messages
        ],
    }


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: int, db: Session = Depends(get_db)):
    conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(404, "對話不存在")
    db.query(ChatMessage).filter(ChatMessage.conversation_id == conversation_id).delete()
    db.delete(conv)
    db.commit()
    return {"ok": True}


def _save_user_message(db: Session, conv: ChatConversation, content: str) -> ChatMessage:
    """儲存使用者訊息並按需生成對話標題（流式/非流式共用）。"""
    user_msg = ChatMessage(
        conversation_id=conv.id,
        role="user",
        content=content,
    )
    db.add(user_msg)

    # 更新對話標題（首條訊息取前 20 字）
    if not conv.title:
        conv.title = content[:20]

    db.commit()
    db.refresh(user_msg)
    return user_msg


async def _build_messages_for_ai(db: Session, conv: ChatConversation) -> list[dict]:
    """構建發給模型的完整 messages（system prompt + 歷史 + 資料上下文，流式/非流式共用）。"""
    messages_for_ai: list[dict] = []

    # System prompt
    system_content = SYSTEM_PROMPT

    # 繫結股票提示
    if conv.stock_symbol and conv.stock_market:
        system_content += f"\n\n當前對話關聯股票：{conv.stock_market}:{conv.stock_symbol}"

    # 前端頁面快照（對話建立時傳入）
    if conv.initial_context:
        system_content += "\n\n--- 使用者頁面快照（對話建立時） ---\n" + conv.initial_context

    messages_for_ai.append({"role": "system", "content": system_content})

    # 歷史訊息
    history = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conv.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    recent = history[-MAX_HISTORY_MESSAGES:] if len(history) > MAX_HISTORY_MESSAGES else history
    for m in recent:
        if m.role in ("user", "assistant"):
            messages_for_ai.append({"role": m.role, "content": m.content})

    # 注入基礎上下文（持倉 + 繫結股票的行情/建議）
    context_parts: list[str] = []

    # 使用者持倉
    portfolio_ctx = _build_portfolio_context(db)
    if portfolio_ctx:
        context_parts.append(portfolio_ctx)

    # 繫結股票的即時資料
    if conv.stock_symbol and conv.stock_market:
        realtime = await _fetch_realtime_context(conv.stock_symbol, conv.stock_market)
        if realtime:
            context_parts.append(realtime)
        technical = await _fetch_technical_context(conv.stock_symbol, conv.stock_market)
        if technical:
            context_parts.append(technical)
        stock_ctx = _build_stock_context(db, conv.stock_symbol, conv.stock_market)
        if stock_ctx:
            context_parts.append(stock_ctx)

    if context_parts:
        # 把上下文追加到 system message
        messages_for_ai[0]["content"] += "\n\n--- 當前資料 ---\n" + "\n\n".join(context_parts)

    return messages_for_ai


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: int,
    body: SendMessageBody,
):
    """傳送訊息並獲取 AI 回覆（非流式，保留作相容與降級兜底）。"""
    db = SessionLocal()
    try:
        conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()
        if not conv:
            raise HTTPException(404, "對話不存在")

        _save_user_message(db, conv, body.content)
        messages_for_ai = await _build_messages_for_ai(db, conv)

        # 呼叫 AI（帶 tool use，用於按需獲取更多資料；主模型失敗自動 failover）
        ai_client = _get_ai_client(db, conv.ai_model_id)
        ai_response = ""
        try:
            for _round in range(MAX_TOOL_ROUNDS):
                try:
                    response_msg = await ai_client.chat_with_tools(
                        messages_for_ai, tools=CHAT_TOOLS, temperature=0.5,
                    )
                except Exception:
                    # 模型不支援 tool use → 直接用 chat_multi
                    logger.info("Tool use 不可用，使用普通對話")
                    ai_response = await ai_client.chat_multi(messages_for_ai, temperature=0.5)
                    break

                if not response_msg.tool_calls:
                    ai_response = response_msg.content or ""
                    break

                # 執行 tool calls
                messages_for_ai.append({
                    "role": "assistant",
                    "content": response_msg.content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in response_msg.tool_calls
                    ],
                })

                for tc in response_msg.tool_calls:
                    tool_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    logger.info(f"Tool call: {tc.function.name}({tool_args})")
                    result = await _execute_tool(db, tc.function.name, tool_args)
                    messages_for_ai.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
            else:
                ai_response = response_msg.content or "抱歉，處理輪次過多，請精簡問題再試。"

        except Exception as e:
            logger.error(f"AI 對話失敗: {e}")
            ai_response = f"抱歉，AI 服務暫時不可用：{e}"

        # 儲存 AI 回覆
        assistant_msg = ChatMessage(
            conversation_id=conversation_id,
            role="assistant",
            content=ai_response,
        )
        db.add(assistant_msg)

        # 更新對話時間
        conv.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(assistant_msg)

        return {
            "id": assistant_msg.id,
            "role": "assistant",
            "content": assistant_msg.content,
            "created_at": str(assistant_msg.created_at or ""),
        }
    finally:
        db.close()


# ──────────────── SSE 流式對話 ────────────────
#
# 事件分型（均帶自增 id，供 Last-Event-ID 續推）：
# - meta:            {stream_id, conversation_id, user_message_id} 首條，供斷線重連定位流
# - token:           {text} 增量文本；工具呼叫輪的過渡性文本也會流出，前端在收到
#                    tool_call_start 時應清空當前緩衝（最終落庫的只有末輪迴答）
# - tool_call_start: {name, arguments} 模型決定呼叫工具（前端視覺化"正在查詢…"）
# - tool_result:     {name, ok, preview} 工具執行完成（preview 截斷，完整結果只進模型上下文）
# - done:            {message_id, content, created_at} 最終回答（已落庫）
# - error:           {message} AI 服務異常（錯誤文案同樣落庫，行為與非流式端點一致）
#
# 生成任務與 SSE 連線解耦：任務往 SSEStream 緩衝推事件，連線斷開不影響生成與落庫；
# 前端可用 GET /chat/streams/{stream_id} + Last-Event-ID 續推。

TOOL_RESULT_PREVIEW_CHARS = 200


async def _run_chat_stream_task(
    conversation_id: int,
    stream: SSEStream,
    task_id: int | None = None,
) -> None:
    """後臺執行對話生成（工具迴圈 + token 流），事件推入 stream。"""
    db = SessionLocal()
    task_repository = AssistantRepository(db)
    try:
        conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()
        if not conv:
            await stream.publish("error", {"message": "對話不存在"})
            return

        messages_for_ai = await _build_messages_for_ai(db, conv)
        ai_client = _get_ai_client(db, conv.ai_model_id)
        ai_response = ""

        # P2 試點:識別"全面診斷持倉"意圖 → 走計劃驅動(複用工具執行器,plan 事件推前端)
        latest_user = next(
            (m.get("content") or "" for m in reversed(messages_for_ai) if m.get("role") == "user"),
            "",
        )
        if should_use_planning(latest_user):
            try:
                ai_response = await run_portfolio_diagnosis(
                    db, stream, ai_client, _execute_tool
                )
            except Exception as e:
                logger.error(f"計劃驅動診斷失敗: {e}")
                ai_response = f"抱歉，持倉診斷失敗：{e}"
                await stream.publish("error", {"message": str(e)})
        else:
            try:
                final_msg: dict | None = None
                for _round in range(MAX_TOOL_ROUNDS):
                    final_msg = None
                    try:
                        async for kind, payload in ai_client.chat_stream(
                            messages_for_ai, tools=CHAT_TOOLS, temperature=0.5,
                        ):
                            if kind == "token":
                                await stream.publish("token", {"text": payload})
                            else:
                                final_msg = payload
                    except Exception:
                        # 模型不支援 tool use / 流式 → 降級為普通對話（與非流式端點同策略）
                        logger.info("流式 tool use 不可用，降級為普通對話")
                        ai_response = await ai_client.chat_multi(messages_for_ai, temperature=0.5)
                        await stream.publish("token", {"text": ai_response})
                        break

                    tool_calls = (final_msg or {}).get("tool_calls") or []
                    if not tool_calls:
                        ai_response = (final_msg or {}).get("content") or ""
                        break

                    # 有工具呼叫：把 assistant 訊息 + 工具結果追加進上下文，進入下一輪
                    messages_for_ai.append({
                        "role": "assistant",
                        "content": (final_msg or {}).get("content") or None,
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {"name": tc["name"], "arguments": tc["arguments"]},
                            }
                            for tc in tool_calls
                        ],
                    })
                    for tc in tool_calls:
                        try:
                            tool_args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                        except json.JSONDecodeError:
                            tool_args = {}
                        logger.info(f"Tool call(stream): {tc['name']}({tool_args})")
                        await stream.publish(
                            "tool_call_start", {"name": tc["name"], "arguments": tool_args}
                        )
                        result = await _execute_tool(db, tc["name"], tool_args)
                        await stream.publish(
                            "tool_result",
                            {
                                "name": tc["name"],
                                "ok": not result.startswith("工具執行出錯"),
                                "preview": (result or "")[:TOOL_RESULT_PREVIEW_CHARS],
                            },
                        )
                        if task_id is not None:
                            task_repository.record_tool_completed(
                                task_id,
                                call_id=tc["id"],
                                tool_name=tc["name"],
                                summary=(result or "")[:TOOL_RESULT_PREVIEW_CHARS],
                            )
                        messages_for_ai.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result,
                        })
                else:
                    ai_response = (final_msg or {}).get("content") or "抱歉，處理輪次過多，請精簡問題再試。"

            except Exception as e:
                logger.error(f"AI 流式對話失敗: {e}")
                ai_response = f"抱歉，AI 服務暫時不可用：{e}"
                await stream.publish("error", {"message": str(e)})

        # 落庫（無論連線是否還在，結果照常持久化）
        assistant_msg = ChatMessage(
            conversation_id=conversation_id,
            role="assistant",
            content=ai_response,
        )
        db.add(assistant_msg)
        conv.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(assistant_msg)
        if task_id is not None:
            task_repository.finish_task(
                task_id,
                status="completed",
                final_message_id=assistant_msg.id,
            )

        await stream.publish("done", {
            "message_id": assistant_msg.id,
            "content": ai_response,
            "created_at": str(assistant_msg.created_at or ""),
            # 實際使用的模型標籤(failover 後可能非主模型),供前端透明展示
            "model_label": getattr(ai_client, "used_model_label", ""),
        })
    except Exception as e:
        logger.error(f"對話流式任務異常: {e}")
        try:
            await stream.publish("error", {"message": str(e)})
        except Exception:
            pass
        if task_id is not None:
            try:
                task_repository.finish_task(
                    task_id,
                    status="failed",
                    final_message_id=None,
                    error_code="chat_stream_failed",
                )
            except Exception:
                pass
    finally:
        await stream.finish()
        db.close()


def _sse_response(stream: SSEStream, after_seq: int = 0) -> StreamingResponse:
    """把 SSEStream 包成 text/event-stream 回應（回應包裝中介軟體對該型別直通）。"""
    return StreamingResponse(
        stream.subscribe(after_seq=after_seq),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # 停用 nginx 等反代的緩衝，保證事件即時下發
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/conversations/{conversation_id}/messages/stream")
async def send_message_stream(
    conversation_id: int,
    body: SendMessageBody,
):
    """傳送訊息並以 SSE 流式返回 AI 回覆（token 流 + 工具過程可視）。

    非流式端點 POST /messages 保留不動，前端在流式失敗時降級使用。
    """
    db = SessionLocal()
    try:
        conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()
        if not conv:
            raise HTTPException(404, "對話不存在")
        user_msg = _save_user_message(db, conv, body.content)
        user_message_id = user_msg.id
        task = AssistantRepository(db).create_task(
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            context={
                "stock_symbol": conv.stock_symbol,
                "stock_market": conv.stock_market,
                "initial_context": conv.initial_context or "",
            },
        )
        task_id = task.id
    finally:
        db.close()

    stream = chat_stream_hub.create()
    # meta 事件放最前：告知 stream_id，斷線後可 GET /chat/streams/{stream_id} 續推
    await stream.publish("meta", {
        "stream_id": stream.stream_id,
        "conversation_id": conversation_id,
        "user_message_id": user_message_id,
        "task_id": task_id,
    })
    # 生成任務獨立執行，不隨本次回應連線斷開而中止
    asyncio.create_task(_run_chat_stream_task(conversation_id, stream, task_id))
    return _sse_response(stream)


@router.get("/streams/{stream_id}")
async def resume_message_stream(
    stream_id: str,
    request: Request,
    last_event_id: int = Query(0, ge=0, description="斷線前收到的最後事件序號"),
):
    """斷線重連：按 Last-Event-ID（header 優先，query 兜底）從緩衝續推。"""
    stream = chat_stream_hub.get(stream_id)
    if not stream:
        raise HTTPException(404, "流不存在或已過期")
    header_id = request.headers.get("last-event-id", "")
    after_seq = int(header_id) if header_id.isdigit() else last_event_id
    return _sse_response(stream, after_seq=after_seq)
