"""命令列系統自檢:`python -m src.modules.administration.doctor` 或 `make doctor`。

終端跑一遍 系統基礎項(DB/磁碟/排程)+ 資料來源/AI/通知,列印結果與中文修復建議。
CLI 程式內無執行中的排程器 → 排程項會優雅跳過(顯示說明,不誤報)。
退出碼:有異常項返回 1,全通返回 0(便於 CI/指令碼判斷)。
"""

from __future__ import annotations

import asyncio
import sys

from src.modules.administration.selfcheck import run_selfcheck

_ICON = {"ok": "✅", "slow": "⚠️", "fail": "❌"}
_CAT = {"system": "系統", "datasource": "資料來源", "ai": "AI模型", "notify": "通知管道"}
_ORDER = ["system", "datasource", "ai", "notify"]


def _print_report(res: dict) -> None:
    s = res["summary"]
    print("\n===== PanWatch 系統自檢 =====")
    print(f"共 {s['total']} · ✅通 {s['ok']} · ⚠️慢 {s['slow']} · ❌斷 {s['fail']}\n")
    items = res.get("items", [])
    for cat in _ORDER:
        cat_items = [i for i in items if i["category"] == cat]
        if not cat_items:
            continue
        print(f"【{_CAT.get(cat, cat)}】")
        for i in cat_items:
            icon = _ICON.get(i["status"], "?")
            grp = f"{i['group']} / " if i.get("group") else ""
            lat = f"  {i['latency_ms']}ms" if i.get("latency_ms") else ""
            print(f"  {icon} {grp}{i['name']}{lat}")
            if i["status"] == "fail":
                if i.get("error"):
                    print(f"       錯誤: {i['error']}")
                if i.get("hint"):
                    print(f"       建議: {i['hint']}")
            elif i.get("note"):
                print(f"       {i['note']}")
        print()
    if s["fail"]:
        print(f"⚠️  發現 {s['fail']} 項異常,見上方建議。")
    else:
        print("✅ 全部正常。")


def main() -> int:
    res = asyncio.run(run_selfcheck())
    _print_report(res)
    return 1 if res["summary"]["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
