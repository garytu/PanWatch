# marketdata

多市場(A股 / 港股 / 美股)行情資料抓取層,**可插拔資料來源 + 主備故障轉移**。脫胎於 PanWatch,但**零 `src/` / web / DB 依賴**——透過兩個注入埠(`ConfigProvider` / `MetricsSink`)解耦宿主,可獨立使用或嵌入任意專案。

宿主(PanWatch)接入是**單一路徑**:資料抓取全部經由本包,`src/core/providers`/`akshare_collector` 等舊實現已刪除,沒有灰度 flag、沒有回退分支。

## 設計

一條路徑,兩層:

```
呼叫方 → MarketData(物件式 API)
            └─ Engine(每型別一個:按 priority 主備鏈故障轉移 + TTL 快取 + 指標)
                 └─ Vendor(每家源一個:只做"抓取 + 解析成標準型別",內部無 fallback)
                      └─ market_get(直連 trust_env=False + 按 host 節流 + 退避重試)
```

- **同步核心**(與底層 HTTP 一致、最好測);非同步呼叫方自行 `asyncio.to_thread(...)` 包一層。
- **兩個埠(Protocol)**:`ConfigProvider` 供"某型別在某市場按優先順序有哪些啟用源 + 憑證",`MetricsSink` 收每次取數的成敗/延遲。包只依賴這倆介面,不碰宿主的 DB/web。
- **返回帶型別 dataclass**(`Quote` / `Bar` / `CapitalFlow` / `EventItem` / `FlashNews` / `Fundamentals` / `DragonTigerItem` / `MarginItem` / `ShareholderItem` / `DividendItem` / `NorthboundItem` / `HotStock` / `HotBoard`),不是裸 dict。
- **`Symbol` 值物件**:一處歸一化各市場程式碼(騰訊字首 / 東財 secid / yfinance 字尾),消滅散落各處的 `_to_market`。

## 安裝

```bash
pip install -e ./packages/marketdata          # 本地 editable(monorepo 內)
# 依賴僅 httpx;yfinance 為可選 extra:
pip install -e "./packages/marketdata[yfinance]"
```

## 快速上手(獨立使用)

用內建的靜態配置埠即可跑,無需任何宿主:

```python
from marketdata import MarketData, StaticConfigProvider, SourceConfig

md = MarketData(config=StaticConfigProvider({
    "quote": [
        SourceConfig(vendor="tencent", priority=0),   # 主源
        SourceConfig(vendor="sina",    priority=5),   # US/HK 備源(免 key 免代理)
    ],
    "kline": [
        SourceConfig(vendor="tencent",   priority=0),
        SourceConfig(vendor="eastmoney", priority=5),  # CN/HK 長曆史兜底
        SourceConfig(vendor="stooq",     priority=15), # US 兜底
    ],
    "capital_flow": [SourceConfig(vendor="eastmoney", priority=0)],
    "events":       [SourceConfig(vendor="eastmoney", priority=0)],
}))

md.quotes(["600519", "00700", "AAPL"])        # 跨市場,自動按市場分組 → list[Quote]
md.klines("600519", market="CN", days=120)    # list[Bar]
md.capital_flow("600519")                      # CapitalFlow | None
md.events(["600519"], since_days=7)            # list[EventItem]
md.hot_stocks(market="CN", mode="turnover")    # list[HotStock](發現,不經 Engine)
md.health()                                     # {vendor: {success_rate, p50_latency_ms, last_error, ...}}
```

## API 速查

### `MarketData(config: ConfigProvider, metrics: MetricsSink | None = None)`

