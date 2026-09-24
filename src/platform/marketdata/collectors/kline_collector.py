"""K線和技術指標採集器 - 基於騰訊 API（更穩定）"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import threading
import time

from src.platform.marketdata.collectors.market_http import fetch_source
from src.platform.marketdata.models import MARKETS, MarketCode

logger = logging.getLogger(__name__)


def get_market_data():
    """惰性 import,避免包未裝/迴圈 import 影響本模組載入。"""
    from src.platform.marketdata.marketdata_client import get_market_data as _g
    return _g()


# 呼叫來源標記統一在 market_http(全專案共享一個 contextvar)。
# 保留 kline_source 名稱,相容已有呼叫方(schedulers 等)。
kline_source = fetch_source


# ── K線按市場狀態快取 ──────────────────────────────────────────────────────
# 日K一天只定稿一次(收盤後),但排程任務每輪都逐只重新聯網拉 → 批次突發觸發限流。
# 交易時段用短 TTL(末根K線盤中會動),收盤後用長 TTL(資料已定稿,無需重複拉)。
_KLINE_CACHE: dict[str, tuple[float, int, list["KlineData"]]] = {}
_KLINE_TTL_TRADING_S = 180
_KLINE_TTL_CLOSED_S = 1800

# 失敗負快取:源短暫故障(Server disconnected/限流)時,冷卻視窗內不再聯網。
# 復活的批次消費者(entry_candidates/strategy_engine/backtest/組合歸因)會併發地
# 對同一批標的取數,空結果若不快取則每個消費者每輪都重複打爆資料來源。
_FAIL_UNTIL: dict[str, float] = {}
_FAIL_COOLDOWN_S = 60.0  # 交易時段:短冷卻,便於儘快重試
_FAIL_COOLDOWN_CLOSED_S = 900.0  # 收盤後:資料已定稿,失敗/不足時長冷卻,避免批次任務反覆刷屏


def _fail_cooldown(market: MarketCode) -> float:
    """取數失敗/不足時的冷卻時長:交易時段短(儘快重試),收盤後長(重試無意義且易刷屏)。"""
    try:
        md = MARKETS.get(market)
        if md and md.is_trading_time():
            return _FAIL_COOLDOWN_S
    except Exception:
        pass
    return _FAIL_COOLDOWN_CLOSED_S


# 同標的併發合併:同一 cache_key 的併發取數序列化,只聯網一次,其餘複用快取。
_FETCH_LOCKS: dict[str, threading.Lock] = {}
_FETCH_LOCKS_GUARD = threading.Lock()


def _get_fetch_lock(cache_key: str) -> threading.Lock:
    """返回某 cache_key 的取數鎖(程式內複用),用於合併同標的併發請求。"""
    with _FETCH_LOCKS_GUARD:
        lk = _FETCH_LOCKS.get(cache_key)
        if lk is None:
            lk = threading.Lock()
            _FETCH_LOCKS[cache_key] = lk
        return lk


def _kline_cache_ttl(market: MarketCode) -> float:
    try:
        md = MARKETS.get(market)
        if md and md.is_trading_time():
            return _KLINE_TTL_TRADING_S
    except Exception:
        pass
    return _KLINE_TTL_CLOSED_S


def clear_kline_cache() -> None:
    """清空 K線記憶體快取與失敗冷卻標記(測試隔離用)。"""
    _KLINE_CACHE.clear()
    _FAIL_UNTIL.clear()


def get_index_klines(index_code: str, market: MarketCode, days: int = 120) -> list[KlineData]:
    """取大盤/指數日K:走 marketdata 包 index_klines(INDEX_SECID 顯式對映,未對映如美股指數
    → 空列表,fail-soft;見 packages/marketdata/src/marketdata/client.py)。
    """
    try:
        bars = get_market_data().index_klines(index_code, market=market.value, days=days)
    except Exception as e:
        logger.debug(f"指數K線獲取失敗 {index_code}: {e}")
        return []
    return [
        KlineData(date=b.date, open=b.open, close=b.close, high=b.high, low=b.low, volume=b.volume)
        for b in bars
    ]


@dataclass
class KlineData:
    """K線資料"""

    date: str
    open: float
    close: float
    high: float
    low: float
    volume: float


@dataclass
class TechnicalIndicators:
    """技術指標"""

    # 均線
    ma5: float | None = None
    ma10: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    # MACD
    macd_dif: float | None = None
    macd_dea: float | None = None
    macd_hist: float | None = None
    macd_cross: str | None = None  # 金叉/死叉
    macd_cross_days: int | None = None  # 距離上次交叉天數
    # RSI
    rsi6: float | None = None
    rsi12: float | None = None
    rsi24: float | None = None
    # KDJ
    kdj_k: float | None = None
    kdj_d: float | None = None
    kdj_j: float | None = None
    kdj_cross: str | None = None  # 金叉/死叉
    # 布林帶
    boll_upper: float | None = None
    boll_mid: float | None = None
    boll_lower: float | None = None
    boll_width: float | None = None  # 頻寬百分比
    # 量能
    volume_ratio: float | None = None  # 量比（今日成交量/5日均量）
    volume_ma5: float | None = None
    volume_ma10: float | None = None
    volume_trend: str | None = None  # 放量/縮量/平量
    # 漲跌幅
    change_5d: float | None = None
    change_20d: float | None = None
    # 振幅
    amplitude: float | None = None  # 今日振幅
    amplitude_avg5: float | None = None  # 5日平均振幅
    # 波動率(ATR)
    atr: float | None = None  # 平均真實波幅(絕對值)
    atr_pct: float | None = None  # ATR / 最新收盤 * 100(相對波動率%)
    # 支撐壓力（多級別）
    support_s: float | None = None  # 短期支撐（5日）
    support_m: float | None = None  # 中期支撐（20日）
    support_l: float | None = None  # 長期支撐（60日）
    resistance_s: float | None = None  # 短期壓力
    resistance_m: float | None = None  # 中期壓力
    resistance_l: float | None = None  # 長期壓力
    # 相容舊欄位
    support: float | None = None
    resistance: float | None = None
    # K線形態
    kline_pattern: str | None = None  # 十字星/錘子線/吞沒等


def _calculate_ma(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _ema(data: list[float], period: int) -> list[float]:
    """計算 EMA"""
    if not data:
        return []
    result = [data[0]]
    multiplier = 2 / (period + 1)
    for price in data[1:]:
        result.append((price - result[-1]) * multiplier + result[-1])
    return result


def _calculate_atr(klines: list[KlineData], period: int = 14) -> float | None:
    """計算 ATR(平均真實波幅)。

    TR = max(high-low, |high-prevClose|, |low-prevClose|)。
    與本模組其它指標一致,取最近 period 個 TR 的簡單均值(非 Wilder 遞迴平滑),
    便於復現與手算校驗。

    需要至少 period+1 根 K 線(才能算出 period 個含前收的 TR);
    資料不足或異常一律返回 None,不拋異常(fail-soft)。
    """
    try:
        if not klines or len(klines) < period + 1:
            return None
        trs: list[float] = []
        for i in range(1, len(klines)):
            cur = klines[i]
            prev_close = klines[i - 1].close
            tr = max(
                cur.high - cur.low,
                abs(cur.high - prev_close),
                abs(cur.low - prev_close),
            )
            trs.append(tr)
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period
    except Exception:
        return None


def _calculate_macd(
    closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[float], list[float], list[float]] | None:
    """計算 MACD，返回完整序列用於判斷交叉"""
    if len(closes) < slow + signal:
        return None

    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    dif = [f - s for f, s in zip(ema_fast, ema_slow)]
    dea = _ema(dif, signal)
    macd_hist = [(d - e) * 2 for d, e in zip(dif, dea)]
    return dif, dea, macd_hist


def _calculate_rsi(closes: list[float], period: int) -> float | None:
    """計算 RSI"""
    if len(closes) < period + 1:
        return None

    gains = []
    losses = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    # 使用最近 period 天計算
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _calculate_kdj(
    klines: list[KlineData], n: int = 9, m1: int = 3, m2: int = 3
) -> tuple[list[float], list[float], list[float]] | None:
    """計算 KDJ，返回完整序列"""
    if len(klines) < n:
        return None

    k_values = []
    d_values = []
    j_values = []

    for i in range(n - 1, len(klines)):
        period_klines = klines[i - n + 1 : i + 1]
        highest = max(k.high for k in period_klines)
        lowest = min(k.low for k in period_klines)
        close = klines[i].close

        if highest == lowest:
            rsv = 50
        else:
            rsv = (close - lowest) / (highest - lowest) * 100

        if not k_values:
            k = 50
            d = 50
        else:
            k = (2 / 3) * k_values[-1] + (1 / 3) * rsv
            d = (2 / 3) * d_values[-1] + (1 / 3) * k

        j = 3 * k - 2 * d

        k_values.append(k)
        d_values.append(d)
        j_values.append(j)

    return k_values, d_values, j_values


def _calculate_boll(
    closes: list[float], period: int = 20, num_std: int = 2
) -> tuple[float, float, float, float] | None:
    """計算布林帶：上軌、中軌、下軌、頻寬"""
    if len(closes) < period:
        return None

    recent = closes[-period:]
    mid = sum(recent) / period
    variance = sum((x - mid) ** 2 for x in recent) / period
    std = variance**0.5

    upper = mid + num_std * std
    lower = mid - num_std * std
    width = (upper - lower) / mid * 100 if mid > 0 else 0

    return upper, mid, lower, width


def _detect_kline_pattern(klines: list[KlineData]) -> str | None:
    """檢測 K 線形態"""
    if len(klines) < 2:
        return None

    curr = klines[-1]
    prev = klines[-2]

    body = abs(curr.close - curr.open)
    upper_shadow = curr.high - max(curr.close, curr.open)
    lower_shadow = min(curr.close, curr.open) - curr.low
    total_range = curr.high - curr.low

    if total_range == 0:
        return None

    body_ratio = body / total_range

    # 十字星：實體很小
    if body_ratio < 0.1:
        if upper_shadow > body * 2 and lower_shadow > body * 2:
            return "十字星"
        elif upper_shadow > body * 3:
            return "倒T字"
        elif lower_shadow > body * 3:
            return "T字線"

    # 錘子線：下影線很長，實體在上方
    if lower_shadow > body * 2 and upper_shadow < body * 0.5:
        if curr.close > curr.open:
            return "錘子線(陽)"
        else:
            return "錘子線(陰)"

    # 倒錘子：上影線很長
    if upper_shadow > body * 2 and lower_shadow < body * 0.5:
        if curr.close > curr.open:
            return "倒錘子(陽)"
        else:
            return "射擊之星"

    # 吞沒形態
    prev_body = abs(prev.close - prev.open)
    if body > prev_body * 1.5:
        if prev.close < prev.open and curr.close > curr.open:  # 前陰後陽
            if curr.close > prev.open and curr.open < prev.close:
                return "看漲吞沒"
        elif prev.close > prev.open and curr.close < curr.open:  # 前陽後陰
            if curr.open > prev.close and curr.close < prev.open:
                return "看跌吞沒"

    # 大陽線/大陰線
    if body_ratio > 0.7:
        change_pct = (curr.close - curr.open) / curr.open * 100 if curr.open > 0 else 0
        if change_pct > 3:
            return "大陽線"
        elif change_pct < -3:
            return "大陰線"

    return None


def _find_cross_days(
    series1: list[float], series2: list[float], cross_type: str
) -> int | None:
    """找到最近一次交叉距今的天數"""
    if len(series1) < 2 or len(series2) < 2:
        return None

    for i in range(len(series1) - 2, -1, -1):
        if cross_type == "金叉":
            # 金叉：series1 從下方穿越 series2
            if series1[i] <= series2[i] and series1[i + 1] > series2[i + 1]:
                return len(series1) - 1 - i
        else:
            # 死叉：series1 從上方穿越 series2
            if series1[i] >= series2[i] and series1[i + 1] < series2[i + 1]:
                return len(series1) - 1 - i

    return None


class KlineCollector:
    """K線資料採集器（騰訊 API）"""

    def __init__(self, market: MarketCode):
        self.market = market

    def get_klines(self, symbol: str, days: int = 60) -> list[KlineData]:
        """獲取日K線資料。

        正快取(按市場狀態 TTL)+ 同標的併發合併(只聯網一次)+ 失敗負快取
        (源短暫故障時冷卻視窗內不再聯網),避免多消費者併發把資料來源打爆。
        """
        cache_key = f"{self.market.value}:{symbol}"
        need = max(1, int(days or 1))

        # 1) 快路徑:命中新鮮正快取,無需加鎖
        hit = self._cache_hit(cache_key, need)
        if hit is not None:
            return hit

        # 2) 同標的併發合併:僅一個執行緒實際聯網,其餘等待後複用結果
        with _get_fetch_lock(cache_key):
            hit = self._cache_hit(cache_key, need)
            if hit is not None:
                return hit

            now = time.time()
            # 3) 負快取:剛失敗過的標的,冷卻視窗內返回陳舊/空,不再聯網
            if now < _FAIL_UNTIL.get(cache_key, 0.0):
                stale = _KLINE_CACHE.get(cache_key)
                bars = stale[2] if stale else []
                return bars[-need:] if len(bars) > need else bars

            klines = self._fetch_all_sources(symbol, days)
            if klines and len(klines) >= need:
                # 成功且條數足夠:固化正快取並清除冷卻標記
                _KLINE_CACHE[cache_key] = (now, len(klines), list(klines))
                _FAIL_UNTIL.pop(cache_key, None)
            else:
                # 空 或 拿到部分但不足 need(常見:HK 騰訊不足 + eastmoney 補全失敗,
                # 正快取因 count<need 永不命中 → 每輪重打補全源刷屏)→ 固化冷卻。
                # 部分結果仍快取下來,冷卻視窗內直接服務,避免反覆聯網。
                if klines:
                    _KLINE_CACHE[cache_key] = (now, len(klines), list(klines))
                _FAIL_UNTIL[cache_key] = now + _fail_cooldown(self.market)
            return klines[-need:] if len(klines) > need else klines

    def _cache_hit(self, cache_key: str, need: int) -> list[KlineData] | None:
        """命中新鮮正快取(TTL 內且條數足夠)則返回切片,否則 None。"""
        cached = _KLINE_CACHE.get(cache_key)
        if (
            cached
            and (time.time() - cached[0]) < _kline_cache_ttl(self.market)
            and cached[1] >= need
        ):
            bars = cached[2]
            return bars[-need:] if len(bars) > need else bars
        return None

    def _fetch_all_sources(self, symbol: str, days: int) -> list[KlineData]:
        """走 marketdata 包取數(不含快取/合併邏輯):Engine 按 DataSource 優先順序 +
        min_count 取數(條數不足則換源/取最長,tencent → stooq(US) / eastmoney(CN/HK))。
        """
        need = (max(10, min(days, 30)) if self.market == MarketCode.US
                else (max(120, int(days * 0.6)) if self.market in (MarketCode.CN, MarketCode.HK) else 1))
        want = min(max(days, 3000), 20000) if self.market in (MarketCode.CN, MarketCode.HK) else days
        bars = get_market_data().klines(symbol, market=self.market.value, days=want, min_count=need)
        return [KlineData(date=b.date, open=b.open, close=b.close, high=b.high,
                          low=b.low, volume=b.volume) for b in bars]

    def get_technical_indicators(
        self, symbol: str = "", klines: list[KlineData] | None = None
    ) -> TechnicalIndicators:
        """計算技術指標(可傳入已取的 klines 複用,避免重複聯網)。"""
        if klines is None:
            klines = self.get_klines(symbol, days=120)

        if not klines:
            return TechnicalIndicators()

        closes = [k.close for k in klines]
        volumes = [k.volume for k in klines]

        # 均線
        ma5 = _calculate_ma(closes, 5)
        ma10 = _calculate_ma(closes, 10)
        ma20 = _calculate_ma(closes, 20)
        ma60 = _calculate_ma(closes, 60)

        # MACD
        macd_result = _calculate_macd(closes)
        macd_dif, macd_dea, macd_hist = None, None, None
        macd_cross, macd_cross_days = None, None
        if macd_result:
            dif_list, dea_list, hist_list = macd_result
            macd_dif = dif_list[-1]
            macd_dea = dea_list[-1]
            macd_hist = hist_list[-1]
            # 判斷金叉/死叉
            if macd_dif > macd_dea:
                macd_cross = "金叉"
                macd_cross_days = _find_cross_days(dif_list, dea_list, "金叉")
            else:
                macd_cross = "死叉"
                macd_cross_days = _find_cross_days(dif_list, dea_list, "死叉")

        # RSI
        rsi6 = _calculate_rsi(closes, 6)
        rsi12 = _calculate_rsi(closes, 12)
        rsi24 = _calculate_rsi(closes, 24)

        # KDJ
        kdj_k, kdj_d, kdj_j = None, None, None
        kdj_cross = None
        kdj_result = _calculate_kdj(klines)
        if kdj_result:
            k_list, d_list, j_list = kdj_result
            kdj_k = k_list[-1]
            kdj_d = d_list[-1]
            kdj_j = j_list[-1]
            if kdj_k > kdj_d:
                kdj_cross = "金叉"
            else:
                kdj_cross = "死叉"

        # 布林帶
        boll_upper, boll_mid, boll_lower, boll_width = None, None, None, None
        boll_result = _calculate_boll(closes)
        if boll_result:
            boll_upper, boll_mid, boll_lower, boll_width = boll_result

        # 量能分析
        volume_ma5 = _calculate_ma(volumes, 5) if volumes else None
        volume_ma10 = _calculate_ma(volumes, 10) if volumes else None
        volume_ratio = None
        volume_trend = None
        if volumes and volume_ma5 and volume_ma5 > 0:
            volume_ratio = volumes[-1] / volume_ma5
            if volume_ratio > 1.5:
                volume_trend = "放量"
            elif volume_ratio < 0.7:
                volume_trend = "縮量"
            else:
                volume_trend = "平量"

        # 漲跌幅
        change_5d = None
        change_20d = None
        if len(closes) >= 6:
            change_5d = (closes[-1] - closes[-6]) / closes[-6] * 100
        if len(closes) >= 21:
            change_20d = (closes[-1] - closes[-21]) / closes[-21] * 100

        # 振幅
        amplitude = None
        amplitude_avg5 = None
        if klines:
            curr = klines[-1]
            if curr.low > 0:
                amplitude = (curr.high - curr.low) / curr.low * 100
            if len(klines) >= 5:
                amps = []
                for k in klines[-5:]:
                    if k.low > 0:
                        amps.append((k.high - k.low) / k.low * 100)
                if amps:
                    amplitude_avg5 = sum(amps) / len(amps)

        # ATR(波動率):個股自身波動基準,供自適應異動判定使用
        atr = _calculate_atr(klines, period=14)
        atr_pct = None
        if atr is not None and closes and closes[-1]:
            atr_pct = round(atr / closes[-1] * 100, 2)

        # 多級支撐壓力位
        support_s, support_m, support_l = None, None, None
        resistance_s, resistance_m, resistance_l = None, None, None
        if len(klines) >= 5:
            support_s = min(k.low for k in klines[-5:])
            resistance_s = max(k.high for k in klines[-5:])
        if len(klines) >= 20:
            support_m = min(k.low for k in klines[-20:])
            resistance_m = max(k.high for k in klines[-20:])
        if len(klines) >= 60:
            support_l = min(k.low for k in klines[-60:])
            resistance_l = max(k.high for k in klines[-60:])

        # 相容舊欄位
        support = support_m
        resistance = resistance_m

        # K線形態
        kline_pattern = _detect_kline_pattern(klines)

        return TechnicalIndicators(
            ma5=ma5,
            ma10=ma10,
            ma20=ma20,
            ma60=ma60,
            macd_dif=macd_dif,
            macd_dea=macd_dea,
            macd_hist=macd_hist,
            macd_cross=macd_cross,
            macd_cross_days=macd_cross_days,
            rsi6=rsi6,
            rsi12=rsi12,
            rsi24=rsi24,
            kdj_k=kdj_k,
            kdj_d=kdj_d,
            kdj_j=kdj_j,
            kdj_cross=kdj_cross,
            boll_upper=boll_upper,
            boll_mid=boll_mid,
            boll_lower=boll_lower,
            boll_width=boll_width,
            volume_ratio=volume_ratio,
            volume_ma5=volume_ma5,
            volume_ma10=volume_ma10,
            volume_trend=volume_trend,
            change_5d=change_5d,
            change_20d=change_20d,
            amplitude=amplitude,
            amplitude_avg5=amplitude_avg5,
            atr=atr,
            atr_pct=atr_pct,
            support_s=support_s,
            support_m=support_m,
            support_l=support_l,
            resistance_s=resistance_s,
            resistance_m=resistance_m,
            resistance_l=resistance_l,
            support=support,
            resistance=resistance,
            kline_pattern=kline_pattern,
        )

    def get_kline_summary(self, symbol: str) -> dict:
        """獲取 K 線摘要（用於 prompt 和前端展示）"""
        klines = self.get_klines(symbol, days=120)
        if not klines:
            return {"error": "無K線資料"}
        indicators = self.get_technical_indicators(klines=klines)

        # 最近5日表現
        recent_5 = klines[-5:] if len(klines) >= 5 else klines
        up_days = sum(
            1
            for i, k in enumerate(recent_5)
            if i > 0 and k.close > recent_5[i - 1].close
        )

        # 趨勢判斷
        trend = "資料不足"
        if indicators.ma5 and indicators.ma10 and indicators.ma20:
            if indicators.ma5 > indicators.ma10 > indicators.ma20:
                trend = "多頭排列"
            elif indicators.ma5 < indicators.ma10 < indicators.ma20:
                trend = "空頭排列"
            else:
                trend = "均線交織"

        # MACD 狀態（更詳細）
        macd_status = "無資料"
        if indicators.macd_cross:
            days_str = (
                f"({indicators.macd_cross_days}日)"
                if indicators.macd_cross_days
                else ""
            )
            macd_status = f"{indicators.macd_cross}{days_str}"

        # RSI 狀態
        rsi_status = None
        if indicators.rsi6 is not None:
            if indicators.rsi6 > 80:
                rsi_status = "超買"
            elif indicators.rsi6 > 70:
                rsi_status = "偏強"
            elif indicators.rsi6 < 20:
                rsi_status = "超賣"
            elif indicators.rsi6 < 30:
                rsi_status = "偏弱"
            else:
                rsi_status = "中性"

        # KDJ 狀態
        kdj_status = None
        if indicators.kdj_k is not None and indicators.kdj_d is not None:
            if indicators.kdj_j is not None and indicators.kdj_j > 100:
                kdj_status = f"{indicators.kdj_cross}/超買"
            elif indicators.kdj_j is not None and indicators.kdj_j < 0:
                kdj_status = f"{indicators.kdj_cross}/超賣"
            else:
                kdj_status = indicators.kdj_cross

        # 布林帶狀態
        boll_status = None
        last_close = klines[-1].close if klines else None
        if last_close and indicators.boll_upper and indicators.boll_lower:
            if last_close > indicators.boll_upper:
                boll_status = "突破上軌"
            elif last_close < indicators.boll_lower:
                boll_status = "跌破下軌"
            elif indicators.boll_width:
                if indicators.boll_width < 5:
                    boll_status = "收口窄幅"
                elif indicators.boll_width > 15:
                    boll_status = "開口放大"
                else:
                    boll_status = "正常波動"

        last_date = klines[-1].date if klines else None
        now = datetime.now(timezone.utc).isoformat()

        return {
            # meta
            "timeframe": "1d",
            "computed_at": now,
            "asof": last_date,
            "params": {
                "ma": [5, 10, 20, 60],
                "macd": {"fast": 12, "slow": 26, "signal": 9},
                "rsi": {"periods": [6, 12, 24]},
                "kdj": {"n": 9, "m1": 3, "m2": 3},
                "boll": {"period": 20, "num_std": 2},
                "support_resistance": {"windows": [5, 20, 60]},
            },
            "last_close": last_close,
            "recent_5_up": up_days,
            "trend": trend,
            # MACD
            "macd_status": macd_status,
            "macd_cross": indicators.macd_cross,
            "macd_cross_days": indicators.macd_cross_days,
            "macd_hist": indicators.macd_hist,
            # RSI
            "rsi6": indicators.rsi6,
            "rsi_status": rsi_status,
            # KDJ
            "kdj_k": indicators.kdj_k,
            "kdj_d": indicators.kdj_d,
            "kdj_j": indicators.kdj_j,
            "kdj_status": kdj_status,
            # 布林帶
            "boll_upper": indicators.boll_upper,
            "boll_mid": indicators.boll_mid,
            "boll_lower": indicators.boll_lower,
            "boll_width": indicators.boll_width,
            "boll_status": boll_status,
            # 量能
            "volume_ratio": indicators.volume_ratio,
            "volume_trend": indicators.volume_trend,
            # 均線
            "ma5": indicators.ma5,
            "ma10": indicators.ma10,
            "ma20": indicators.ma20,
            "ma60": indicators.ma60,
            # 漲跌幅
            "change_5d": indicators.change_5d,
            "change_20d": indicators.change_20d,
            # 振幅
            "amplitude": indicators.amplitude,
            "amplitude_avg5": indicators.amplitude_avg5,
            # 波動率(ATR)
            "atr": indicators.atr,
            "atr_pct": indicators.atr_pct,
            # 多級支撐壓力
            "support_s": indicators.support_s,
            "support_m": indicators.support_m,
            "support_l": indicators.support_l,
            "resistance_s": indicators.resistance_s,
            "resistance_m": indicators.resistance_m,
            "resistance_l": indicators.resistance_l,
            # 相容舊欄位
            "support": indicators.support,
            "resistance": indicators.resistance,
            # K線形態
            "kline_pattern": indicators.kline_pattern,
        }
