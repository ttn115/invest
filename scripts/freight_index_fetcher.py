"""
航運指數抓取器 (Freight Index Fetcher)

抓取 SCFI（上海出口貨櫃綜合運費指數）和 BDI（波羅的海乾散貨指數）
供台股掃描器對貨櫃航運股（長榮 2603、陽明 2609、萬海 2615）的深度分析使用。

指數說明：
  SCFI → 貨櫃航運運費，長榮/陽明/萬海的直接收入驅動力
  BDI  → 乾散貨運費，與長榮關係較低，僅供宏觀參考

資料來源（依優先序）：
  BDI  → stockq.org → handybulk.com
  SCFI → container-news.com → tradingeconomics.com

快取：data/freight_cache.json（TTL 6 小時，避免重複 HTTP 請求）
"""

import re
import json
import datetime as dt
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional, Tuple
import httpx
from loguru import logger

try:
    from bs4 import BeautifulSoup
    _BS4 = True
except ImportError:
    _BS4 = False
    logger.debug("bs4 not available, using regex-only parsing")

_CACHE_PATH = Path(__file__).parent.parent / "data" / "freight_cache.json"
_CACHE_TTL_HOURS = 6

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Cache-Control": "no-cache",
}
_TIMEOUT = 20.0


# ── 資料結構 ────────────────────────────────────────────────────────────────

@dataclass
class FreightData:
    name: str           # "BDI" or "SCFI"
    current: float      # 最新指數值
    prev: float         # 前一期值（0 = 無資料）
    pct_1w: float       # 週變化 %（0 = 無資料）
    pct_1m: float       # 月變化 %（0 = 無資料）
    trend: str          # "UP" / "DOWN" / "FLAT"
    date: str           # 最新資料日期
    source: str         # 成功取得資料的 URL
    ok: bool = True     # False = 所有來源均失敗

    @property
    def trend_icon(self) -> str:
        return {"UP": "↑", "DOWN": "↓", "FLAT": "→"}.get(self.trend, "?")

    @property
    def trend_emoji(self) -> str:
        return {"UP": "🟢", "DOWN": "🔴", "FLAT": "⚪"}.get(self.trend, "❓")

    @property
    def has_trend(self) -> bool:
        """
        是否具備可信的『趨勢』資料（而非僅有水位）。

        BUG 修正（2026-09）：pct_1m == 0 有兩種完全不同的含義——
        「真的沒變動」與「抓不到比較值」。舊版一律當成前者，導致
        下游把『資料缺失』誤判為『貿易疲弱』。此處以 prev 是否存在區分。
        """
        return self.ok and self.prev > 0 and (self.pct_1w != 0 or self.pct_1m != 0)

    def summary(self) -> str:
        if not self.ok:
            return f"{self.name}: ⚠️ 資料抓取失敗"
        parts = [f"{self.name}: **{self.current:,.0f}** {self.trend_emoji}{self.trend_icon}"]
        if self.pct_1w != 0:
            parts.append(f"{self.pct_1w:+.1f}%週")
        if self.pct_1m != 0:
            parts.append(f"{self.pct_1m:+.1f}%月")
        if not self.has_trend:
            parts.append("⚠️趨勢資料缺失")
        parts.append(f"({self.date})")
        return " ".join(parts)