| 方法 | 引數 | 返回 | 說明 |
|---|---|---|---|
| `quotes` | `symbols, *, market=None` | `list[Quote]` | 批次報價。`symbols` 可跨市場;`market` 省略時按程式碼**自動識別**並分組,每市場走一次 Engine。 |
| `klines` | `symbol, *, market, days=120, min_count=1` | `list[Bar]` | 單隻日 K。`min_count`:某源條數 `< min_count` 視為不足→試下一個;全不足→返回最長的那個。 |
| `capital_flow` | `symbol, *, market="CN"` | `CapitalFlow \| None` | 單隻資金流向。 |
| `events` | `symbols, *, market="CN", since_days=7` | `list[EventItem]` | 批次結構化事件(東財公告→型別/重要度啟發式)。 |
| `flash_news` | `*, market="CN", limit=50, keyword=None` | `list[FlashNews]` | 快訊(7×24)。**市場級**(`symbols` 恆空),仍走 Engine 做主備/快取/健康度;`keyword` 在拿到結果後本地過濾 title/content。 |
| `fundamentals` | `symbols, *, market=None` | `list[Fundamentals]` | 批次基本面/財務。跨市場自動分組,範式同 `quotes`。 |
| `dragon_tiger` | `*, date=None, market="CN"` | `list[DragonTigerItem]` | 龍虎榜,**市場級**單日快照。`date` 未給出時不猜測"今天",直接返回 `[]`。 |
| `margin` | `symbols, *, market=None` | `list[MarginItem]` | 批次融資融券(每隻取最新一條快照)。 |
| `shareholders` | `symbols, *, market=None` | `list[ShareholderItem]` | 批次股東戶數(每隻取最新一期)。 |
| `dividend` | `symbols, *, market=None` | `list[DividendItem]` | 批次分紅(每隻返回全部歷史)。 |
| `northbound` | `*, market="CN"` | `list[NorthboundItem]` | 北向資金,**市場級**,取當日末值快照。 |
| `index_quotes` | `tencent_symbols` | `list[dict]` | 指數行情。按**原始騰訊符號**(`sh000001`/`hkHSI`/`usDJI`…)取,不經 `Symbol.parse`(指數程式碼可能與個股撞號)。不經 Engine/registry。 |
| `index_klines` | `code, *, market, days=120` | `list[Bar]` | 指數日K。僅 `INDEX_SECID`(client.py)顯式對映的指數(滬深300/上證/深成指/創業板指/恒生)有資料;未對映(如美股指數)→ `[]`(fail-soft)。不經 Engine/registry。 |
| `hot_stocks` / `hot_boards` / `board_stocks` | 見下 | `list[HotStock/HotBoard]` | 東財熱門榜。**市場級、不經 Engine**(非 symbol 模型),直連 `DiscoveryVendor`。`hot_stocks(*, market="CN", mode="turnover", limit=20, proxy=None)`、`hot_boards(*, market="CN", mode="gainers", limit=12, proxy=None)`、`board_stocks(*, board_code, mode="gainers", limit=20, proxy=None)`。 |
| `health` | — | `dict[str, dict]` | 每個 vendor 的記憶體健康度快照(成功率 / p50 延遲 / 最近錯誤 / 樣本數)。 |

> `klines`/`capital_flow`/`events` **不在包內快取**(`cache_ttl_sec=0`),快取/節流交給宿主(PanWatch 的 collector 層有市場態感知快取);`quotes` 有 5s 短 TTL 防抖;`flash_news` 30s、`fundamentals`/`dragon_tiger`/`margin`/`shareholders`/`dividend` 300s、`northbound` 60s(均為包內 Engine 層 TTL,詳見 `client.py`)。

### 型別

- `Symbol(market: Market, code)` —— `Symbol.parse("600519")`(自動識別)/ `Symbol.parse("00700", "HK")`;`.to_tencent()` / `.to_eastmoney_secid()` / `.to_yfinance()`。`Market` = `CN` / `HK` / `US`。
- `Quote`:symbol / market / current_price / name / prev_close / open_price / high_price / low_price / change_amount / change_pct / volume / turnover / turnover_rate / volume_ratio / pe_ratio / circulating_market_value / total_market_value / timestamp。
- `Bar`:date / open / close / high / low / volume。
- `CapitalFlow`:symbol / name / main_net_inflow / main_net_inflow_pct / super_/big_/mid_/small_net_inflow / main_net_5d。
- `EventItem`:source / external_id / event_type / title / publish_time / symbols / importance / url。
- `FlashNews`:source / external_id / title / content / publish_time / symbols / importance / url。
- `Fundamentals`:symbol / market / name + 估值類(pe_ttm / pe_static / pb / ps_ttm / total_market_value / circulating_market_value / dividend_yield / total_shares / float_shares)+ 財報類(eps / bps / roe / revenue / net_profit / gross_margin / net_margin / revenue_yoy / net_profit_yoy / report_date)/ timestamp。
- `DragonTigerItem`:trade_date / symbol / name / reason / close / change_pct / net_buy / buy_amt / sell_amt / turnover_pct。
- `MarginItem`:date / symbol / rz_balance / rz_buy / rz_repay / rq_balance / rq_sell_vol / rq_repay_vol / total_balance。
- `ShareholderItem`:report_date / symbol / holder_num / change_num / change_ratio / avg_shares。
- `DividendItem`:ex_date / symbol / dividend_per_share / transfer_ratio / bonus_ratio / progress。
- `NorthboundItem`:date / hgt_net / sgt_net / total_net / time。
- `HotStock` / `HotBoard`:榜單條目。

