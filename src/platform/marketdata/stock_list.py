"""股票標的清單的資料來源介面卡、專案級快取與模糊搜尋。

清單可被 API、任務排程和業務模組共同使用，因此屬於市場資料平臺，而不是
HTTP 層。快取仍固定儲存在專案根目錄的 ``data/``，避免移動程式碼後悄然生成
另一份 ``src/data`` 快取。
"""
import json
import os
import time
import logging
import concurrent.futures
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
CACHE_FILE = DATA_DIR / "stock_list_cache.json"
CACHE_TTL = 86400 * 7  # 7 days

# 東方財富 A 股（使用 push2delay 域名，避免重定向）
EASTMONEY_URL = "http://80.push2delay.eastmoney.com/api/qt/clist/get"
EASTMONEY_PARAMS = {
    "po": "1",
    "np": "1",
    "fltt": "2",
    "invt": "2",
    "fid": "f12",
    "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
    "fields": "f12,f14",
}

# 東方財富港股引數
EASTMONEY_HK_PARAMS = {
    "po": "1",
    "np": "1",
    "fltt": "2",
    "invt": "2",
    "fid": "f12",
    "fs": "m:128+t:3,m:128+t:4,m:128+t:1,m:128+t:2",  # 港股主機板、創業板等
    "fields": "f12,f14",
}

# 東方財富美股引數
EASTMONEY_US_PARAMS = {
    "po": "1",
    "np": "1",
    "fltt": "2",
    "invt": "2",
    "fid": "f12",
    "fs": "m:105,m:106,m:107",  # 美股 NYSE, NASDAQ, AMEX
    "fields": "f12,f14",
}

# 東方財富北交所引數（北證A股）
EASTMONEY_BJ_PARAMS = {
    "po": "1",
    "np": "1",
    "fltt": "2",
    "invt": "2",
    "fid": "f12",
    "fs": "m:0+t:81",  # 北交所
    "fields": "f12,f14",
}
PAGE_SIZE = 100


def _load_cache() -> list[dict] | None:
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if time.time() - data.get("ts", 0) < CACHE_TTL:
            return data["stocks"]
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def _save_cache(stocks: list[dict]):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"ts": time.time(), "stocks": stocks}, f, ensure_ascii=False)


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://quote.eastmoney.com/",
}


def _fetch_page(client: httpx.Client, page: int) -> list[dict]:
    """獲取東方財富股票列表的單頁"""
    params = {**EASTMONEY_PARAMS, "pn": str(page), "pz": str(PAGE_SIZE)}
    resp = client.get(EASTMONEY_URL, params=params, timeout=30, follow_redirects=True)
    data = resp.json()
    diff = data.get("data") or {}
    items = diff.get("diff") or []
    return [{"symbol": str(item["f12"]), "name": str(item["f14"]), "market": "CN"} for item in items]


def _fetch_from_eastmoney() -> list[dict]:
    """東方財富 A 股列表（HTTP 分頁併發獲取）"""
    with httpx.Client(follow_redirects=True, headers=HEADERS, timeout=30) as client:
        # 第一頁: 獲取總數
        params = {**EASTMONEY_PARAMS, "pn": "1", "pz": str(PAGE_SIZE)}
        resp = client.get(EASTMONEY_URL, params=params)
        data = resp.json()
        root = data.get("data") or {}
        total = root.get("total", 0)
        first_items = root.get("diff") or []

        stocks = [{"symbol": str(item["f12"]), "name": str(item["f14"]), "market": "CN"} for item in first_items]

        if total <= PAGE_SIZE:
            return stocks

        # 剩餘頁併發獲取
        pages_needed = (total + PAGE_SIZE - 1) // PAGE_SIZE
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_page, client, pn): pn for pn in range(2, pages_needed + 1)}
            for future in concurrent.futures.as_completed(futures):
                try:
                    stocks.extend(future.result())
                except Exception as e:
                    logger.warning(f"東方財富第 {futures[future]} 頁獲取失敗: {e}")

    return stocks


