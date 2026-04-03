"""
SOL 環境績效分析與動態門檻調整器
(Self-Optimization & Learning - Contextual Optimizer)

Phase 2: 分析不同市場環境下的信號表現
Phase 3: 根據分析結果動態調整交易門檻

使用方式:
    from src.analysis.contextual_optimizer import ContextualOptimizer
    optimizer = ContextualOptimizer()
    bias = optimizer.get_bias()
    # bias.min_agreement → 動態門檻
    # bias.blocked_contexts → 應否決的環境組合
"""

from __future__ import annotations
import csv
import json
import datetime as dt
from pathlib import Path
from dataclasses import dataclass, field
from loguru import logger

HISTORY_FILE = Path(__file__).parent.parent / "monitor" / ".." / ".." / "data" / "signal_history.csv"
BIAS_FILE = Path(__file__).parent.parent / ".." / "data" / "sol_bias.json"

# 最少需要多少筆帶有 context 的信號才能進行分析
MIN_SIGNALS_FOR_ANALYSIS = 15


@dataclass
class ContextPerformance:
    """特定環境組合的績效統計"""
    context_key: str       # e.g. "BEAR|BTC_SEASON|FALLING"
    total: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    win_rate: float = 0.0

    @property
    def is_toxic(self) -> bool:
        """判斷此環境組合是否有毒（勝率極低且虧損顯著）"""
        return self.total >= 5 and self.win_rate < 20.0 and self.avg_pnl < -0.002

    @property
    def is_golden(self) -> bool:
        """判斷此環境組合是否是黃金組合（勝率高且獲利顯著）"""
        return self.total >= 5 and self.win_rate > 60.0 and self.avg_pnl > 0.001


@dataclass
class TradingBias:
    """動態交易偏差設定 — 根據歷史環境績效自動計算"""

    # 動態門檻
    min_agreement: float = 0.55           # 預設值，可能被動態調整
    min_agreement_reason: str = "default"

    # 被封鎖的「有毒」環境組合
    blocked_contexts: list = field(default_factory=list)

    # 被加持的「黃金」環境組合
    golden_contexts: list = field(default_factory=list)

    # 環境績效總覽
    context_stats: dict = field(default_factory=dict)

    # 分析元資料
    analysis_date: str = ""
    total_signals_analyzed: int = 0

    def should_block(self, phase: str, season: str, fg_trend: str) -> tuple[bool, str]:
        """
        根據當前環境判斷是否應該封鎖交易。

        Returns:
            (should_block, reason)
        """
        current_key = f"{phase}|{season}|{fg_trend}"
        for blocked in self.blocked_contexts:
            if blocked["key"] == current_key:
                return True, (
                    f"🚫 SOL 自動否決：環境 [{current_key}] "
                    f"歷史勝率 {blocked['win_rate']:.0f}% "
                    f"(avg PnL {blocked['avg_pnl']*100:+.2f}%)"
                )
        return False, ""

    def get_dynamic_agreement(self, phase: str, season: str, fg_trend: str) -> float:
        """
        根據當前環境返回動態 min_agreement 門檻。
        黃金環境降低門檻（更激進），有毒環境提高門檻（更保守）。
        """
        current_key = f"{phase}|{season}|{fg_trend}"

        for golden in self.golden_contexts:
            if golden["key"] == current_key:
                return max(0.40, self.min_agreement - 0.10)  # 黃金環境: 降低門檻

        for blocked in self.blocked_contexts:
            if blocked["key"] == current_key:
                return min(0.80, self.min_agreement + 0.15)  # 有毒環境: 大幅提高

        return self.min_agreement  # 預設