### 埠(解耦宿主的關鍵)

```python
class ConfigProvider(Protocol):
    def sources_for(self, datatype: str, market: str | None) -> list[SourceConfig]: ...

class MetricsSink(Protocol):
    def record(self, *, vendor, datatype, market, ok, count, latency_ms, error="") -> None: ...

@dataclass
class SourceConfig:
    vendor: str            # "tencent" / "sina" / "eastmoney" / ...
    priority: int = 100    # 越小越優先
    enabled: bool = True
    config: dict = {}      # 憑證/引數:token / cookies / proxy ...(透傳給 vendor.fetch)
    supports_batch: bool = False
```

內建預設實現:`StaticConfigProvider({datatype: [SourceConfig, ...]})`、`InMemoryMetricsSink()`(滾動視窗最近 100 次)。宿主可換成自己的實現(如 PanWatch 用 DB 表驅動的 `DbConfigProvider`,見下)。

## 資料型別覆蓋矩陣(11 類)

以 `src/marketdata/registry.py` 的 `VENDOR_CLASSES_BY_TYPE` 為準——這是 type→vendor 的權威清單,新增/調整 vendor 必須同步這裡。

| type | 返回 dataclass | 已實現 vendor(provider 名) | 覆蓋市場 | 粒度 |
|---|---|---|---|---|
| `quote` | `Quote` | `tencent` / `sina` / `eastmoney` / `yfinance` | tencent: CN+HK+US;sina: US+HK;eastmoney: CN;yfinance: HK+US(可選依賴) | 按 symbol |
| `kline` | `Bar` | `tencent` / `stooq` / `eastmoney` / `yahoo` | tencent: CN+HK+US;eastmoney: CN+HK;stooq: US;yahoo: US+HK | 按 symbol |
| `capital_flow` | `CapitalFlow` | `eastmoney` / `sina` | eastmoney: CN+HK+US;sina: CN | 按 symbol |
| `events` | `EventItem` | `eastmoney` | CN | 按 symbol |
| `flash_news` | `FlashNews` | `cls` / `sina` / `eastmoney` | 均 CN | **市場級**(symbols 恆空) |
| `fundamentals` | `Fundamentals` | `tencent` / `eastmoney` | tencent: CN;eastmoney: CN+HK+US | 按 symbol |
| `dragon_tiger` | `DragonTigerItem` | `eastmoney` | CN | **市場級**(單日快照,按 date 過濾) |
| `margin` | `MarginItem` | `eastmoney` | CN | 按 symbol(取最新一條) |
| `shareholders` | `ShareholderItem` | `eastmoney` | CN | 按 symbol(取最新一期) |
| `dividend` | `DividendItem` | `eastmoney` | CN | 按 symbol(返回全部歷史) |
| `northbound` | `NorthboundItem` | `ths`(同花順 hexin) | CN | **市場級**(symbols 恆空,取當日末值) |

此外還有兩類**不進 registry/不進 Engine**的特殊入口(市場級、非 symbol 模型,故不計入上述 11 類):
- **discovery**(`hot_stocks`/`hot_boards`/`board_stocks`):東財熱門榜,單源,直連 `DiscoveryVendor`。
- **index**(`index_quotes`/`index_klines`):指數行情/K線,`index_quotes` 走騰訊原始符號,`index_klines` 走 `INDEX_SECID` 顯式對映 + 東財K線。

**故障轉移**:Engine 按 `ConfigProvider` 返回的 priority 順序試 vendor,過濾 `enabled` + `supports_markets`;首個"成功且非空(kline 為 ≥`min_count`)"即返回並快取;全失敗返回空。每次取數經 `MetricsSink` 記錄,`health()` 可讀。

### 權威表:`PACKAGE_VENDORS_BY_TYPE`