@dataclass
class FreightContext:
    bdi: FreightData
    scfi: FreightData

    def dalio_position(self, rate_10y: float) -> str:
        """
        Dalio 矩陣象限（容器航運視角）。

        BUG 修正（2026-09）：SCFI 若只有水位、沒有趨勢資料，
        舊版會把「未知」當成「貿易弱」，輸出假的「雙殺風險」。
        現在明確回報資料缺失，不做無根據的判定。
        """
        rate_label = "利率高" if rate_10y >= 3.5 else "利率低"
        if not self.scfi.has_trend:
            return f"{rate_label}＋貿易趨勢未知 → ⚠️ 無法判定（SCFI 趨勢資料缺失）"

        rate_high = rate_10y >= 3.5
        trade_strong = self.scfi.pct_1m > 5
        if rate_high and trade_strong:
            return "利率高＋貿易強 → 勉強到還不錯"
        if rate_high and not trade_strong:
            return "利率高＋貿易弱 → 雙殺風險"
        if not rate_high and trade_strong:
            return "利率低＋貿易強 → 航運暴利"
        return "利率低＋貿易弱 → 勉強"

    def evergreen_outlook(self, rate_10y: float) -> str:
        """
        長榮/陽明/萬海整體展望標籤。

        BUG 修正（2026-09）：趨勢資料缺失時回傳 UNKNOWN，
        不再因 pct_1m==0 落入 NEUTRAL 而給出看似有依據的結論。
        """
        if not self.scfi.has_trend:
            return "UNKNOWN"
        m = self.scfi.pct_1m
        rate_high = rate_10y >= 3.5
        if m > 20 and not rate_high:
            return "BULLISH"
        if m > 10:
            return "MILDLY_BULLISH" if rate_high else "BULLISH"
        if m > -5:
            return "NEUTRAL" if rate_high else "MILDLY_BULLISH"
        if m > -15:
            return "MILDLY_BEARISH"
        return "BEARISH"

    def outlook_emoji(self, rate_10y: float) -> str:
        return {
            "BULLISH": "🚀", "MILDLY_BULLISH": "🟢",
            "NEUTRAL": "⚪", "MILDLY_BEARISH": "🟡",
            "BEARISH": "🔴", "UNKNOWN": "❓",
        }.get(self.evergreen_outlook(rate_10y), "❓")

    def summary_line(self) -> str:
        return f"{self.scfi.summary()} | {self.bdi.summary()}"


# ── 工具函式 ────────────────────────────────────────────────────────────────

def _stale(name: str) -> FreightData:
    return FreightData(
        name=name, current=0, prev=0, pct_1w=0, pct_1m=0,
        trend="FLAT", date="N/A", source="", ok=False
    )


def _compute_trend(current: float, prev: float) -> str:
    if prev == 0:
        return "FLAT"
    pct = (current - prev) / prev * 100
    if pct > 1.5:
        return "UP"
    if pct < -1.5:
        return "DOWN"
    return "FLAT"


# ── 資料清洗與合理性檢查 ────────────────────────────────────────────────────
# BUG 修正（2026-09）：頁面上的「年份」會落在指數合理區間內被誤判成指數值。
# 實例：stockq BDI 頁回傳 [728.0, 2018.0, 2018.0]，程式把年份 2018 當成上週 BDI，
#      算出 -63.9% 的假崩跌。真實指數值帶小數，年份是精確整數 → 以此區分。

_YEAR_MIN, _YEAR_MAX = 1990, 2035

# 單週合理變動上限（%）；超過視為解析錯誤，不採用該 prev
_MAX_WEEKLY_MOVE = {"BDI": 35.0, "SCFI": 45.0}


def _looks_like_year(v: float) -> bool:
    return v == int(v) and _YEAR_MIN <= v <= _YEAR_MAX


def _drop_year_like(nums: List[float]) -> List[float]:
    """
    濾掉疑似年份的精確整數（真實運價指數多帶小數）。
    若濾完就沒有資料，則保留原始清單（寧可有值也不要空手）。
    """
    filtered = [v for v in nums if not _looks_like_year(v)]
    return filtered if filtered else nums


def _page_has_index_data(html: str) -> bool:
    """
    判斷頁面是否真的含有「日期 + 指數值」的資料結構。
    用於避免 JS 動態載入的頁面被 regex 掃出無關數字（假資料比沒資料更危險）。
    """
    plain = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    plain = re.sub(r"<[^>]+>", " ", plain)
    plain = " ".join(plain.split())
    # 需同時出現：日期樣式，且其附近有帶小數的四位數（指數值特徵）
    for m in re.finditer(r"\d{4}[/-]\d{1,2}[/-]\d{1,2}", plain):
        seg = plain[m.end(): m.end() + 60]
        if re.search(r"\b\d{3,4}\.\d+\b", seg):
            return True
    return False


