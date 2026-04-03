"""
資金費率收集器 (Funding Rate Collector)

透過 Binance 公開 API 抓取永續合約 (Perpetual Futures) 的資金費率歷史資料。
一般每 8 小時結算一次，但現在有的市場可能是 4 小時。
"""

import datetime as dt
from typing import Optional

import ccxt
import pandas as pd
from loguru import logger


class FundingRateCollector:
    """幣安永續合約資金費率收集器"""

    def __init__(self, exchange_id: str = "binance"):
        self.exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})

    def get_historical(
        self,
        symbol: str = "ETH/USDT:USDT",
        start_date: str = "2020-01-01",
        end_date: str = "2026-02-28",
        limit: int = 1000
    ) -> pd.DataFrame:
        """
        分頁抓取歷史資金費率
        注意: ccxt 需使用期貨交易對格式 (如 'ETH/USDT:USDT')
        """
        logger.info(f"📡 拉取 {symbol} 歷史資金費率...")
        
        # ccxt 如果不支援 fetch_funding_rate_history，我們可以調用隱含 API 或 requests
        # ccxt 最新版支援 fetch_funding_rate_history
        since = int(dt.datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)
        end_ts = int(dt.datetime.strptime(end_date, "%Y-%m-%d").timestamp() * 1000)

        all_data = []
        page = 0

        while since < end_ts:
            page += 1
            try:
                # CCXT Unified API
                if not self.exchange.has['fetchFundingRateHistory']:
                    logger.error(f"Exchange {self.exchange.id} does not support fetchFundingRateHistory")
                    break

                rates = self.exchange.fetch_funding_rate_history(symbol, since=since, limit=limit)
                
                if not rates:
                    break

                all_data.extend(rates)
                last_ts = rates[-1]['timestamp']

                if last_ts <= since:
                    break

                since = last_ts + 1  # 避免重複抓取最後一筆

                bars_so_far = len(all_data)
                last_date = dt.datetime.fromtimestamp(last_ts / 1000).strftime("%Y-%m-%d %H:%M")
                if page % 5 == 0:
                    logger.info(f"  📥 Funding Rate Page {page}: {bars_so_far} records, last={last_date}")

            except Exception as e:
                logger.error(f"  ⚠️ Error fetching funding rate at page {page}: {e}")
                import time
                time.sleep(2)
                # Fallback to direct requests if CCXT fails
                break

        if not all_data:
            # Fallback Binance requests GET https://fapi.binance.com/fapi/v1/fundingRate
            logger.warning("Attempting fallback to Binance fapi direct requests...")
            return self._fallback_binance_fapi(symbol.split(":")[0].replace("/", ""), start_date, end_date)

        records = []
        for r in all_data:
            records.append({
                "timestamp": dt.datetime.fromtimestamp(r["timestamp"] / 1000),
                "funding_rate": float(r["fundingRate"])
            })

        df = pd.DataFrame(records)
        df = df.sort_values("timestamp").reset_index(drop=True)
        logger.info(f"✅ Funding Rate: {len(df)} records ({df['timestamp'].iloc[0]} ~ {df['timestamp'].iloc[-1]})")
        return df

    def _fallback_binance_fapi(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        """如果有 ccxt 版本問題，直接使用 requests 抓 Binance fapi"""
        import requests
        import time
        url = "https://fapi.binance.com/fapi/v1/fundingRate"
        
        since = int(dt.datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)
        end_ts = int(dt.datetime.strptime(end_date, "%Y-%m-%d").timestamp() * 1000)
        
        all_data = []
        page = 0
        
        while since < end_ts:
            page += 1
            try:
                params = {
                    "symbol": symbol,
                    "startTime": since,
                    "limit": 1000
                }
                resp = requests.get(url, params=params, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                
                if not data:
                    break
                    
                all_data.extend(data)
                last_ts = data[-1]["fundingTime"]
                
                if last_ts <= since:
                    break
                    
                since = last_ts + 1
                time.sleep(0.5) # Rate limit protection
                
            except Exception as e:
                logger.error(f"Fallback API error: {e}")
                break
                
        if not all_data:
            return pd.DataFrame()
            
        records = []
        for r in all_data:
            records.append({
                "timestamp": dt.datetime.fromtimestamp(r["fundingTime"] / 1000),
                "funding_rate": float(r["fundingRate"])
            })

        df = pd.DataFrame(records)
        df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
        logger.info(f"✅ Fallback Funding Rate: {len(df)} records fetched")
        return df
