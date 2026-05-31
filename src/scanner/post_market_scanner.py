"""
盤後掃描引擎 (Post-Market Scanner)

收盤後自動執行複合條件篩選，整合：
  - 成交量異常（量比 > 閾值）
  - 三大法人買賣超方向與強度
  - 融資融券籌碼變化
  - 技術面確認（均線排列、RSI 區間）

輸出帶有綜合評分（0~100）的候選名單，供投資顧問二次分析。

評分系統（ScoreCard）:
    法人多頭（三大法人買超）   ：最高 +35 分
    量能放大（量比 > 2）       ：最高 +20 分
    技術面多排（MA 排列）      ：最高 +25 分
    融資健康（融資未過熱）     ：最高 +10 分
    RSI 合理區間（40-70）      ：最高 +10 分
    ── 風險扣分 ──
    融資大增（散戶追高）       ：-15 分
    高點爆量收黑（出貨）       ：-20 分
    三大法人賣超               ：-15 分
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import urllib3
import pandas as pd
import requests
from loguru import logger

from src.data.chip_collector import ChipCollector
from src.data.indicators import IndicatorEngine

# 關閉 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─────────────────────────────────────────────
# 常數
# ─────────────────────────────────────────────
TWSE_BASE = "https://www.twse.com.tw/rwd/zh"
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
}


# ═══════════════════════════════════════════════════════════════
# 資料模型
# ═══════════════════════════════════════════════════════════════

@dataclass
class ScanCandidate:
    """
    掃描候選股票

    Attributes:
        stock_id      : 股票代號
        stock_name    : 股票名稱
        close         : 收盤價
        volume        : 今日成交量（張）
        volume_avg20  : 20日平均量（張）
        volume_ratio  : 量比（今日 / 20日均量）
        foreign_net   : 外資買賣超（張，正=買超）
        trust_net     : 投信買賣超（張）
        dealer_net    : 自營商買賣超（張）
        total_inst    : 三大法人合計買賣超（張）
        margin_change : 融資餘額變化（張）
        short_change  : 融券餘額變化（張）
        score         : 綜合評分（0~100）
        signals       : 觸發的信號清單
        risk_flags    : 風險警示清單
    """
    stock_id:     str
    stock_name:   str   = ""
    close:        float = 0.0
    change_pct:   float = 0.0
    volume:       int   = 0
    volume_avg20: float = 0.0
    volume_ratio: float = 0.0
    foreign_net:  int   = 0
    trust_net:    int   = 0
    dealer_net:   int   = 0
    total_inst:   int   = 0
    margin_change:int   = 0
    short_change: int   = 0
    rsi:          float = 0.0
    above_ma20:   bool  = False
    above_ma60:   bool  = False
    score:        int   = 0
    signals:      list  = field(default_factory=list)
    risk_flags:   list  = field(default_factory=list)

    def summary(self) -> str:
        direction = "▲" if self.change_pct >= 0 else "▼"
        return (
            f"[{self.stock_id}] {self.stock_name:8s} "
            f"${self.close:.1f} {direction}{abs(self.change_pct):.1f}%  "
            f"量比:{self.volume_ratio:.1f}x  "
            f"外資:{self.foreign_net:+,}張  投信:{self.trust_net:+,}張  "
            f"評分:{self.score}  "
            f"{'⚠️ '+' '.join(self.risk_flags) if self.risk_flags else '✅'}"
        )


@dataclass
class ScanResult:
    """掃描結果集合"""
    scan_date:   str
    total_stocks:int
    candidates:  list[ScanCandidate] = field(default_factory=list)
    filter_params: dict = field(default_factory=dict)

    @property
    def top(self) -> list[ScanCandidate]:
        return sorted(self.candidates, key=lambda x: x.score, reverse=True)

    def to_dataframe(self) -> pd.DataFrame:
        if not self.candidates:
            return pd.DataFrame()
        return pd.DataFrame([
            {
                "股票代號":    c.stock_id,
                "股票名稱":    c.stock_name,
                "收盤價":     c.close,
                "漲跌幅(%)":  c.change_pct,
                "成交量(張)": c.volume,
                "量比":       c.volume_ratio,
                "外資買賣超": c.foreign_net,
                "投信買賣超": c.trust_net,
                "自營商買賣超": c.dealer_net,
                "三大法人合計": c.total_inst,
                "融資變化":   c.margin_change,
                "融券變化":   c.short_change,
                "RSI":        round(c.rsi, 1),
                "在MA20上方": c.above_ma20,
                "在MA60上方": c.above_ma60,
                "綜合評分":   c.score,
                "信號":       " | ".join(c.signals),
                "風險警示":   " | ".join(c.risk_flags),
            }
            for c in self.top
        ])


# ═══════════════════════════════════════════════════════════════
# 評分引擎
# ═══════════════════════════════════════════════════════════════

class ScoreEngine:
    """
    多維度評分系統

    芒格原則：單一信號是雜訊，多重確認才是訊號。
    每個維度獨立打分，需跨越最低總分才進入候選名單。
    """

    # ── 評分權重（可從 config 覆蓋）──────────────────────────────

    WEIGHTS = {
        # 加分項
        "inst_buy_strong":    35,  # 三大法人大量買超（前提：外資主導）
        "inst_buy_normal":    20,  # 三大法人輕度買超
        "volume_surge_high":  20,  # 量比 >= 3x
        "volume_surge_mid":   12,  # 量比 >= 2x
        "ma_bullish_full":    25,  # 站上 MA20 + MA60（多頭排列）
        "ma_bullish_partial": 12,  # 只站上 MA20
        "rsi_sweet_spot":     10,  # RSI 40-65（未過熱）
        "margin_healthy":     10,  # 融資未大增（融資變化 < 平均量1%）

        # 扣分項
        "margin_surge":      -15,  # 融資大增（散戶追高警訊）
        "inst_sell":         -15,  # 三大法人賣超
        "top_reversal":      -20,  # 高點爆量收黑（出貨形態）
        "rsi_overbought":    -10,  # RSI > 75（超買）
    }

    def score(self, c: ScanCandidate) -> ScanCandidate:
        """對單一候選股票計分，填入 score / signals / risk_flags"""
        total = 0
        signals = []
        risks = []

        # ── 三大法人 ─────────────────────────────────────────────
        if c.total_inst > 3000:                        # 大量買超 >3000 張
            total += self.WEIGHTS["inst_buy_strong"]
            signals.append(f"法人強力買超 {c.total_inst:+,}張")
        elif c.total_inst > 500:                       # 輕度買超 >500 張
            total += self.WEIGHTS["inst_buy_normal"]
            signals.append(f"法人買超 {c.total_inst:+,}張")
        elif c.total_inst < -500:                      # 賣超
            total += self.WEIGHTS["inst_sell"]
            risks.append(f"法人賣超 {c.total_inst:,}張")

        # 外資單獨大買（外資影響力最大）
        if c.foreign_net > 2000:
            signals.append(f"外資大買 {c.foreign_net:+,}張")
        if c.trust_net > 500:
            signals.append(f"投信連買 {c.trust_net:+,}張")

        # ── 量能 ─────────────────────────────────────────────────
        if c.volume_ratio >= 3.0:
            total += self.WEIGHTS["volume_surge_high"]
            signals.append(f"爆量 {c.volume_ratio:.1f}x")
        elif c.volume_ratio >= 2.0:
            total += self.WEIGHTS["volume_surge_mid"]
            signals.append(f"放量 {c.volume_ratio:.1f}x")

        # ── 技術面 ────────────────────────────────────────────────
        if c.above_ma20 and c.above_ma60:
            total += self.WEIGHTS["ma_bullish_full"]
            signals.append("多頭排列 (MA20+MA60)")
        elif c.above_ma20:
            total += self.WEIGHTS["ma_bullish_partial"]
            signals.append("站上MA20")

        if 40 <= c.rsi <= 65:
            total += self.WEIGHTS["rsi_sweet_spot"]
            signals.append(f"RSI健康 {c.rsi:.0f}")
        elif c.rsi > 75:
            total += self.WEIGHTS["rsi_overbought"]
            risks.append(f"RSI超買 {c.rsi:.0f}")

        # ── 融資健康 ─────────────────────────────────────────────
        # 融資大增（>成交量 3%）視為散戶追高
        if c.volume > 0 and c.margin_change > c.volume * 0.03:
            total += self.WEIGHTS["margin_surge"]
            risks.append(f"融資大增 {c.margin_change:+,}張")
        else:
            total += self.WEIGHTS["margin_healthy"]

        # ── 高點爆量收黑（出貨形態）──────────────────────────────
        if c.volume_ratio >= 2.0 and c.change_pct < -1.0 and c.above_ma60:
            total += self.WEIGHTS["top_reversal"]
            risks.append("高點爆量收黑（疑似出貨）")

        c.score = max(0, min(100, total))
        c.signals = signals
        c.risk_flags = risks
        return c


# ═══════════════════════════════════════════════════════════════
# 成交量資料收集
# ═══════════════════════════════════════════════════════════════

class VolumeDataCollector:
    """從 TWSE 取得全市場當日成交量資料"""

    def fetch_daily_all(self, target_date: Optional[str] = None) -> pd.DataFrame:
        """
        取得全市場當日收盤資料（含成交量、開高低收）

        Returns:
            DataFrame，index=stock_id，含 close/change_pct/volume 等欄位
        """
        d = datetime.strptime(target_date, "%Y-%m-%d").date() if target_date else date.today()
        date_str = d.strftime("%Y%m%d")

        url = f"{TWSE_BASE}/afterTrading/MI_INDEX"
        params = {
            "response": "json",
            "date":     date_str,
            "type":     "ALLBUT0999",
        }

        logger.info(f"[成交量] 抓取 {date_str} 全市場")
        try:
            time.sleep(0.4)
            resp = requests.get(url, params=params, headers=_DEFAULT_HEADERS, timeout=15, verify=False)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"[成交量] 請求失敗: {e}")
            return pd.DataFrame()

        if data.get("stat") != "OK":
            logger.warning(f"[成交量] {date_str} 無資料（stat={data.get('stat')}）")
            return pd.DataFrame()

        # 新版 API：資料在 tables 陣列內（舊版用 data9/fields9）
        rows, fields = self._extract_rows(data)
        if not rows:
            logger.warning(f"[成交量] {date_str} 無個股資料")
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=fields)
        df = self._clean(df, date_str)
        logger.info(f"[成交量] {date_str} 共 {len(df)} 支")
        return df

    @staticmethod
    def _extract_rows(data: dict) -> tuple[list, list]:
        """相容新舊版 TWSE API，提取個股收盤資料"""
        # 舊版格式（fields9 / data9）
        if "data9" in data and data["data9"]:
            return data["data9"], data.get("fields9", [])

        # 新版格式（tables 陣列，找 rows > 100 的最大表格）
        tables = data.get("tables", [])
        best = None
        for t in tables:
            rows = t.get("data", [])
            if len(rows) > 100:
                if best is None or len(rows) > len(best.get("data", [])):
                    best = t
        if best:
            # 欄位名稱可能因編碼出現亂碼，改用位置對應
            # 欄位順序：代號,名稱,成交股數,成交筆數,成交金額,開盤,最高,最低,收盤,漲跌方向,漲跌價差,...
            fields = ["stock_id", "stock_name", "volume_shares", "trades",
                      "turnover", "open", "high", "low", "close",
                      "change_dir", "change", "bid_price", "bid_vol",
                      "ask_price", "ask_vol", "pe_ratio"]
            raw_fields = best.get("fields", [])
            # 若欄位數不匹配，補齊或截斷
            n = len(raw_fields)
            if n > len(fields):
                fields += [f"col_{i}" for i in range(len(fields), n)]
            return best["data"], fields[:n]

        return [], []

    def fetch_history_yfinance(
        self, stock_ids: list[str], days: int = 30
    ) -> dict[str, pd.DataFrame]:
        """
        用 yfinance 批量抓歷史 OHLCV（用於計算均量、MA、RSI）

        Returns:
            dict: stock_id → DataFrame
        """
        try:
            import yfinance as yf
        except ImportError:
            logger.warning("yfinance 未安裝，執行: pip install yfinance")
            return {}

        result = {}
        for sid in stock_ids:
            try:
                ticker = yf.Ticker(f"{sid}.TW")
                df = ticker.history(period=f"{days+5}d")
                if not df.empty:
                    df.index = pd.to_datetime(df.index)
                    df.columns = [c.lower() for c in df.columns]
                    result[sid] = df
                time.sleep(0.2)
            except Exception as e:
                logger.debug(f"yfinance {sid} 失敗: {e}")
        return result

    @staticmethod
    def _clean(df: pd.DataFrame, date_str: str) -> pd.DataFrame:
        rename = {
            "證券代號": "stock_id",
            "證券名稱": "stock_name",
            "成交股數": "volume_shares",
            "成交金額": "turnover",
            "開盤價":   "open",
            "最高價":   "high",
            "最低價":   "low",
            "收盤價":   "close",
            "漲跌價差": "change",
            "成交筆數": "trades",
        }
        df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

        for col in ["volume_shares", "turnover", "trades"]:
            if col in df.columns:
                df[col] = (
                    df[col].astype(str)
                    .str.replace(",", "")
                    .apply(pd.to_numeric, errors="coerce")
                    .fillna(0)
                )
        for col in ["open", "high", "low", "close"]:
            if col in df.columns:
                df[col] = pd.to_numeric(
                    df[col].astype(str).str.replace(",", ""), errors="coerce"
                )

        # 張數換算（1張=1000股）
        if "volume_shares" in df.columns:
            df["volume"] = (df["volume_shares"] / 1000).astype(int)

        # 漲跌幅計算（相容舊版 ▲▼ 及新版 HTML 格式）
        if "change" in df.columns and "close" in df.columns:
            change_str = df["change"].astype(str).str.replace(",", "")

            # 判斷方向：新版有 change_dir 欄（HTML），舊版嵌在值裡（▲▼）
            if "change_dir" in df.columns:
                # 新版：change_dir 含 "green" 或 "-" 代表下跌
                is_neg = df["change_dir"].astype(str).str.contains("green|下跌|-", na=False)
            else:
                is_neg = change_str.str.startswith("▼") | change_str.str.startswith("-")
                change_str = change_str.str.replace("▲", "").str.replace("▼", "")

            df["change"] = pd.to_numeric(change_str, errors="coerce").fillna(0)
            df["change"] = df["change"].where(~is_neg, -df["change"])
            df["change_pct"] = (df["change"] / (df["close"] - df["change"]) * 100).round(2)

        df["date"] = date_str
        df["stock_id"] = df["stock_id"].astype(str).str.strip()
        return df.set_index("stock_id")


# ═══════════════════════════════════════════════════════════════
# 主掃描引擎
# ═══════════════════════════════════════════════════════════════

class PostMarketScanner:
    """
    盤後複合條件掃描引擎

    Usage:
        scanner = PostMarketScanner()

        # 執行今日掃描
        result = scanner.scan()

        # 查看候選名單（依評分排序）
        print(result.to_dataframe().to_string())

        # 取前10名
        for c in result.top[:10]:
            print(c.summary())

        # 儲存報告
        scanner.save_report(result, "output/scan_2026-05-07.csv")

    進階：自訂篩選條件
        result = scanner.scan(
            min_volume_ratio=3.0,     # 量比至少3倍
            min_score=50,             # 綜合評分至少50
            inst_buy_only=True,       # 只看法人買超
            exclude_etf=True,         # 排除ETF
        )
    """

    def __init__(
        self,
        finmind_token: Optional[str] = None,
        score_engine:  Optional[ScoreEngine] = None,
    ):
        self.chip_collector  = ChipCollector(finmind_token=finmind_token)
        self.vol_collector   = VolumeDataCollector()
        self.indicator_engine = IndicatorEngine()
        self.scorer          = score_engine or ScoreEngine()

    # ── 主入口 ──────────────────────────────────────────────────

    def scan(
        self,
        target_date:      Optional[str] = None,
        min_volume_ratio: float = 2.0,
        min_score:        int   = 30,
        min_turnover:     int   = 5000,   # 最低成交金額（萬元），過濾流動性差的股票
        inst_buy_only:    bool  = False,  # 只看法人淨買超
        exclude_etf:      bool  = True,   # 排除 ETF（代號有字母前綴）
        top_n:            int   = 50,     # 最多回傳幾支
    ) -> ScanResult:
        """
        執行盤後複合掃描

        Args:
            target_date     : 掃描日期 YYYY-MM-DD（預設今日）
            min_volume_ratio: 最低量比（今日量 / 20日均量）
            min_score       : 最低綜合評分
            min_turnover    : 最低成交金額（萬元）
            inst_buy_only   : 僅顯示三大法人合計買超
            exclude_etf     : 排除 ETF
            top_n           : 最多候選數量
        Returns:
            ScanResult
        """
        scan_date = target_date or date.today().strftime("%Y-%m-%d")
        logger.info(f"🔍 盤後掃描啟動：{scan_date}")

        # Step 1: 抓今日收盤資料
        df_price = self.vol_collector.fetch_daily_all(scan_date)
        if df_price.empty:
            logger.warning("收盤價資料為空，可能是非交易日")
            return ScanResult(scan_date=scan_date, total_stocks=0)

        # Step 2: 抓三大法人 + 融資融券
        df_chip = self.chip_collector.fetch_chip_snapshot(scan_date)

        # Step 3: 合併
        df = self._merge_all(df_price, df_chip)
        total_stocks = len(df)

        # Step 4: 初步篩選（減少 yfinance 請求量）
        df_filtered = self._pre_filter(
            df,
            min_volume_ratio=min_volume_ratio,
            min_turnover=min_turnover,
            inst_buy_only=inst_buy_only,
            exclude_etf=exclude_etf,
        )
        logger.info(
            f"初步篩選後剩 {len(df_filtered)} 支（原 {total_stocks} 支）"
        )

        if df_filtered.empty:
            return ScanResult(
                scan_date=scan_date,
                total_stocks=total_stocks,
                filter_params={
                    "min_volume_ratio": min_volume_ratio,
                    "min_score": min_score,
                },
            )

        # Step 5: 抓歷史資料計算技術指標（均量、MA、RSI）
        candidate_ids = df_filtered.index.tolist()[:200]   # 最多 200 支送 yfinance
        hist_data = self.vol_collector.fetch_history_yfinance(candidate_ids, days=60)

        # Step 6: 建立 ScanCandidate 並計分
        candidates = []
        for sid, row in df_filtered.iterrows():
            c = self._build_candidate(sid, row)
            c = self._enrich_with_history(c, hist_data.get(sid))
            c = self.scorer.score(c)

            if c.score >= min_score:
                candidates.append(c)

        # 依評分排序，取 top_n
        candidates.sort(key=lambda x: x.score, reverse=True)
        candidates = candidates[:top_n]

        result = ScanResult(
            scan_date=scan_date,
            total_stocks=total_stocks,
            candidates=candidates,
            filter_params={
                "min_volume_ratio": min_volume_ratio,
                "min_score": min_score,
                "inst_buy_only": inst_buy_only,
            },
        )

        logger.info(
            f"✅ 掃描完成：{len(candidates)} 支候選（評分 >= {min_score}）"
        )
        return result

    # ── 資料整合 ────────────────────────────────────────────────

    @staticmethod
    def _merge_all(df_price: pd.DataFrame, df_chip: pd.DataFrame) -> pd.DataFrame:
        """合併收盤價 + 籌碼資料"""
        chip_cols = [
            c for c in [
                "stock_name", "foreign_net", "trust_net", "dealer_net",
                "total_net", "margin_balance", "margin_change",
                "short_balance", "short_change",
            ]
            if c in df_chip.columns
        ] if not df_chip.empty else []

        if chip_cols and not df_chip.empty:
            df = df_price.join(df_chip[chip_cols], how="left", rsuffix="_chip")
            # 若 stock_name 有重複欄位，以 price 的為主
            if "stock_name_chip" in df.columns:
                df["stock_name"] = df["stock_name"].fillna(df["stock_name_chip"])
                df.drop(columns=["stock_name_chip"], inplace=True, errors="ignore")
        else:
            df = df_price.copy()

        # 填 NaN
        for col in ["foreign_net", "trust_net", "dealer_net", "total_net",
                    "margin_change", "short_change"]:
            if col not in df.columns:
                df[col] = 0
            else:
                df[col] = df[col].fillna(0).astype(int)

        return df

    @staticmethod
    def _pre_filter(
        df: pd.DataFrame,
        min_volume_ratio: float,
        min_turnover: int,
        inst_buy_only: bool,
        exclude_etf: bool,
    ) -> pd.DataFrame:
        """初步條件過濾，保留有潛力的股票"""
        result = df.copy()

        # 排除 ETF（股票代號非純數字，如 0050, 00878 等以 0 開頭的 4-5 碼代號）
        if exclude_etf:
            result = result[
                result.index.astype(str).str.match(r"^[1-9]\d{3}$")
            ]

        # 成交金額門檻（萬元）
        if "turnover" in result.columns:
            result = result[result["turnover"] / 10000 >= min_turnover]

        # 法人買超篩選
        if inst_buy_only and "total_net" in result.columns:
            result = result[result["total_net"] > 0]

        return result

    @staticmethod
    def _build_candidate(stock_id: str, row: pd.Series) -> ScanCandidate:
        """從合併後的行資料建立 ScanCandidate"""
        return ScanCandidate(
            stock_id=stock_id,
            stock_name=str(row.get("stock_name", "")),
            close=float(row.get("close", 0) or 0),
            change_pct=float(row.get("change_pct", 0) or 0),
            volume=int(row.get("volume", 0) or 0),
            foreign_net=int(row.get("foreign_net", 0) or 0),
            trust_net=int(row.get("trust_net", 0) or 0),
            dealer_net=int(row.get("dealer_net", 0) or 0),
            total_inst=int(row.get("total_net", 0) or 0),
            margin_change=int(row.get("margin_change", 0) or 0),
            short_change=int(row.get("short_change", 0) or 0),
        )

    def _enrich_with_history(
        self,
        c: ScanCandidate,
        hist_df: Optional[pd.DataFrame],
    ) -> ScanCandidate:
        """用歷史資料計算均量、MA、RSI，填入 candidate"""
        if hist_df is None or hist_df.empty:
            return c

        try:
            df = hist_df.copy()

            # 均量（20日）
            if "volume" in df.columns and len(df) >= 5:
                c.volume_avg20 = df["volume"].tail(20).mean()
                if c.volume_avg20 > 0:
                    c.volume_ratio = c.volume / (c.volume_avg20 / 1000)  # yfinance 是股數

            # 技術指標
            df = df.rename(columns={"Volume": "volume", "Close": "close",
                                     "Open": "open", "High": "high", "Low": "low"})
            if len(df) >= 20:
                df = self.indicator_engine.add_sma(df, 20)
                df = self.indicator_engine.add_sma(df, 60)
                df = self.indicator_engine.add_rsi(df, 14)

                last = df.iloc[-1]
                c.above_ma20 = bool(last.get("close", 0) > last.get("SMA_20", float("inf")))
                c.above_ma60 = bool(
                    len(df) >= 60 and
                    last.get("close", 0) > last.get("SMA_60", float("inf"))
                )
                c.rsi = float(last.get("RSI_14", 50) or 50)

        except Exception as e:
            logger.debug(f"[{c.stock_id}] 指標計算失敗: {e}")

        return c

    # ── 報告輸出 ────────────────────────────────────────────────

    def save_report(self, result: ScanResult, path: str) -> None:
        """儲存掃描結果為 CSV"""
        df = result.to_dataframe()
        if df.empty:
            logger.warning("無候選股，不儲存報告")
            return
        df.to_csv(path, encoding="utf-8-sig", index=False)
        logger.info(f"📄 報告已儲存：{path}（{len(df)} 支候選）")

    def print_summary(self, result: ScanResult, top: int = 20) -> None:
        """列印掃描摘要"""
        import sys
        out = sys.stdout
        sep = "=" * 70
        out.write(f"\n{sep}\n")
        out.write(f"  [掃描報告]  {result.scan_date}  |  "
                  f"掃描 {result.total_stocks} 支  |  "
                  f"候選 {len(result.candidates)} 支\n")
        out.write(f"{sep}\n")
        for i, c in enumerate(result.top[:top], 1):
            line = c.summary().encode("utf-8", errors="replace").decode("utf-8")
            out.write(f"  #{i:2d}  {line}\n")
        out.write(f"{sep}\n\n")
        out.flush()


# ═══════════════════════════════════════════════════════════════
# 快速啟動函式
# ═══════════════════════════════════════════════════════════════

def run_post_market_scan(
    date_str:         Optional[str] = None,
    min_score:        int   = 40,
    min_volume_ratio: float = 2.0,
    inst_buy_only:    bool  = True,
    save_csv:         bool  = True,
    finmind_token:    Optional[str] = None,
) -> ScanResult:
    """
    一鍵執行盤後掃描（推薦入口）

    Usage:
        from src.scanner.post_market_scanner import run_post_market_scan

        result = run_post_market_scan(
            min_score=50,
            inst_buy_only=True,   # 只看法人買超股
        )
        for c in result.top[:10]:
            print(c.summary())
    """
    scanner = PostMarketScanner(finmind_token=finmind_token)
    result  = scanner.scan(
        target_date=date_str,
        min_volume_ratio=min_volume_ratio,
        min_score=min_score,
        inst_buy_only=inst_buy_only,
    )
    scanner.print_summary(result)

    if save_csv and result.candidates:
        from pathlib import Path
        output_dir = Path("data/scan_reports")
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"scan_{result.scan_date}.csv"
        scanner.save_report(result, str(path))

    return result