def _sanitize(fd: FreightData) -> FreightData:
    """
    合理性檢查：單週變動若超過上限，代表 prev 很可能解析錯誤。
    此時保留 current（通常正確），但清掉不可信的比較值，避免汙染下游判讀。
    """
    limit = _MAX_WEEKLY_MOVE.get(fd.name, 50.0)
    if fd.prev and abs(fd.pct_1w) > limit:
        logger.warning(
            f"⚠️ {fd.name} 單週變動 {fd.pct_1w:+.1f}% 超過合理上限 {limit}%，"
            f"判定 prev={fd.prev} 解析有誤 → 捨棄比較值（current={fd.current} 保留）"
        )
        fd.prev = 0.0
        fd.pct_1w = 0.0
        fd.trend = "FLAT"
    if fd.pct_1m and abs(fd.pct_1m) > limit * 2:
        logger.warning(f"⚠️ {fd.name} 月變動 {fd.pct_1m:+.1f}% 不合理 → 捨棄")
        fd.pct_1m = 0.0
    return fd


def _table_numbers(html: str, lo: float, hi: float) -> List[float]:
    """
    BeautifulSoup 優先：從 <table> 中提取在 [lo, hi] 範圍內的數字，
    保留文件順序。若 bs4 不可用則回退到 regex。
    """
    nums: List[float] = []
    if _BS4:
        soup = BeautifulSoup(html, "html.parser")
        for td in soup.find_all(["td", "th"]):
            raw = td.get_text(strip=True).replace(",", "")
            try:
                v = float(raw)
                if lo <= v <= hi:
                    nums.append(v)
            except ValueError:
                pass
    if not nums:
        for m in re.finditer(r"[\d,]+(?:\.\d+)?", html):
            raw = m.group().replace(",", "")
            try:
                v = float(raw)
                if lo <= v <= hi:
                    nums.append(v)
            except ValueError:
                pass
    return nums


def _keyword_numbers(html: str, keyword: str, lo: float, hi: float, window: int = 200) -> List[float]:
    """在 keyword 附近提取數字（更精確）"""
    nums: List[float] = []
    for match in re.finditer(re.escape(keyword), html, re.IGNORECASE):
        segment = html[max(0, match.start() - window): match.end() + window]
        for m in re.finditer(r"[\d,]+(?:\.\d+)?", segment):
            raw = m.group().replace(",", "")
            try:
                v = float(raw)
                if lo <= v <= hi:
                    nums.append(v)
            except ValueError:
                pass
    return nums


# ── BDI 來源 ────────────────────────────────────────────────────────────────

def _bdi_stockq(client: httpx.Client) -> FreightData:
    """
    從 stockq 解析 BDI。

    BUG 修正（2026-09）：原本用「關鍵字附近抓數字」，但 "BDI" 首次出現在
    <title>，導致抓到標題區的無關數字（含年份 2018）。頁面真正可靠的是
    「Index Return」區塊的百分比，與「MA5~MA260」均線值。
      Index Return: 1 day / 1 week / MTD / 1 month / 3 months / 6 months / YTD / 1 year
      Baltic Dry MA5 MA10 MA20 MA60 MA120 MA260
    注意：MA 值是均線，不是歷史指數序列（舊版誤把 MA5/MA10 當成本週/上週）。
    """
    url = "https://en.stockq.org/index/BDI.php"
    r = client.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()

    plain = re.sub(r"<[^>]+>", " ", r.text)
    plain = " ".join(plain.split())

    # 1) Index Return 區塊：取 1 day / 1 week / MTD / 1 month 的百分比
    pct_1w = pct_1m = 0.0
    m = re.search(
        r"Index Return.*?1 day\s*1 week\s*MTD\s*1 month(.*?)(?:Intraday|Technical|$)",
        plain, re.IGNORECASE)
    if m:
        pcts = re.findall(r"(-?\d+(?:\.\d+)?)%", m.group(1))
        if len(pcts) >= 4:
            pct_1w = float(pcts[1])   # 1 week
            pct_1m = float(pcts[3])   # 1 month

    # 2) 均線區塊取 MA5 當「近期水位」的近似值（頁面未直接提供即時指數）
    current = 0.0
    ma = re.search(r"MA5\s*MA10\s*MA20\s*MA60\s*MA120\s*MA260\s*Deviation\s*"
                   r"([\d.]+)\s*([\d.]+)", plain)
    if ma:
        current = float(ma.group(1))          # MA5
    if not current:
        nums = _drop_year_like(_table_numbers(r.text, 400, 14000))
        if nums:
            current = nums[0]
    if not current:
        raise ValueError("stockq BDI: 無法定位指數值")

    # prev 由 current 與週變動反推（頁面未直接給上週值）
    prev = current / (1 + pct_1w / 100) if pct_1w else 0.0

    return _sanitize(FreightData(
        name="BDI", current=round(current, 2), prev=round(prev, 2),
        pct_1w=round(pct_1w, 2), pct_1m=round(pct_1m, 2),
        trend=_compute_trend(current, prev),
        date=dt.date.today().isoformat(), source=url,
    ))