def _fetch_hk_page(client: httpx.Client, page: int) -> list[dict]:
    """獲取東方財富港股列表的單頁"""
    params = {**EASTMONEY_HK_PARAMS, "pn": str(page), "pz": str(PAGE_SIZE)}
    resp = client.get(EASTMONEY_URL, params=params, timeout=30, follow_redirects=True)
    data = resp.json()
    diff = data.get("data") or {}
    items = diff.get("diff") or []
    return [{"symbol": str(item["f12"]), "name": str(item["f14"]), "market": "HK"} for item in items]


def _fetch_hk_from_eastmoney() -> list[dict]:
    """東方財富港股列表"""
    with httpx.Client(follow_redirects=True, headers=HEADERS, timeout=30) as client:
        params = {**EASTMONEY_HK_PARAMS, "pn": "1", "pz": str(PAGE_SIZE)}
        resp = client.get(EASTMONEY_URL, params=params)
        data = resp.json()
        root = data.get("data") or {}
        total = root.get("total", 0)
        first_items = root.get("diff") or []

        stocks = [{"symbol": str(item["f12"]), "name": str(item["f14"]), "market": "HK"} for item in first_items]

        if total <= PAGE_SIZE:
            return stocks

        pages_needed = (total + PAGE_SIZE - 1) // PAGE_SIZE
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_hk_page, client, pn): pn for pn in range(2, pages_needed + 1)}
            for future in concurrent.futures.as_completed(futures):
                try:
                    stocks.extend(future.result())
                except Exception as e:
                    logger.warning(f"東方財富港股第 {futures[future]} 頁獲取失敗: {e}")

    return stocks


def _fetch_bj_page(client: httpx.Client, page: int) -> list[dict]:
    """獲取東方財富北交所列表的單頁"""
    params = {**EASTMONEY_BJ_PARAMS, "pn": str(page), "pz": str(PAGE_SIZE)}
    resp = client.get(EASTMONEY_URL, params=params, timeout=30, follow_redirects=True)
    data = resp.json()
    diff = data.get("data") or {}
    items = diff.get("diff") or []
    return [{"symbol": str(item["f12"]), "name": str(item["f14"]), "market": "CN"} for item in items]


def _fetch_bj_from_eastmoney() -> list[dict]:
    """東方財富北交所列表（HTTP 分頁併發獲取）"""
    with httpx.Client(follow_redirects=True, headers=HEADERS, timeout=30) as client:
        # 第一頁: 獲取總數
        params = {**EASTMONEY_BJ_PARAMS, "pn": "1", "pz": str(PAGE_SIZE)}
        resp = client.get(EASTMONEY_URL, params=params)
        data = resp.json()
        root = data.get("data") or {}
        total = root.get("total", 0)
        first_items = root.get("diff") or []

        stocks = [{"symbol": str(item["f12"]), "name": str(item["f14"]), "market": "CN"} for item in first_items]

        if total <= PAGE_SIZE:
            return stocks

        # 剩餘頁併發獲取
        pages_needed = (total + PAGE_SIZE - 1) // PAGE_SIZE
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_bj_page, client, pn): pn for pn in range(2, pages_needed + 1)}
            for future in concurrent.futures.as_completed(futures):
                try:
                    stocks.extend(future.result())
                except Exception as e:
                    logger.warning(f"東方財富北交所第 {futures[future]} 頁獲取失敗: {e}")

    return stocks


def _fetch_us_page(client: httpx.Client, page: int) -> list[dict]:
    """獲取東方財富美股列表的單頁"""
    params = {**EASTMONEY_US_PARAMS, "pn": str(page), "pz": str(PAGE_SIZE)}
    resp = client.get(EASTMONEY_URL, params=params, timeout=30, follow_redirects=True)
    data = resp.json()
    diff = data.get("data") or {}
    items = diff.get("diff") or []
    return [{"symbol": str(item["f12"]), "name": str(item["f14"]), "market": "US"} for item in items]


