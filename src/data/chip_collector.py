"""
籌碼面資料收集器 (Chip / Institutional Data Collector)

負責從 TWSE / TPEx / FinMind 收集法人籌碼資料：
- ThreeInstitutionCollector : 三大法人（外資、投信、自營商）買賣超
- MarginTradingCollector    : 融資融券餘額變化
- ChipCollector             : 整合入口，提供統一介面

資料來源（優先順序）:
  1. TWSE OpenAPI  — 官方免費，速率限制 3 req/5s
  2. FinMind API   — 功能完整，免費 600 req/day（需 token）
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Optional

import urllib3
import pandas as pd
import requests
from loguru import logger

# 關閉 SSL 警告（TWSE 憑證問題）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─────────────────────────────────────────────
# 常數
# ─────────────────────────────────────────────
TWSE_BASE = "https://www.twse.com.tw/rwd/zh"
TPEX_BASE = "https://www.tpex.org.tw/web/stock"
FINMIND_BASE = "https://api.finmindtrade.com/api/v4/data"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://www.twse.com.tw/",
}


def _twse_get(url: str, params: dict, retry: int = 3) -> Optional[dict]:
    """帶重試的 TWSE GET 請求（自動限速）"""
    for attempt in range(retry):
        try:
            time.sleep(0.4)          # 遵守 3 req/5s 限制
            resp = requests.get(url, params=params, headers=_DEFAULT_HEADERS, timeout=15, verify=False)
            resp.raise_for_status()
            data = resp.json()
            if data.get("stat") == "OK":
                return data
            logger.warning(f"TWSE stat not OK: {data.get('stat')} | {url}")
            return None
        except requests.exceptions.RequestException as e:
            logger.warning(f"TWSE request attempt {attempt+1} failed: {e}")
            time.sleep(2 ** attempt)
    return None


# ═══════════════════════════════════════════════════════════════
# 三大法人收集器
# ═══════════════════════════════════════════════════════════════

class ThreeInstitutionCollector:
    """
    三大法人買賣超資料
    ─────────────────
    外資（Foreign Investors）   : 含外資自營商
    投信（Investment Trust）    : 國內投信基金
    自營商（Dealers）            : 證券商自行操盤

    欄位說明：
        foreign_buy     外資買進張數
        foreign_sell    外資賣出張數
        foreign_net     外資買賣超 (買 - 賣)，正數=買超
        trust_buy       投信買進張數
        trust_sell      投信賣出張數
        trust_net       投信買賣超
        dealer_net      自營商買賣超
        total_net       三大法人合計買賣超
    """

    # ── TWSE 上市 ──────────────────────────────────────────────

    def fetch_twse_daily(self, target_date: Optional[str] = None) -> pd.DataFrame:
        """
        取得上市股票當日三大法人買賣超（全市場）

        Args:
            target_date: 日期字串 YYYY-MM-DD，預設今日
        Returns:
            DataFrame，index=股票代號
        """
        d = self._resolve_date(target_date)
        date_str = d.strftime("%Y%m%d")

        url = f"{TWSE_BASE}/fund/T86"
        params = {
            "response": "json",
            "date": date_str,
            "selectType": "ALLBUT0999",   # 全部上市股票
        }

        logger.info(f"[三大法人-上市] 抓取 {date_str}")
        data = _twse_get(url, params)
        if not data:
            return pd.DataFrame()

        fields = data.get("fields", [])
        rows   = data.get("data", [])
        if not rows:
            logger.warning(f"[三大法人-上市] {date_str} 無資料（非交易日？）")
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=fields)
        df = self._clean_twse_chip(df, date_str)
        logger.info(f"[三大法人-上市] {date_str} 共 {len(df)} 支")
        return df

    def fetch_twse_stock(
        self,
        stock_id: str,
        start_date: str,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        取得單一上市股票的三大法人歷史資料

        Args:
            stock_id  : 股票代號（e.g. "2330"）
            start_date: 起始日期 YYYY-MM-DD
            end_date  : 結束日期 YYYY-MM-DD（預設今日）
        """
        end = self._resolve_date(end_date)
        start = datetime.strptime(start_date, "%Y-%m-%d").date()

        all_frames = []
        current = start

        while current <= end:
            date_str = current.strftime("%Y%m%d")
            url = f"{TWSE_BASE}/fund/BHCSMART"
            params = {
                "response": "json",
                "date": date_str,
                "stockNo": stock_id,
            }
            data = _twse_get(url, params)
            if data:
                fields = data.get("fields", [])
                rows   = data.get("data", [])
                if rows:
                    df_month = pd.DataFrame(rows, columns=fields)
                    all_frames.append(df_month)

            # 逐月跳轉
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1, day=1)
            else:
                current = current.replace(month=current.month + 1, day=1)

        if not all_frames:
            return pd.DataFrame()

        df = pd.concat(all_frames, ignore_index=True)
        return self._clean_twse_chip_single(df, stock_id)

    # ── 整理欄位 ────────────────────────────────────────────────

    @staticmethod
    def _resolve_date(d: Optional[str]) -> date:
        if d is None:
            return date.today()
        return datetime.strptime(d, "%Y-%m-%d").date()

    @staticmethod
    def _to_int(series: pd.Series) -> pd.Series:
        return (
            series.astype(str)
            .str.replace(",", "", regex=False)
            .str.replace(" ", "", regex=False)
            .apply(pd.to_numeric, errors="coerce")
            .fillna(0)
            .astype(int)
        )

    def _clean_twse_chip(self, df: pd.DataFrame, date_str: str) -> pd.DataFrame:
        """整理全市場三大法人欄位"""
        # TWSE T86 欄位（中文）→ 標準化
        rename_map = {
            "證券代號": "stock_id",
            "證券名稱": "stock_name",
            # 新版 T86 欄位名稱（2024+ API 格式）
            "外陸資買進股數(不含外資自營商)": "foreign_buy",
            "外陸資賣出股數(不含外資自營商)": "foreign_sell",
            "外陸資買賣超股數(不含外資自營商)": "foreign_net_ex",   # 外資不含自營商
            "外資自營商買進股數": "foreign_dealer_buy",
            "外資自營商賣出股數": "foreign_dealer_sell",
            "外資自營商買賣超股數": "foreign_dealer_net",
            # 舊版 T86 欄位名稱（相容）
            "外陸資買進股數": "foreign_buy",
            "外陸資賣出股數": "foreign_sell",
            "外陸資買賣超股數": "foreign_net",
            # 投信
            "投信買進股數": "trust_buy",
            "投信賣出股數": "trust_sell",
            "投信買賣超股數": "trust_net",
            # 自營商
            "自營商買賣超股數": "dealer_net",          # 合計（新版直接有此欄）
            "自營商買進股數(自行買賣)": "dealer_self_buy",
            "自營商賣出股數(自行買賣)": "dealer_self_sell",
            "自營商買賣超股數(自行買賣)": "dealer_self_net",
            "自營商買進股數(避險)": "dealer_hedge_buy",
            "自營商賣出股數(避險)": "dealer_hedge_sell",
            "自營商買賣超股數(避險)": "dealer_hedge_net",
            # 合計
            "三大法人買賣超股數": "total_net",
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

        # 先轉換所有數值欄位為 int
        int_cols = [
            "foreign_buy", "foreign_sell", "foreign_net", "foreign_net_ex",
            "foreign_dealer_net",
            "trust_buy", "trust_sell", "trust_net",
            "dealer_net", "dealer_self_net", "dealer_hedge_net", "total_net",
        ]
        for col in int_cols:
            if col in df.columns:
                df[col] = self._to_int(df[col])

        # 外資合計 = 外陸資(不含自營商) + 外資自營商
        # 新版 API 才有 foreign_net_ex；舊版直接有 foreign_net
        if "foreign_net" not in df.columns or df["foreign_net"].eq(0).all():
            ex  = df["foreign_net_ex"].values  if "foreign_net_ex"     in df.columns else 0
            dlr = df["foreign_dealer_net"].values if "foreign_dealer_net" in df.columns else 0
            df["foreign_net"] = ex + dlr

        # 自營商合計（若新版 API 已有 dealer_net 欄位則直接用；否則計算）
        if "dealer_net" not in df.columns:
            if "dealer_self_net" in df.columns and "dealer_hedge_net" in df.columns:
                df["dealer_net"] = df["dealer_self_net"] + df["dealer_hedge_net"]
            elif "dealer_self_net" in df.columns:
                df["dealer_net"] = df["dealer_self_net"]

        df["date"] = date_str
        df["stock_id"] = df["stock_id"].astype(str).str.strip()

        # 張數換算（股數 → 張，1張=1000股）
        for col in ["foreign_buy", "foreign_sell", "foreign_net",
                    "trust_buy", "trust_sell", "trust_net",
                    "dealer_net", "total_net"]:
            if col in df.columns:
                df[col] = (df[col] / 1000).astype(int)

        return df.set_index("stock_id")

    def _clean_twse_chip_single(self, df: pd.DataFrame, stock_id: str) -> pd.DataFrame:
        """整理單一股票三大法人欄位（月報格式）"""
        # 簡化處理，欄位視實際 API 回傳調整
        df["stock_id"] = stock_id
        return df


# ═══════════════════════════════════════════════════════════════
# 融資融券收集器
# ═══════════════════════════════════════════════════════════════

class MarginTradingCollector:
    """
    融資融券餘額資料
    ─────────────────
    融資（Margin）  : 借錢買股，餘額↑代表散戶積極做多
    融券（Short）   : 借股放空，餘額↑代表市場悲觀
    融資使用率      : 融資餘額 / 融資限額，高代表市場過熱

    欄位說明：
        margin_buy      融資買進（張）
        margin_sell     融資賣出（張）
        margin_balance  融資餘額（張）
        margin_change   融資餘額變化（今日 - 昨日）
        short_buy       融券買進（張，空單回補）
        short_sell      融券賣出（張，新增空單）
        short_balance   融券餘額（張）
        short_change    融券餘額變化
        offset          資券相抵（張）
    """

    def fetch_twse_daily(self, target_date: Optional[str] = None) -> pd.DataFrame:
        """
        取得上市股票當日融資融券（全市場）

        Args:
            target_date: 日期字串 YYYY-MM-DD，預設今日
        """
        d = self._resolve_date(target_date)
        date_str = d.strftime("%Y%m%d")

        url = f"{TWSE_BASE}/marginTrading/MI_MARGN"
        params = {
            "response": "json",
            "date": date_str,
            "selectType": "ALL",
        }

        logger.info(f"[融資融券-上市] 抓取 {date_str}")
        data = _twse_get(url, params)
        if not data:
            return pd.DataFrame()

        # MI_MARGN 有 table1（融資）和 table2（融券）兩組欄位
        fields1 = data.get("fields1", [])
        rows1   = data.get("data1", [])
        fields2 = data.get("fields2", [])
        rows2   = data.get("data2", [])

        if not rows1:
            logger.warning(f"[融資融券-上市] {date_str} 無資料")
            return pd.DataFrame()

        df1 = pd.DataFrame(rows1, columns=fields1) if rows1 else pd.DataFrame()
        df2 = pd.DataFrame(rows2, columns=fields2) if rows2 else pd.DataFrame()

        df = self._merge_margin_data(df1, df2, date_str)
        logger.info(f"[融資融券-上市] {date_str} 共 {len(df)} 支")
        return df

    def fetch_twse_stock(
        self,
        stock_id: str,
        start_date: str,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """取得單一上市股票融資融券歷史資料（月報）"""
        end = self._resolve_date(end_date)
        start = datetime.strptime(start_date, "%Y-%m-%d").date()

        all_frames = []
        current = start

        while current <= end:
            date_str = current.strftime("%Y%m%d")
            url = f"{TWSE_BASE}/marginTrading/STOCK_DAY_ALL"
            params = {
                "response": "json",
                "date": date_str,
                "stockNo": stock_id,
            }
            data = _twse_get(url, params)
            if data and data.get("data"):
                fields = data.get("fields", [])
                rows   = data.get("data", [])
                df_month = pd.DataFrame(rows, columns=fields)
                df_month["stock_id"] = stock_id
                all_frames.append(df_month)

            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1, day=1)
            else:
                current = current.replace(month=current.month + 1, day=1)

        return pd.concat(all_frames, ignore_index=True) if all_frames else pd.DataFrame()

    # ── 整理欄位 ────────────────────────────────────────────────

    @staticmethod
    def _resolve_date(d: Optional[str]) -> date:
        if d is None:
            return date.today()
        return datetime.strptime(d, "%Y-%m-%d").date()

    @staticmethod
    def _to_int(series: pd.Series) -> pd.Series:
        return (
            series.astype(str)
            .str.replace(",", "", regex=False)
            .str.replace(" ", "", regex=False)
            .apply(pd.to_numeric, errors="coerce")
            .fillna(0)
            .astype(int)
        )

    def _merge_margin_data(
        self,
        df1: pd.DataFrame,
        df2: pd.DataFrame,
        date_str: str,
    ) -> pd.DataFrame:
        """合併融資（df1）和融券（df2）資料"""
        rename1 = {
            "股票代號": "stock_id",
            "股票名稱": "stock_name",
            "融資買進": "margin_buy",
            "融資賣出": "margin_sell",
            "現金償還": "margin_cash_repay",
            "前日餘額": "margin_prev_balance",
            "今日餘額": "margin_balance",
            "限額": "margin_limit",
        }
        rename2 = {
            "股票代號": "stock_id",
            "融券賣出": "short_sell",
            "融券買進": "short_buy",
            "現券償還": "short_stock_repay",
            "前日餘額": "short_prev_balance",
            "今日餘額": "short_balance",
            "限額": "short_limit",
            "資券相抵": "offset",
        }

        df1 = df1.rename(columns={k: v for k, v in rename1.items() if k in df1.columns})
        df2 = df2.rename(columns={k: v for k, v in rename2.items() if k in df2.columns})

        int_cols1 = ["margin_buy", "margin_sell", "margin_prev_balance",
                     "margin_balance", "margin_limit"]
        int_cols2 = ["short_sell", "short_buy", "short_prev_balance",
                     "short_balance", "short_limit", "offset"]

        for col in int_cols1:
            if col in df1.columns:
                df1[col] = self._to_int(df1[col])
        for col in int_cols2:
            if col in df2.columns:
                df2[col] = self._to_int(df2[col])

        if "stock_id" in df1.columns and "stock_id" in df2.columns:
            df1["stock_id"] = df1["stock_id"].astype(str).str.strip()
            df2["stock_id"] = df2["stock_id"].astype(str).str.strip()
            df = df1.merge(
                df2[["stock_id"] + [c for c in int_cols2 if c in df2.columns]],
                on="stock_id",
                how="left",
            )
        else:
            df = df1

        # 計算當日變化量
        if "margin_balance" in df.columns and "margin_prev_balance" in df.columns:
            df["margin_change"] = df["margin_balance"] - df["margin_prev_balance"]
        if "short_balance" in df.columns and "short_prev_balance" in df.columns:
            df["short_change"] = df["short_balance"] - df["short_prev_balance"]

        # 融資使用率（%）
        if "margin_balance" in df.columns and "margin_limit" in df.columns:
            df["margin_util_pct"] = (
                df["margin_balance"] / df["margin_limit"].replace(0, float("nan")) * 100
            ).round(2)

        df["date"] = date_str
        return df.set_index("stock_id") if "stock_id" in df.columns else df


# ═══════════════════════════════════════════════════════════════
# 整合入口
# ═══════════════════════════════════════════════════════════════

class ChipCollector:
    """
    籌碼面資料整合入口

    Usage:
        collector = ChipCollector()

        # 今日全市場三大法人
        df_chip = collector.fetch_institutional_today()

        # 今日融資融券
        df_margin = collector.fetch_margin_today()

        # 整合籌碼快照（三大法人 + 融資融券合併）
        df_all = collector.fetch_chip_snapshot()

        # 單一股票歷史籌碼（30日）
        df_hist = collector.fetch_stock_chip_history("2330", days=30)
    """

    def __init__(self, finmind_token: Optional[str] = None):
        """
        Args:
            finmind_token: FinMind API Token（可選，提升請求上限）
        """
        self.three_inst = ThreeInstitutionCollector()
        self.margin     = MarginTradingCollector()
        self.finmind_token = finmind_token

    # ── 今日快照 ────────────────────────────────────────────────

    def fetch_institutional_today(
        self, target_date: Optional[str] = None
    ) -> pd.DataFrame:
        """取得今日三大法人買賣超（全市場）"""
        return self.three_inst.fetch_twse_daily(target_date)

    def fetch_margin_today(
        self, target_date: Optional[str] = None
    ) -> pd.DataFrame:
        """取得今日融資融券（全市場）"""
        return self.margin.fetch_twse_daily(target_date)

    def fetch_chip_snapshot(
        self, target_date: Optional[str] = None
    ) -> pd.DataFrame:
        """
        整合三大法人 + 融資融券，回傳完整籌碼快照

        Returns:
            DataFrame，欄位包含法人買賣超 + 融資融券餘額
        """
        df_inst   = self.fetch_institutional_today(target_date)
        df_margin = self.fetch_margin_today(target_date)

        if df_inst.empty:
            logger.warning("三大法人資料為空，請確認是否為交易日")
            return pd.DataFrame()

        if not df_margin.empty:
            # 合併，保留所有法人股票
            margin_cols = [
                c for c in [
                    "margin_balance", "margin_change", "margin_util_pct",
                    "short_balance", "short_change", "offset",
                ]
                if c in df_margin.columns
            ]
            df = df_inst.join(df_margin[margin_cols], how="left")
        else:
            df = df_inst

        logger.info(f"籌碼快照完成，共 {len(df)} 支個股")
        return df

    # ── 歷史籌碼 ────────────────────────────────────────────────

    def fetch_stock_chip_history(
        self,
        stock_id: str,
        days: int = 30,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        取得單一股票歷史法人籌碼（N日）

        Args:
            stock_id : 股票代號
            days     : 往回幾個交易日
            end_date : 結束日期（預設今日）
        Returns:
            DataFrame，含三大法人歷史買賣超
        """
        end  = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else date.today()
        start = (end - timedelta(days=days + 10)).strftime("%Y-%m-%d")  # 多抓一些以涵蓋非交易日
        end_str = end.strftime("%Y-%m-%d")

        logger.info(f"抓取 {stock_id} 歷史籌碼 ({start} ~ {end_str})")
        return self.three_inst.fetch_twse_stock(stock_id, start, end_str)

    # ── FinMind 備援 ────────────────────────────────────────────

    def fetch_via_finmind(
        self,
        dataset: str,
        stock_id: str,
        start_date: str,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        使用 FinMind API 取得資料（備援方案，需 token）

        常用 dataset：
            TaiwanStockInstitutionalInvestorsBuySell  三大法人
            TaiwanStockMarginPurchaseShortSale         融資融券

        Args:
            dataset   : FinMind 資料集名稱
            stock_id  : 股票代號
            start_date: 起始日期 YYYY-MM-DD
        """
        if not self.finmind_token:
            logger.warning("FinMind token 未設定，跳過")
            return pd.DataFrame()

        params = {
            "dataset":    dataset,
            "data_id":    stock_id,
            "start_date": start_date,
            "token":      self.finmind_token,
        }
        if end_date:
            params["end_date"] = end_date

        try:
            resp = requests.get(FINMIND_BASE, params=params, timeout=20)
            resp.raise_for_status()
            result = resp.json()
            if result.get("status") == 200:
                df = pd.DataFrame(result["data"])
                logger.info(f"[FinMind] {dataset} {stock_id} 共 {len(df)} 筆")
                return df
            else:
                logger.warning(f"[FinMind] {result.get('msg', 'unknown error')}")
                return pd.DataFrame()
        except Exception as e:
            logger.error(f"[FinMind] 請求失敗: {e}")
            return pd.DataFrame()