def _bdi_handybulk(client: httpx.Client) -> FreightData:
    url = "https://www.handybulk.com/baltic-dry-index/"
    r = client.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()

    nums = _drop_year_like(_table_numbers(r.text, 400, 14000))
    if len(nums) < 2:
        nums = _drop_year_like(_keyword_numbers(r.text, "Baltic", 400, 14000))
    if not nums:
        raise ValueError("handybulk BDI: no usable values")

    current = nums[0]
    prev = nums[1] if len(nums) >= 2 else 0
    pct_1w = (current - prev) / prev * 100 if prev else 0
    return _sanitize(FreightData(
        name="BDI", current=current, prev=prev,
        pct_1w=round(pct_1w, 2), pct_1m=0,
        trend=_compute_trend(current, prev),
        date=dt.date.today().isoformat(), source=url,
    ))


# ── SCFI 來源 ───────────────────────────────────────────────────────────────

def _scfi_containernews(client: httpx.Client) -> FreightData:
    url = "https://container-news.com/scfi/"
    r = client.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()

    # BUG 修正（2026-09）：此頁為 JS 動態載入，純文字中「沒有任何 SCFI 數值」，
    # 舊版 regex 掃全頁會抓到無關數字（曾誤回 447、2015）。
    # → 先確認頁面確實含有可辨識的數據結構，否則誠實失敗，不要回傳垃圾。
    if not _page_has_index_data(r.text):
        raise ValueError("containernews SCFI: 頁面無可解析的指數資料（疑為 JS 動態載入）")

    # SCFI range：200–7000（點）；濾掉年份（2016~2025 都落在此區間）
    nums = _drop_year_like(_keyword_numbers(r.text, "SCFI", 200, 7000))
    if len(nums) < 2:
        nums = _drop_year_like(_table_numbers(r.text, 200, 7000))
    if not nums:
        raise ValueError("containernews SCFI: no usable values")

    current = nums[0]
    prev = nums[1] if len(nums) >= 2 else 0
    pct_1w = (current - prev) / prev * 100 if prev else 0
    pct_1m = (current - nums[min(4, len(nums) - 1)]) / nums[min(4, len(nums) - 1)] * 100 if len(nums) >= 5 else 0
    return _sanitize(FreightData(
        name="SCFI", current=current, prev=prev,
        pct_1w=round(pct_1w, 2), pct_1m=round(pct_1m, 2),
        trend=_compute_trend(current, prev),
        date=dt.date.today().isoformat(), source=url,
    ))


def _scfi_tradingeconomics(client: httpx.Client) -> FreightData:
    url = "https://tradingeconomics.com/commodity/containerized-freight-index"
    r = client.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()

    nums = _drop_year_like(_keyword_numbers(r.text, "Containerized", 200, 7000))
    if not nums:
        nums = _drop_year_like(_table_numbers(r.text, 200, 7000))
    if not nums:
        raise ValueError("tradingeconomics SCFI: no values found")

    current = nums[0]
    prev = nums[1] if len(nums) >= 2 else 0
    pct_1w = (current - prev) / prev * 100 if prev else 0
    return _sanitize(FreightData(
        name="SCFI", current=current, prev=prev,
        pct_1w=round(pct_1w, 2), pct_1m=0,
        trend=_compute_trend(current, prev),
        date=dt.date.today().isoformat(), source=url,
    ))