`registry.py` 裡的 `PACKAGE_VENDORS_BY_TYPE`(由 `VENDOR_CLASSES_BY_TYPE` 派生)是"某 type 合法 vendor 名集合"的**唯一真相源**——不會出現"改了 Engine 忘了改檔案/權威表"的漂移。

宿主(PanWatch `DataSource` 表)據此判定某行 `(type, provider)` 是否為孤兒:

```python
legal(type) = PACKAGE_VENDORS_BY_TYPE.get(type, frozenset()) | seed 內該 type 的 provider 集合
```

`discovery`/`index` 是市場級、非 symbol 模型,不進 Engine/不進 `DataSource` taxonomy,故不出現在此表。

### ⚠️ 欄位對映校準現狀

B 階段新增型別(`flash_news` / `fundamentals` / `dragon_tiger` / `margin` / `shareholders` / `dividend` / `northbound`)的欄位解析,多數**未經真實網路抓取驗證**(開發沙箱代理會攔截東財/同花順等介面,只能靠介面檔案 + 歷史 PanWatch collector 實現推斷欄位對映)。各 dataclass 的 docstring 裡已標註"欄位待實抓校準"。首次在生產接入這些型別時,建議:

1. 在「資料來源」頁對該 `(type, provider)` 點「測試」,核對返回欄位是否符合預期(尤其是 `NorthboundItem.sgt_net` 這類已知不穩定欄位)。
2. 若欄位錯位/為空,對照 vendor 原始碼(`src/marketdata/vendors/*.py`)與東財/同花順介面實際回應調整解析邏輯,而不是照抄檔案欄位名。

## 新增一個資料源

1. 在 `src/marketdata/vendors/` 寫一個 vendor,繼承對應標記基類——`QuoteVendor` / `KlineVendor` / `CapitalFlowVendor` / `EventsVendor` / `FlashNewsVendor` / `FundamentalsVendor` / `DragonTigerVendor` / `MarginVendor` / `ShareholdersVendor` / `DividendVendor` / `NorthboundVendor`(均定義在 `vendors/base.py`)——實現 `fetch(symbols: list[Symbol], config: dict) -> list[<型別>]`,用包內 `market_get` 發請求(`verify=`/`proxy=`/`encoding=` 按源需要)。設 `name` 與 `supports_markets`。
2. 在 `registry.py` 的 `VENDOR_CLASSES_BY_TYPE[<datatype>]` 里加一行 `"<vendor_name>": <VendorClass>`(`PACKAGE_VENDORS_BY_TYPE` 與 `MarketData.__init__` 的 Engine `vendors={}` 都由它自動派生,不用另外改 `client.py`)。
3. 透過 `ConfigProvider` 給它一條 `SourceConfig(vendor="<name>", priority=...)`(宿主側配置/種子)。
4. 加解析單測(monkeypatch 該 vendor 模組的 `market_get`)。

## 嵌入宿主(PanWatch 為例)

宿主實現兩個埠即可接入:

```python
class DbConfigProvider:                       # 讀 DataSource 表 → SourceConfig
    def sources_for(self, datatype, market):
        rows = query(DataSource, type=datatype, enabled=True, order_by=priority)
        return [SourceConfig(vendor=r.provider, priority=r.priority,
                             config=r.config or {}, supports_batch=bool(r.supports_batch))
                for r in rows]

md = MarketData(config=DbConfigProvider())    # metrics 用預設記憶體 sink
```

PanWatch 側是**單一路徑**:`src/core/marketdata_client.py` 用程式級單例 `get_market_data()` 持有一個 `MarketData(config=DbConfigProvider())`,各 collector/agent 直接呼叫它取數;沒有 flag、沒有相容層分支、沒有回退到舊 `akshare_collector`(舊實現已刪除)。`health()` 喂到「資料來源」頁的健康度面板。

> 歷史備註(已移除,僅存檔參考):早期 Phase 1 曾用 `USE_MARKETDATA` 環境變數做灰度切換,新舊兩套並存;該 flag 與舊路徑已在後續階段整體下線,現在只有一條路徑。

## 測試

```bash
cd packages/marketdata && python -m pytest -q     # 全部 mock HTTP(不發真實網路)
```

## License / 參考

抓取端點參考並適配自 `simonlin1212/global-stock-data`、`simonlin1212/a-stock-data`(Apache-2.0)。
