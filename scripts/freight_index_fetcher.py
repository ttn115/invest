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

    def summary(self) -> str:
        if not self.ok:
            return f"{self.name}: ⚠️ 資料抓取失敗"
        parts = [f"{self.name}: **{self.current:,.0f}** {self.trend_emoji}{self.trend_icon}"]
        if self.pct_1w != 0:
            parts.append(f"{self.pct_1w:+.1f}%週")
        if self.pct_1m != 0:
            parts.append(f"{self.pct_1m:+.1f}%月")
        parts.append(f"({self.date})")
        return " ".join(parts)


@dataclass
class FreightContext:
    bdi: FreightData
    scfi: FreightData

    def dalio_position(self, rate_10y: float) -> str:
        """Dalio 矩陣象限（容器航運視角）"""
        rate_high = rate_10y >= 3.5
        trade_strong = self.scfi.ok and self.scfi.pct_1m > 5
        if rate_high and trade_strong:
            return "利率高＋貿易強 → 勉強到還不錯"
        if rate_high and not trade_strong:
            return "利率高＋貿易弱 → 雙殺風險"
        if not rate_high and trade_strong:
            return "利率低＋貿易強 → 航運暴利"
        return "利率低＋貿易弱 → 勉強"

    def evergreen_outlook(self, rate_10y: float) -> str:
        """長榮/陽明/萬海整體展望標籤"""
        if not self.scfi.ok:
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
    url = "https://en.stockq.org/index/BDI.php"
    r = client.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()

    # 先在 keyword 附近取數
    nums = _keyword_numbers(r.text, "BDI", 400, 14000)
    if len(nums) < 2:
        nums = _table_numbers(r.text, 400, 14000)
    if len(nums) < 2:
        raise ValueError(f"stockq BDI: only {len(nums)} values found")

    current, prev = nums[0], nums[1]
    pct_1w = (current - prev) / prev * 100 if prev else 0
    pct_1m = (current - nums[min(4, len(nums) - 1)]) / nums[min(4, len(nums) - 1)] * 100 if len(nums) >= 5 else 0
    return FreightData(
        name="BDI", current=current, prev=prev,
        pct_1w=round(pct_1w, 2), pct_1m=round(pct_1m, 2),
        trend=_compute_trend(current, prev),
        date=dt.date.today().isoformat(), source=url,
    )


def _bdi_handybulk(client: httpx.Client) -> FreightData:
    url = "https://www.handybulk.com/baltic-dry-index/"
    r = client.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()

    nums = _table_numbers(r.text, 400, 14000)
    if len(nums) < 2:
        nums = _keyword_numbers(r.text, "Baltic", 400, 14000)
    if len(nums) < 2:
        raise ValueError(f"handybulk BDI: only {len(nums)} values")

    current, prev = nums[0], nums[1]
    pct_1w = (current - prev) / prev * 100 if prev else 0
    return FreightData(
        name="BDI", current=current, prev=prev,
        pct_1w=round(pct_1w, 2), pct_1m=0,
        trend=_compute_trend(current, prev),
        date=dt.date.today().isoformat(), source=url,
    )


# ── SCFI 來源 ───────────────────────────────────────────────────────────────

def _scfi_containernews(client: httpx.Client) -> FreightData:
    url = "https://container-news.com/scfi/"
    r = client.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()

    # SCFI range：200–7000（點）
    nums = _keyword_numbers(r.text, "SCFI", 200, 7000)
    if len(nums) < 2:
        nums = _table_numbers(r.text, 200, 7000)
    if len(nums) < 2:
        raise ValueError(f"containernews SCFI: only {len(nums)} values")

    current, prev = nums[0], nums[1]
    pct_1w = (current - prev) / prev * 100 if prev else 0
    pct_1m = (current - nums[min(4, len(nums) - 1)]) / nums[min(4, len(nums) - 1)] * 100 if len(nums) >= 5 else 0
    return FreightData(
        name="SCFI", current=current, prev=prev,
        pct_1w=round(pct_1w, 2), pct_1m=round(pct_1m, 2),
        trend=_compute_trend(current, prev),
        date=dt.date.today().isoformat(), source=url,
    )


def _scfi_tradingeconomics(client: httpx.Client) -> FreightData:
    url = "https://tradingeconomics.com/commodity/containerized-freight-index"
    r = client.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    r.raise_for_status()

    nums = _keyword_numbers(r.text, "Containerized", 200, 7000)
    if not nums:
        nums = _table_numbers(r.text, 200, 7000)
    if not nums:
        raise ValueError("tradingeconomics SCFI: no values found")

    current = nums[0]
    prev = nums[1] if len(nums) >= 2 else 0
    pct_1w = (current - prev) / prev * 100 if prev else 0
    return FreightData(
        name="SCFI", current=current, prev=prev,
        pct_1w=round(pct_1w, 2), pct_1m=0,
        trend=_compute_trend(current, prev),
        date=dt.date.today().isoformat(), source=url,
    )


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
        print(ctx.dalio_position(rate_10y=4.37))
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
