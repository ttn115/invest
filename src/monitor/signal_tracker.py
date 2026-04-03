"""
信號績效追蹤器 (Signal Performance Tracker)

每次掃描時：
1. 記錄所有 BUY/SELL 信號到 CSV
2. 回查之前信號的後續表現
3. 計算系統的歷史勝率
"""

import os
import csv
import datetime as dt
from pathlib import Path
from loguru import logger


HISTORY_FILE = Path(__file__).parent.parent.parent / "data" / "signal_history.csv"
HEADERS = [
    "timestamp", "symbol", "signal", "price_at_signal", "price_twd",
    "confidence", "rsi", "sentiment",
    # SOL Phase 1: 市場背景標籤 (Context Tags)
    "ctx_phase", "ctx_season", "ctx_mtf_score", "ctx_fg_trend", "ctx_dxy_trend",
    "price_after_1h", "price_after_4h", "price_after_24h",
    "pnl_1h_pct", "pnl_4h_pct", "pnl_24h_pct", "verified"
]


class SignalTracker:
    """追蹤信號歷史績效"""

    def __init__(self, history_file: str = None):
        self.history_file = Path(history_file) if history_file else HISTORY_FILE
        self._ensure_file()

    def _ensure_file(self):
        """確保 CSV 檔案和目錄存在，且欄位與最新 HEADERS 一致"""
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.history_file.exists():
            with open(self.history_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(HEADERS)
            logger.info(f"📝 Created signal history file: {self.history_file}")
        else:
            # 向下相容：如果舊 CSV 缺少 ctx_ 欄位，自動遷移
            self._migrate_if_needed()

    def _migrate_if_needed(self):
        """檢查舊 CSV 是否缺少 ctx_ 欄位，若是則補齊"""
        with open(self.history_file, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            existing_headers = next(reader, [])
        if "ctx_phase" not in existing_headers:
            logger.info("🔄 Migrating signal_history.csv → adding context columns...")
            rows = []
            with open(self.history_file, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    for col in ["ctx_phase", "ctx_season", "ctx_mtf_score", "ctx_fg_trend", "ctx_dxy_trend"]:
                        row.setdefault(col, "")
                    rows.append(row)
            with open(self.history_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=HEADERS)
                writer.writeheader()
                writer.writerows(rows)
            logger.info(f"✅ Migration complete: {len(rows)} rows updated")

    def _has_recent_signal(self, symbol: str, signal: str, hours: int = 4) -> bool:
        """檢查是否已有近期相同方向的信號（去重用）"""
        if not self.history_file.exists():
            return False
        cutoff = dt.datetime.now() - dt.timedelta(hours=hours)
        with open(self.history_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row["symbol"] == symbol and row["signal"] == signal:
                    try:
                        ts = dt.datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
                        if ts >= cutoff:
                            return True
                    except ValueError:
                        continue
        return False

    def record_signal(self, symbol: str, signal: str, price: float,
                      price_twd: str, confidence: str, rsi: str,
                      sentiment: float = 50.0,
                      market_ctx=None):
        """
        記錄一筆新信號（含去重檢查 + 市場背景標籤）
        
        Args:
            market_ctx: MarketContext 物件 (SOL Phase 1: 背景標籤化)
        """
        # 去重：同一標的同方向 4 小時內不重複記錄
        if self._has_recent_signal(symbol, signal, hours=4):
            logger.debug(f"⏭️ Skipped duplicate {signal} for {symbol} (recent signal exists)")
            return False

        # SOL Phase 1: 提取市場背景標籤
        ctx_phase = ""
        ctx_season = ""
        ctx_mtf_score = ""
        ctx_fg_trend = ""
        ctx_dxy_trend = ""
        if market_ctx:
            ctx_phase = getattr(market_ctx, 'phase', '')
            ctx_season = getattr(market_ctx, 'season', '')
            ctx_mtf_score = str(getattr(market_ctx, 'mtf_score', ''))
            ctx_fg_trend = getattr(market_ctx, 'fg_3d_trend', '')
            ctx_dxy_trend = getattr(market_ctx, 'dxy_trend', '')

        row = [
            dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            symbol, signal, f"{price:.6f}", price_twd,
            confidence, rsi, f"{sentiment:.1f}",
            ctx_phase, ctx_season, ctx_mtf_score, ctx_fg_trend, ctx_dxy_trend,
            "", "", "",       # price_after fields (to be filled later)
            "", "", "",       # pnl fields (to be filled later)
            "false"           # verified flag
        ]
        with open(self.history_file, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(row)
        logger.debug(f"📝 Recorded {signal} for {symbol} @ {price:.4f} [ctx: {ctx_phase}/{ctx_season}]")
        return True

    def record_signals_from_report(self, report_df, fg_val: float, market_ctx=None):
        """從掃描報告中記錄所有 BUY/SELL 信號（含去重 + 背景標籤）"""
        actionable = report_df[report_df["Signal"].isin(["BUY", "SELL"])]
        recorded = 0
        skipped = 0
        for _, row in actionable.iterrows():
            was_recorded = self.record_signal(
                symbol=row["Symbol"],
                signal=row["Signal"],
                price=row["Price"],
                price_twd=row["Price(TWD)"],
                confidence=row["Confidence"],
                rsi=row["RSI"],
                sentiment=fg_val,
                market_ctx=market_ctx,
            )
            if was_recorded:
                recorded += 1
            else:
                skipped += 1
        if recorded or skipped:
            logger.info(f"📝 Signals: {recorded} recorded, {skipped} skipped (dedup)")
        return recorded

    def verify_past_signals(self, exchange, hours_ago: int = 1):
        """
        回查過去的未驗證信號，填入後續價格。
        
        Args:
            exchange: ccxt exchange instance (可為 None，會跳過驗證)
            hours_ago: 要回查多少小時前的信號
        """
        if exchange is None:
            return
        if not self.history_file.exists():
            return

        rows = []
        updated = 0
        with open(self.history_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)

        now = dt.datetime.now()
        for row in rows:
            if row["verified"] == "true":
                continue

            signal_time = dt.datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
            hours_elapsed = (now - signal_time).total_seconds() / 3600
            price_at_signal = float(row["price_at_signal"])

            if price_at_signal == 0:
                continue

            symbol = row["symbol"]

            # 嘗試獲取當前價格來更新
            try:
                ticker = exchange.fetch_ticker(symbol)
                current_price = float(ticker["last"])
            except Exception:
                continue

            # 根據經過時間填入對應欄位
            if hours_elapsed >= 1 and not row["price_after_1h"]:
                row["price_after_1h"] = f"{current_price:.6f}"
                pnl = (current_price - price_at_signal) / price_at_signal
                if row["signal"] == "SELL":
                    pnl = -pnl  # SELL 信號的獲利邏輯相反
                row["pnl_1h_pct"] = f"{pnl:.4f}"
                updated += 1

            if hours_elapsed >= 4 and not row["price_after_4h"]:
                row["price_after_4h"] = f"{current_price:.6f}"
                pnl = (current_price - price_at_signal) / price_at_signal
                if row["signal"] == "SELL":
                    pnl = -pnl
                row["pnl_4h_pct"] = f"{pnl:.4f}"
                updated += 1

            if hours_elapsed >= 24 and not row["price_after_24h"]:
                row["price_after_24h"] = f"{current_price:.6f}"
                pnl = (current_price - price_at_signal) / price_at_signal
                if row["signal"] == "SELL":
                    pnl = -pnl
                row["pnl_24h_pct"] = f"{pnl:.4f}"
                row["verified"] = "true"
                updated += 1

        if updated:
            with open(self.history_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=HEADERS)
                writer.writeheader()
                writer.writerows(rows)
            logger.info(f"✅ Updated {updated} historical signal entries")

    def get_performance_stats(self) -> dict:
        """
        計算完整的營利統計。
        
        Returns:
            dict with comprehensive profitability metrics:
            - win_rate: 勝率 (%)
            - total: 有效信號總數
            - wins / losses: 勝敗次數
            - total_pnl_pct: 累計報酬率 (%)
            - avg_pnl_pct: 平均每筆報酬率 (%)
            - max_gain_pct: 最大單筆獲利 (%)
            - max_loss_pct: 最大單筆虧損 (%)
            - profit_factor: 獲利因子 (總獲利/總虧損，>1 就是賺)
            - best_symbol / worst_symbol: 最佳/最差標的
            - avg_confidence_win / avg_confidence_loss: 勝/敗的平均信心度
        """
        empty = {
            "win_rate": 0, "total": 0, "wins": 0, "losses": 0,
            "total_pnl_pct": 0, "avg_pnl_pct": 0,
            "max_gain_pct": 0, "max_loss_pct": 0,
            "profit_factor": 0,
            "best_symbol": "N/A", "worst_symbol": "N/A",
            "avg_confidence_win": 0, "avg_confidence_loss": 0,
        }
        if not self.history_file.exists():
            return empty

        pnl_list = []        # 所有 PnL 數據
        symbol_pnl = {}      # 按幣種累計
        win_confidences = []
        loss_confidences = []
        total_gain = 0.0
        total_loss = 0.0

        with open(self.history_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # 統一使用 1h PnL 作為基準（避免混用不同時框造成統計失真）
                # 1h 代表信號品質的即時反應，更能反映進場時機準確性
                pnl_str = row.get("pnl_1h_pct", "")
                if not pnl_str:
                    continue

                pnl = float(pnl_str)
                pnl_list.append(pnl)
                symbol = row["symbol"]
                confidence = float(row.get("confidence", "0") or "0")

                # 按幣種累計
                symbol_pnl[symbol] = symbol_pnl.get(symbol, 0) + pnl

                if pnl > 0:
                    total_gain += pnl
                    win_confidences.append(confidence)
                else:
                    total_loss += abs(pnl)
                    loss_confidences.append(confidence)

        if not pnl_list:
            return empty

        wins = sum(1 for p in pnl_list if p > 0)
        losses = len(pnl_list) - wins
        total_pnl = sum(pnl_list)
        avg_pnl = total_pnl / len(pnl_list)
        max_gain = max(pnl_list)
        max_loss = min(pnl_list)

        # 獲利因子：總獲利 / 總虧損 (> 1 代表策略整體獲利)
        profit_factor = (total_gain / total_loss) if total_loss > 0 else float("inf")

        # 最佳/最差幣種
        best_sym = max(symbol_pnl, key=symbol_pnl.get) if symbol_pnl else "N/A"
        worst_sym = min(symbol_pnl, key=symbol_pnl.get) if symbol_pnl else "N/A"

        return {
            "win_rate": round(wins / len(pnl_list) * 100, 1),
            "total": len(pnl_list),
            "wins": wins,
            "losses": losses,
            "total_pnl_pct": round(total_pnl * 100, 2),
            "avg_pnl_pct": round(avg_pnl * 100, 2),
            "max_gain_pct": round(max_gain * 100, 2),
            "max_loss_pct": round(max_loss * 100, 2),
            "profit_factor": round(profit_factor, 2),
            "best_symbol": f"{best_sym} ({symbol_pnl.get(best_sym, 0)*100:+.2f}%)",
            "worst_symbol": f"{worst_sym} ({symbol_pnl.get(worst_sym, 0)*100:+.2f}%)",
            "avg_confidence_win": round(sum(win_confidences) / len(win_confidences), 2) if win_confidences else 0,
            "avg_confidence_loss": round(sum(loss_confidences) / len(loss_confidences), 2) if loss_confidences else 0,
        }

    def get_summary_text(self) -> str:
        """產生完整績效摘要文字（含營利統計）"""
        stats = self.get_performance_stats()
        if stats["total"] == 0:
            return "📈 尚無歷史績效數據 (信號追蹤已啟用，資料將在未來數小時內累積)"
        
        pnl_emoji = "💰" if stats["total_pnl_pct"] >= 0 else "📉"
        pf_emoji = "🟢" if stats["profit_factor"] >= 1.0 else "🔴"
        
        lines = [
            f"{pnl_emoji} *累計報酬：{stats['total_pnl_pct']:+.2f}%* | "
            f"平均每筆：{stats['avg_pnl_pct']:+.2f}%",
            f"{pf_emoji} 獲利因子：{stats['profit_factor']} "
            f"(勝率 {stats['win_rate']}% | {stats['wins']}勝 {stats['losses']}敗 / 共{stats['total']}筆)",
            f"📊 最大獲利：{stats['max_gain_pct']:+.2f}% | "
            f"最大虧損：{stats['max_loss_pct']:+.2f}%",
            f"🏆 最佳標的：{stats['best_symbol']} | "
            f"最差標的：{stats['worst_symbol']}",
        ]
        return "\n".join(lines)