class ContextualOptimizer:
    """
    SOL Phase 2+3: 環境績效分析 + 動態門檻調整
    
    - 讀取 signal_history.csv 中帶有 ctx_ 標籤的信號
    - 分析每種環境組合的歷史勝率
    - 產生 TradingBias 物件，供 DecisionEngine 使用
    - 將分析結果持久化到 sol_bias.json
    """

    def __init__(self, history_file: str = None, bias_file: str = None):
        self.history_file = Path(history_file) if history_file else HISTORY_FILE.resolve()
        self.bias_file = Path(bias_file) if bias_file else BIAS_FILE.resolve()

    def analyze_and_update(self) -> TradingBias:
        """
        主入口：分析歷史信號，更新 TradingBias。

        Returns:
            TradingBias 物件，包含動態門檻和封鎖/加持環境列表
        """
        signals = self._load_tagged_signals()

        if len(signals) < MIN_SIGNALS_FOR_ANALYSIS:
            logger.info(
                f"🧠 SOL: {len(signals)}/{MIN_SIGNALS_FOR_ANALYSIS} 筆帶標籤信號 "
                f"(需要更多數據才能進行環境分析)"
            )
            return self._load_or_default()

        # Phase 2: 分析每種環境組合的績效
        context_perf = self._analyze_context_performance(signals)

        # Phase 3: 根據分析結果生成動態偏差
        bias = self._generate_bias(context_perf, len(signals))

        # 持久化
        self._save_bias(bias)

        return bias

    def get_bias(self) -> TradingBias:
        """取得最新的 TradingBias（從 cache 或即時分析）"""
        return self.analyze_and_update()

    def get_report_text(self) -> str:
        """產生 SOL 環境分析的人類可讀報告"""
        bias = self._load_or_default()
        if bias.total_signals_analyzed == 0:
            return "🧠 SOL 學習中：尚無足夠數據進行環境分析"

        lines = [
            f"🧠 *SOL 環境學習報告* ({bias.analysis_date})",
            f"分析信號：{bias.total_signals_analyzed} 筆",
        ]

        if bias.blocked_contexts:
            lines.append(f"🚫 有毒環境 ({len(bias.blocked_contexts)} 個)：")
            for b in bias.blocked_contexts:
                lines.append(
                    f"  • {b['key']} → 勝率 {b['win_rate']:.0f}% "
                    f"avg {b['avg_pnl']*100:+.2f}%"
                )

        if bias.golden_contexts:
            lines.append(f"✨ 黃金環境 ({len(bias.golden_contexts)} 個)：")
            for g in bias.golden_contexts:
                lines.append(
                    f"  • {g['key']} → 勝率 {g['win_rate']:.0f}% "
                    f"avg {g['avg_pnl']*100:+.2f}%"
                )

        if not bias.blocked_contexts and not bias.golden_contexts:
            lines.append("📊 目前無明顯有毒/黃金環境組合（需更多數據）")

        lines.append(f"⚙️ 動態門檻：{bias.min_agreement:.2f} ({bias.min_agreement_reason})")

        return "\n".join(lines)

    # ── 內部方法 ──────────────────────────────────────────────

    def _load_tagged_signals(self) -> list[dict]:
        """從 CSV 載入有 ctx_ 標籤的信號"""
        if not self.history_file.exists():
            return []

        tagged = []
        with open(self.history_file, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # 只取有標籤且有 1h PnL 的信號
                if row.get("ctx_phase") and row.get("pnl_1h_pct"):
                    tagged.append(row)
        return tagged

    def _analyze_context_performance(self, signals: list[dict]) -> dict[str, ContextPerformance]:
        """
        Phase 2: 計算每種環境組合的績效。
        
        環境 key 格式: "PHASE|SEASON|FG_TREND"
        例如: "BEAR|BTC_SEASON|FALLING"
        """
        perf_map: dict[str, ContextPerformance] = {}

        for sig in signals:
            key = f"{sig['ctx_phase']}|{sig['ctx_season']}|{sig.get('ctx_fg_trend', '')}"
            pnl = float(sig["pnl_1h_pct"])

            if key not in perf_map:
                perf_map[key] = ContextPerformance(context_key=key)

            cp = perf_map[key]
            cp.total += 1
            cp.total_pnl += pnl
            if pnl > 0:
                cp.wins += 1
            else:
                cp.losses += 1

        # 計算衍生指標
        for cp in perf_map.values():
            if cp.total > 0:
                cp.win_rate = (cp.wins / cp.total) * 100
                cp.avg_pnl = cp.total_pnl / cp.total

        return perf_map

    def _generate_bias(self, perf_map: dict[str, ContextPerformance],
                       total_signals: int) -> TradingBias:
        """
        Phase 3: 根據環境績效生成動態交易偏差。
        """
        bias = TradingBias(
            analysis_date=dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
            total_signals_analyzed=total_signals,
        )

        blocked = []
        golden = []

        for key, cp in perf_map.items():
            entry = {
                "key": key,
                "total": cp.total,
                "wins": cp.wins,
                "losses": cp.losses,
                "win_rate": cp.win_rate,
                "avg_pnl": cp.avg_pnl,
            }
            if cp.is_toxic:
                blocked.append(entry)
                logger.warning(f"🚫 SOL 有毒環境: {key} (勝率{cp.win_rate:.0f}%, avg {cp.avg_pnl*100:+.2f}%)")
            elif cp.is_golden:
                golden.append(entry)
                logger.info(f"✨ SOL 黃金環境: {key} (勝率{cp.win_rate:.0f}%, avg {cp.avg_pnl*100:+.2f}%)")

        bias.blocked_contexts = blocked
        bias.golden_contexts = golden

        # 根據整體績效決定動態門檻
        all_pnl = [float(s["pnl_1h_pct"]) for s in self._load_tagged_signals()]
        if all_pnl:
            overall_win_rate = sum(1 for p in all_pnl if p > 0) / len(all_pnl) * 100
            if overall_win_rate < 30:
                bias.min_agreement = 0.65
                bias.min_agreement_reason = f"勝率偏低 ({overall_win_rate:.0f}%)，提高門檻"
            elif overall_win_rate > 55:
                bias.min_agreement = 0.45
                bias.min_agreement_reason = f"勝率良好 ({overall_win_rate:.0f}%)，適度放寬"
            else:
                bias.min_agreement = 0.55
                bias.min_agreement_reason = f"勝率正常 ({overall_win_rate:.0f}%)，維持預設"

        # 環境統計 (供報告用)
        bias.context_stats = {
            k: {"total": cp.total, "win_rate": round(cp.win_rate, 1),
                "avg_pnl": round(cp.avg_pnl * 100, 3)}
            for k, cp in perf_map.items()
        }

        return bias

    def _save_bias(self, bias: TradingBias):
        """持久化 TradingBias 到 JSON"""
        self.bias_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "analysis_date": bias.analysis_date,
            "total_signals_analyzed": bias.total_signals_analyzed,
            "min_agreement": bias.min_agreement,
            "min_agreement_reason": bias.min_agreement_reason,
            "blocked_contexts": bias.blocked_contexts,
            "golden_contexts": bias.golden_contexts,
            "context_stats": bias.context_stats,
        }
        with open(self.bias_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info(f"🧠 SOL bias saved to {self.bias_file}")

    def _load_or_default(self) -> TradingBias:
        """從 JSON 載入或返回預設值"""
        if self.bias_file.exists():
            try:
                with open(self.bias_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                bias = TradingBias(
                    min_agreement=data.get("min_agreement", 0.55),
                    min_agreement_reason=data.get("min_agreement_reason", "cached"),
                    blocked_contexts=data.get("blocked_contexts", []),
                    golden_contexts=data.get("golden_contexts", []),
                    context_stats=data.get("context_stats", {}),
                    analysis_date=data.get("analysis_date", ""),
                    total_signals_analyzed=data.get("total_signals_analyzed", 0),
                )
                return bias
            except Exception as e:
                logger.warning(f"SOL bias load error: {e}")
        return TradingBias()
