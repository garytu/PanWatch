"""Planning 試點 —— "全面診斷我的持倉"計劃驅動編排。

範圍刻意小:只覆蓋單一場景(全面診斷持倉)。識別到該意圖後走計劃驅動:
LLM 生成結構化計劃(逐持倉股分析 → 組合風險 → 彙總建議)→ 計劃經 SSE `plan` 事件
推給前端 → 逐步執行(每步複用現有工具/LLM)→ 步驟失敗重規劃(上限 1 次,超限帶失敗
資訊直接彙總)。

這是**試點**:驗證"計劃驅動"相對固定流程的價值,不做過度泛化。編排函式把工具執行器
(execute_tool)與 SSE 流(stream)作為依賴注入,便於單測全 mock。
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

# 觸發詞:顯式命中即走計劃驅動(簡單啟發式,試點足夠)
_PLANNING_TRIGGERS = (
    "全面診斷",
    "診斷我的持倉",
    "診斷一下我的持倉",
    "持倉診斷",
    "組合診斷",
    "全面體檢",
    "持倉體檢",
    "全面分析我的持倉",
)


def should_use_planning(content: str) -> bool:
    """判斷使用者輸入是否命中"全面診斷持倉"場景。"""
    if not content:
        return False
    text = content.replace(" ", "")
    return any(t in text for t in _PLANNING_TRIGGERS)


_PLAN_SYSTEM = (
    "你是投資組合診斷規劃助手。根據使用者持倉,產出一個結構化診斷計劃。"
    "只輸出 JSON,形如:"
    '{"steps":[{"title":"分析 貴州茅臺(600519)","action":"analyze_stock",'
    '"params":{"symbol":"600519","market":"CN"}},'
    '{"title":"組合整體風險","action":"portfolio_risk"}]}。'
    "action 取值:analyze_stock(逐只持倉,params 帶 symbol/market)、portfolio_risk(組合風險)。"
    "不要包含彙總步驟,彙總由系統自動追加。"
)


def _plan_messages(portfolio_text: str) -> list[dict]:
    return [
        {"role": "system", "content": _PLAN_SYSTEM},
        {"role": "user", "content": f"我的持倉如下,請產出診斷計劃:\n{portfolio_text}"},
    ]


def _replan_messages(
    portfolio_text: str, failed_title: str, error: str
) -> list[dict]:
    return [
        {"role": "system", "content": _PLAN_SYSTEM},
        {
            "role": "user",
            "content": (
                f"我的持倉:\n{portfolio_text}\n\n"
                f'上一版計劃裡的步驟「{failed_title}」執行失敗({error}),'
                "請重新產出一份可執行的診斷計劃(跳過或替換失敗步驟)。"
            ),
        },
    ]


def parse_plan(text: str) -> list[dict] | None:
    """從 LLM 文本里容錯解析計劃步驟列表。

    支援:純 JSON、```json 圍欄包裹、前後有解釋文字、尾部截斷等常見髒輸出。
    解析失敗返回 None(交由呼叫方回退預設計劃)。
    """
    if not text:
        return None

    blob = None
    m = re.search(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", text, re.S)
    if m:
        blob = m.group(1)
    else:
        candidates = [i for i in (text.find("{"), text.find("[")) if i >= 0]
        if candidates:
            blob = text[min(candidates):]

    if not blob:
        return None

    data = None
    for attempt in (blob, blob[: max(blob.rfind("]"), blob.rfind("}")) + 1]):
        try:
            data = json.loads(attempt)
            break
        except Exception:
            continue
    if data is None:
        return None

    if isinstance(data, dict):
        data = data.get("steps") or data.get("plan")
    if not isinstance(data, list) or not data:
        return None
    return data


def build_default_plan(portfolio_text: str) -> list[dict]:
    """LLM 計劃不可用時的降級預設計劃(僅做組合風險,彙總由系統追加)。"""
    return [{"title": "組合整體風險評估", "action": "portfolio_risk"}]


def normalize_steps(steps: list[dict], start_id: int = 1) -> list[dict]:
    """規範化步驟:補 id/title/action/params/status。過濾 summarize(彙總系統自動做)。"""
    out = []
    sid = start_id
    for s in steps:
        if not isinstance(s, dict):
            continue
        action = s.get("action") or "portfolio_risk"
        if action == "summarize":
            continue
        out.append(
            {
                "id": sid,
                "title": s.get("title") or f"步驟 {sid}",
                "action": action,
                "params": s.get("params") or {},
                "status": "pending",
            }
        )
        sid += 1
    return out


def _steps_public(steps: list[dict]) -> list[dict]:
    return [{"id": s["id"], "title": s["title"], "status": s["status"]} for s in steps]


async def _publish_plan(stream, steps: list[dict], status: str, current=None) -> None:
    data = {"status": status, "steps": _steps_public(steps)}
    if current is not None:
        data["current"] = current
    await stream.publish("plan", data)


_STEP_SYSTEM = "你是資深投研分析師。基於給定資料,給出精煉、有據的分析(150 字內)。"
_SUMMARY_SYSTEM = (
    "你是資深投資顧問。基於各步驟的分析結果,給出全面的持倉診斷結論:"
    "整體健康度、主要風險、可執行的調倉建議。分點、精煉、有據。"
)


async def _execute_step(db, ai_client, execute_tool, step: dict, portfolio_text: str) -> str:
    """執行單個計劃步驟,返回該步的分析文本。"""
    action = step["action"]
    if action == "analyze_stock":
        p = step.get("params") or {}
        symbol = p.get("symbol", "")
        market = p.get("market", "CN")
        tech = await execute_tool(db, "get_technical_analysis", {"symbol": symbol, "market": market})
        sug = await execute_tool(db, "get_stock_suggestions", {"symbol": symbol, "market": market})
        msgs = [
            {"role": "system", "content": _STEP_SYSTEM},
            {
                "role": "user",
                "content": f"分析持倉「{step['title']}」。\n技術面:\n{tech}\n\nAI 建議:\n{sug}",
            },
        ]
        return await ai_client.chat_multi(msgs, temperature=0.4)

    # portfolio_risk 及其它未知 action:統一按組合風險處理
    msgs = [
        {"role": "system", "content": _STEP_SYSTEM},
        {"role": "user", "content": f"評估以下持倉組合的整體風險:\n{portfolio_text}"},
    ]
    return await ai_client.chat_multi(msgs, temperature=0.4)


def _summary_messages(results: list[tuple[str, str]]) -> list[dict]:
    body = "\n\n".join(f"【{title}】\n{res}" for title, res in results)
    return [
        {"role": "system", "content": _SUMMARY_SYSTEM},
        {"role": "user", "content": f"以下是各步驟的診斷結果,請彙總:\n\n{body}"},
    ]


async def run_portfolio_diagnosis(db, stream, ai_client, execute_tool) -> str:
    """計劃驅動的"全面診斷持倉"編排,返回最終彙總文本(已透過 SSE 流式推送)。

    Args:
        db: DB session。
        stream: SSEStream(需支援 async publish(event, data))。
        ai_client: AI 使用者端(chat_multi / chat_stream)。
        execute_tool: async (db, name, args) -> str 工具執行器。
    """
    await stream.publish("plan", {"status": "planning", "steps": []})

    portfolio_text = await execute_tool(db, "get_portfolio", {})

    # 1) 生成計劃(失敗/解析不了則回退預設計劃)
    steps = None
    try:
        raw = await ai_client.chat_multi(_plan_messages(portfolio_text), temperature=0.3)
        steps = parse_plan(raw)
    except Exception:
        logger.warning("生成診斷計劃失敗,回退預設計劃", exc_info=True)
    if not steps:
        steps = build_default_plan(portfolio_text)
    steps = normalize_steps(steps)
    if not steps:
        steps = normalize_steps(build_default_plan(portfolio_text))

    await _publish_plan(stream, steps, status="running")

    # 2) 逐步執行,失敗重規劃(上限 1 次)
    results: list[tuple[str, str]] = []
    replanned = False
    i = 0
    while i < len(steps):
        step = steps[i]
        step["status"] = "running"
        await _publish_plan(stream, steps, status="running", current=step["id"])
        try:
            res = await _execute_step(db, ai_client, execute_tool, step, portfolio_text)
            step["status"] = "done"
            results.append((step["title"], res))
        except Exception as e:  # noqa: BLE001
            if not replanned:
                replanned = True
                logger.info("步驟「%s」失敗,觸發重規劃: %s", step["title"], e)
                try:
                    raw = await ai_client.chat_multi(
                        _replan_messages(portfolio_text, step["title"], str(e)),
                        temperature=0.3,
                    )
                    new_steps = parse_plan(raw)
                except Exception:
                    new_steps = None
                if new_steps:
                    steps = steps[:i] + normalize_steps(new_steps, start_id=step["id"])
                    await _publish_plan(stream, steps, status="running")
                    continue  # 從當前位置用新計劃重試
            # 已重規劃過或重規劃失敗:標記失敗,帶失敗資訊繼續彙總
            step["status"] = "failed"
            results.append((step["title"], f"(該步執行失敗:{e})"))
        await _publish_plan(stream, steps, status="running")
        i += 1

    # 3) 彙總(流式推 token)
    summary = ""
    try:
        parts: list[str] = []
        async for kind, payload in ai_client.chat_stream(
            _summary_messages(results), temperature=0.4
        ):
            if kind == "token":
                parts.append(payload)
                await stream.publish("token", {"text": payload})
        summary = "".join(parts)
    except Exception as e:  # noqa: BLE001 — 流式彙總失敗降級為非流式
        logger.warning("流式彙總失敗,降級非流式: %s", e)
        try:
            summary = await ai_client.chat_multi(_summary_messages(results), temperature=0.4)
            await stream.publish("token", {"text": summary})
        except Exception:
            summary = "抱歉,診斷彙總失敗。"
            await stream.publish("token", {"text": summary})

    await _publish_plan(stream, steps, status="done")
    return summary