# ── 10 年期公債殖利率 ───────────────────────────────────────────────────────
# BUG 修正（2026-09）：原本各處寫死 rate_10y=4.37，從不抓即時值，
# 導致 Dalio 象限與航運展望長期用過時利率判斷（實際已達 4.80）。
# 註：yfinance 的 ^TNX 已是百分比（4.796 = 4.796%），不需再除以 10。

_RATE_FALLBACK = 4.37


def fetch_10y_rate(default: float = _RATE_FALLBACK) -> float:
    """抓取美國 10 年期公債殖利率（%）。失敗時回退到 default。"""
    try:
        import yfinance as yf
        s = yf.Ticker("^TNX").history(period="5d")["Close"].dropna()
        if len(s):
            v = float(s.iloc[-1])
            if 0.1 <= v <= 20:          # 合理性檢查（避免單位錯誤）
                return round(v, 2)
            logger.warning(f"⚠️ ^TNX 回傳 {v} 超出合理範圍，改用預設 {default}%")
    except Exception as e:
        logger.debug(f"10Y 殖利率抓取失敗，改用預設 {default}%: {e}")
    return default


# ── 快取 ────────────────────────────────────────────────────────────────────

def _load_cache() -> Optional[dict]:
    try:
        if not _CACHE_PATH.exists():
            return None
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        ts = dt.datetime.fromisoformat(data.get("timestamp", "2000-01-01T00:00:00"))
        age_h = (dt.datetime.now() - ts).total_seconds() / 3600
        if age_h < _CACHE_TTL_HOURS:
            return data
        logger.debug(f"Freight cache expired ({age_h:.1f}h > {_CACHE_TTL_HOURS}h)")
    except Exception as e:
        logger.debug(f"Freight cache load failed: {e}")
    return None


def _save_cache(bdi: FreightData, scfi: FreightData) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(
            json.dumps({
                "timestamp": dt.datetime.now().isoformat(),
                "bdi": asdict(bdi),
                "scfi": asdict(scfi),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.debug(f"Freight cache save failed: {e}")


def _from_dict(d: dict) -> FreightData:
    return FreightData(**{k: v for k, v in d.items() if k in FreightData.__dataclass_fields__})


# ── 主要類別 ────────────────────────────────────────────────────────────────

class FreightIndexFetcher:
    """
    多來源、帶本地快取的航運指數抓取器。

    用法：
        ctx = FreightIndexFetcher().fetch_all()
        print(ctx.scfi.summary())
        rate = fetch_10y_rate()
        print(f"10Y 殖利率: {rate:.2f}%")
        print(ctx.dalio_position(rate_10y=rate))
    """

    def fetch_bdi(self) -> FreightData:
        for fn in [_bdi_stockq, _bdi_handybulk]:
            try:
                with httpx.Client(follow_redirects=True) as client:
                    data = fn(client)
                logger.info(f"🌊 BDI {data.current:,.0f} [{fn.__name__}]")
                return data
            except Exception as e:
                logger.debug(f"BDI {fn.__name__} failed: {e}")
        logger.warning("⚠️ BDI 所有來源失敗")
        return _stale("BDI")

    def fetch_scfi(self) -> FreightData:
        for fn in [_scfi_containernews, _scfi_tradingeconomics]:
            try:
                with httpx.Client(follow_redirects=True) as client:
                    data = fn(client)
                logger.info(f"🌊 SCFI {data.current:,.0f} [{fn.__name__}]")
                return data
            except Exception as e:
                logger.debug(f"SCFI {fn.__name__} failed: {e}")
        logger.warning("⚠️ SCFI 所有來源失敗")
        return _stale("SCFI")

    def fetch_all(self) -> FreightContext:
        cache = _load_cache()
        if cache:
            try:
                bdi = _from_dict(cache["bdi"])
                scfi = _from_dict(cache["scfi"])
                logger.info(f"📦 航運指數（快取）BDI:{bdi.current:,.0f} SCFI:{scfi.current:,.0f}")
                return FreightContext(bdi=bdi, scfi=scfi)
            except Exception:
                pass

        logger.info("📡 抓取航運指數 SCFI / BDI...")
        bdi = self.fetch_bdi()
        scfi = self.fetch_scfi()
        if bdi.ok or scfi.ok:
            _save_cache(bdi, scfi)
        return FreightContext(bdi=bdi, scfi=scfi)