def _fetch_us_from_eastmoney() -> list[dict]:
    """東方財富美股列表"""
    with httpx.Client(follow_redirects=True, headers=HEADERS, timeout=30) as client:
        params = {**EASTMONEY_US_PARAMS, "pn": "1", "pz": str(PAGE_SIZE)}
        resp = client.get(EASTMONEY_URL, params=params)
        data = resp.json()
        root = data.get("data") or {}
        total = root.get("total", 0)
        first_items = root.get("diff") or []

        stocks = [{"symbol": str(item["f12"]), "name": str(item["f14"]), "market": "US"} for item in first_items]

        if total <= PAGE_SIZE:
            return stocks

        pages_needed = (total + PAGE_SIZE - 1) // PAGE_SIZE
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_us_page, client, pn): pn for pn in range(2, pages_needed + 1)}
            for future in concurrent.futures.as_completed(futures):
                try:
                    stocks.extend(future.result())
                except Exception as e:
                    logger.warning(f"東方財富美股第 {futures[future]} 頁獲取失敗: {e}")

    return stocks


def _fetch_from_akshare() -> list[dict]:
    """akshare 資料來源（備用，可能有 SSL 問題）"""
    import akshare as ak

    df = ak.stock_info_a_code_name()
    stocks = []
    for _, row in df.iterrows():
        stocks.append({
            "symbol": str(row["code"]),
            "name": str(row["name"]),
            "market": "CN",
        })
    return stocks


def refresh_stock_list() -> list[dict]:
    """拉取 A 股和港股列表並快取"""
    stocks = []

    # A 股: 東方財富優先，akshare 備用
    try:
        cn_stocks = _fetch_from_eastmoney()
        stocks.extend(cn_stocks)
        logger.info(f"東方財富獲取 A 股列表成功: {len(cn_stocks)} 只")
    except Exception as e:
        logger.warning(f"東方財富獲取 A 股失敗: {e}")
        try:
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(_fetch_from_akshare)
                cn_stocks = future.result(timeout=15)
                stocks.extend(cn_stocks)
            logger.info(f"akshare 獲取 A 股列表成功: {len(cn_stocks)} 只")
        except concurrent.futures.TimeoutError:
            logger.error("akshare 獲取超時（15s）")
        except Exception as e2:
            logger.error(f"A 股資料來源獲取失敗: {e2}")

    # 港股: 東方財富
    try:
        hk_stocks = _fetch_hk_from_eastmoney()
        stocks.extend(hk_stocks)
        logger.info(f"東方財富獲取港股列表成功: {len(hk_stocks)} 只")
    except Exception as e:
        logger.warning(f"東方財富獲取港股失敗: {e}")

    # 美股: 東方財富
    try:
        us_stocks = _fetch_us_from_eastmoney()
        stocks.extend(us_stocks)
        logger.info(f"東方財富獲取美股列表成功: {len(us_stocks)} 只")
    except Exception as e:
        logger.warning(f"東方財富獲取美股失敗: {e}")

    # 北交所: 東方財富
    try:
        bj_stocks = _fetch_bj_from_eastmoney()
        stocks.extend(bj_stocks)
        logger.info(f"東方財富獲取北交所列表成功: {len(bj_stocks)} 只")
    except Exception as e:
        logger.warning(f"東方財富獲取北交所失敗: {e}")

    if stocks:
        _save_cache(stocks)
    return stocks


def get_stock_list() -> list[dict]:
    """獲取股票列表(優先快取)"""
    cached = _load_cache()
    if cached:
        return cached
    return refresh_stock_list()


