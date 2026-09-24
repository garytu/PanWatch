#!/usr/bin/env python3
"""模擬交易通知系統本地端到端測試指令碼。

使用方法:
    1. 先啟動服務: python server.py
    2. 執行此指令碼: python scripts/test_paper_trading_local.py <username> <password>
"""

import sys
import requests

BASE_URL = "http://localhost:8000"
TIMEOUT = 10
TOKEN = ""


def _api(method: str, path: str, **kwargs):
    url = f"{BASE_URL}{path}"
    headers = kwargs.pop("headers", {})
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    kwargs.setdefault("timeout", TIMEOUT)
    try:
        resp = getattr(requests, method)(url, headers=headers, **kwargs)
    except requests.Timeout:
        print(f"  {method.upper()} {path} -> TIMEOUT")
        return 0, {"error": "請求超時"}
    except requests.ConnectionError:
        print(f"  {method.upper()} {path} -> CONNECTION ERROR")
        return 0, {"error": "連線失敗"}
    print(f"  {method.upper()} {path} -> {resp.status_code}")
    try:
        data = resp.json()
        # 解包統一回應格式 {"code":0, "data": {...}}
        if isinstance(data, dict) and "data" in data and "code" in data:
            data = data["data"]
    except Exception:
        data = resp.text
    return resp.status_code, data


def step(name: str):
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")


def main():
    global TOKEN

    if len(sys.argv) < 3:
        print(f"用法: python {sys.argv[0]} <username> <password>")
        sys.exit(1)

    username, password = sys.argv[1], sys.argv[2]

    # 0. 連線 & 登入
    step("0. 連線 & 登入")
    try:
        resp = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"username": username, "password": password},
            timeout=TIMEOUT,
        )
        print(f"  POST /api/auth/login -> {resp.status_code}")
        if resp.status_code == 200:
            body = resp.json()
            # 相容統一回應包裝 {"data": {"token": ...}} 和裸回應 {"token": ...}
            TOKEN = (body.get("data") or body).get("token", "")
            print(f"  登入成功, token 長度: {len(TOKEN)}")
        else:
            print(f"  登入失敗: {resp.text}")
            sys.exit(1)
    except requests.ConnectionError:
        print("  無法連線服務，請先執行 python server.py")
        sys.exit(1)
    except requests.Timeout:
        print("  連線超時")
        sys.exit(1)

    # 驗證 token
    code, data = _api("get", "/api/paper-trading/account")
    if code == 401:
        print(f"  鑑權失敗: {data}")
        sys.exit(1)
    cap = data.get("current_capital", "?") if isinstance(data, dict) else "?"
    print(f"  鑑權透過, 帳戶資金: {cap}")

    # 1. 重置模擬交易
    step("1. 重置模擬交易帳戶")
    code, data = _api("post", "/api/paper-trading/account/reset")
    print(f"  結果: {data}")

    # 2. 測試通知管道連通性
    step("2. 測試通知管道")
    code, data = _api("post", "/api/paper-trading/notify-test")
    print(f"  結果: {data}")
    if code != 200:
        print("  通知管道不通，後續通知可能無法傳送（請在 Web UI 中配置）")

    # 3. 觸發盤前計劃
    step("3. 觸發盤前計劃通知")
    code, data = _api("post", "/api/paper-trading/premarket-plan")
    print(f"  結果: {data}")
    print("  -> 檢查通知管道是否收到盤前計劃（驗證去重效果）")

    # 4. 觸發掃描（自動建倉）
    step("4. 觸發掃描")
    code, data = _api("post", "/api/paper-trading/scan", timeout=120)
    print(f"  結果: {data}")
    if isinstance(data, dict):
        opened = data.get("opened", 0)
        closed = data.get("closed", 0)
        print(f"  建倉: {opened}, 平倉: {closed}")
        if opened > 0:
            print("  -> 檢查通知管道是否收到建倉通知")

    # 5. 檢視當前持倉
    step("5. 檢視當前持倉")
    code, data = _api("get", "/api/paper-trading/positions")
    positions = data if isinstance(data, list) else data.get("positions", [])
    print(f"  持倉數量: {len(positions)}")
    for p in positions[:5]:
        if isinstance(p, dict):
            print(f"    {p.get('stock_name', '')} ({p.get('stock_symbol', '')}) "
                  f"入場價: {p.get('entry_price', 0):.2f}")

    # 6. 手動平倉第一個持倉
    if positions:
        step("6. 手動平倉")
        first = positions[0]
        pos_id = first.get("id") if isinstance(first, dict) else None
        if pos_id:
            code, data = _api("post", f"/api/paper-trading/positions/{pos_id}/close")
            print(f"  結果: {data}")
            print("  -> 檢查通知管道是否收到平倉通知")
    else:
        step("6. 手動平倉（跳過，無持倉）")

    # 7. 檢視交易歷史
    step("7. 檢視交易歷史")
    code, data = _api("get", "/api/paper-trading/trades")
    trades = data.get("items", []) if isinstance(data, dict) else data
    print(f"  交易記錄數: {len(trades)}")
    for t in trades[:5]:
        if isinstance(t, dict):
            pnl = t.get("pnl", 0)
            sign = "+" if pnl >= 0 else ""
            print(f"    {t.get('stock_name', '')} 損益: {sign}{pnl:.2f} ({t.get('exit_reason', '')})")

    # 8. 觸發日終摘要
    step("8. 觸發日終摘要通知")
    code, data = _api("post", "/api/paper-trading/daily-summary")
    print(f"  結果: {data}")
    print("  -> 檢查通知管道是否收到日終摘要")

    step("測試完成")
    print("  請檢視通知管道確認所有通知是否正確送達。\n")


if __name__ == "__main__":
    main()