def _realtime_search(query: str, market: str = "", limit: int = 20) -> list[dict]:
    """東方財富即時搜尋 API"""
    import urllib.parse
    # 提高 count 以覆蓋更多候選項（包含北交所）
    url = f"https://searchapi.eastmoney.com/api/suggest/get?input={urllib.parse.quote(query)}&type=14&count={limit * 5}"

    try:
        with httpx.Client(timeout=5) as client:
            resp = client.get(url, headers=HEADERS)
            data = resp.json()
    except Exception as e:
        logger.warning(f"即時搜尋失敗: {e}")
        return []

    items = data.get("QuotationCodeTable", {}).get("Data", [])
    if not items:
        return []

    def _normalize_symbol(code: str, mkt: str) -> str:
        c = (code or "").strip().upper()
        # 去掉可能的市場字首/字尾，如 SH000001 / SZ000001 / BJ830799 / 00700.HK / 836239.BJ
        for p in ("SH", "SZ", "BJ", "US", "HK"):
            if c.startswith(p):
                c = c[len(p):]
                break
        if "." in c:
            # 形如 00700.HK / 836239.BJ
            c = c.split(".")[0]
        if mkt == "HK":
            # 保證為 5 位程式碼
            c = c.zfill(5)
        return c

    results = []
    for item in items:
        classify = (item.get("Classify") or "").strip()
        security_type = (item.get("SecurityTypeName") or "").strip()
        code_raw = (item.get("Code") or "").strip().upper()

        # 判斷市場
        if (
            classify in ("AStock", "BJStock")
            or any(ch in security_type for ch in ("滬", "深", "北"))
            or code_raw.endswith(".BJ")
            or code_raw.startswith("BJ")
        ):
            stock_market = "CN"
        elif classify == "HKStock" or "港" in security_type:
            stock_market = "HK"
        elif classify == "UsStock" or "美" in security_type:
            stock_market = "US"
        else:
            continue  # 跳過其他型別（債券、基金等）

        # 市場篩選
        if market and stock_market != market:
            continue

        # 只保留股票（排除債券等）
        type_us = item.get("TypeUS", "")
        if stock_market == "US" and type_us and type_us not in ("1", "2", "3"):  # 1=普通股, 3=ADR/ADS 等；5=ETF 等
            continue

        code = item.get("Code", "")
        symbol = _normalize_symbol(code, stock_market)

        results.append({
            "symbol": symbol,
            "name": item.get("Name", ""),
            "market": stock_market,
        })

        if len(results) >= limit:
            break

    return results


def search_stocks(query: str, market: str = "", limit: int = 20) -> list[dict]:
    """搜尋股票 - 優先使用即時搜尋，失敗則使用快取"""
    q = query.strip()
    if not q:
        return []

    # 嘗試即時搜尋
    results = _realtime_search(q, market, limit)
    if len(results) >= limit:
        return results[:limit]

    # 即時搜尋結果不足時，用快取補全（便於聚合多市場搜尋結果）
    cached = _cached_search(q, market, limit)
    if not results:
        if cached:
            logger.info("即時搜尋無結果，使用快取搜尋")
        return cached

    seen = {(r.get("market"), r.get("symbol")) for r in results}
    for r in cached:
        key = (r.get("market"), r.get("symbol"))
        if key in seen:
            continue
        results.append(r)
        seen.add(key)
        if len(results) >= limit:
            break
    return results


def _cached_search(query: str, market: str = "", limit: int = 20) -> list[dict]:
    """從快取中模糊搜尋股票"""
    stocks = get_stock_list()
    if not stocks:
        return []

    q = query.strip().upper()
    if not q:
        return []

    results = []
    for s in stocks:
        if market and s["market"] != market:
            continue
        code = s["symbol"].upper()
        name = s["name"].upper()
        # 程式碼字首匹配優先
        if code.startswith(q):
            results.append((0, s))
        elif q in name:
            results.append((1, s))
        elif q in code:
            results.append((2, s))

        if len(results) >= limit * 2:
            break

    results.sort(key=lambda x: x[0])
    return [r[1] for r in results[:limit]]
