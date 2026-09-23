"""
預測模型 (Predictor)

對掃描候選做出明確、可驗證的預測：未來 N 日「方向 + 幅度(%)」+ 信心。

核心設計：線性模型
    expected_return% = bias + Σ wᵢ · featureᵢ

特徵 = 掃描器既有的信號旗標（inst_buy_strong / ma_bullish_full / ...），
權重初始化自 ScoreEngine.WEIGHTS（換算成 %-報酬單位）。因此「預測權重」與
「評分權重」共用同一套詞彙，calibrator 對實際報酬做回歸即可微調這些權重。

model 狀態存於 data/prediction_model.json（calibrator 會 version+1 寫回）。
信心校準映射存於 data/prediction_calibration.json（calibrator 產生）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from math import tanh
from pathlib import Path
from typing import Optional

from loguru import logger

_DATA_DIR        = Path(__file__).parent.parent.parent / "data"
MODEL_PATH       = _DATA_DIR / "prediction_model.json"
CALIBRATION_PATH = _DATA_DIR / "prediction_calibration.json"

# 預設 horizon（交易日）
DEFAULT_HORIZON = {"tw_stock": 5, "crypto": 2}

# score 點數 → %-報酬 的初始換算（滿分正信號 ~100 點 → 台股 ~6% / 5日；幣 ~10% / 2日）
_SCALE = {"tw_stock": 0.06, "crypto": 0.10}

# 方向判定門檻（%）
_DIR_THRESHOLD = {"tw_stock": 1.0, "crypto": 2.0}


# ──────────────────────────────────────────────────────────────
# 特徵抽取（特徵名 = ScoreEngine 信號旗標，確保詞彙一致）
# ──────────────────────────────────────────────────────────────

def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _squash(v: float, scale: float) -> float:
    """以 tanh 平滑壓縮到 (-1, 1)，保留正負號；用於張數等長尾數值"""
    return tanh(v / scale) if scale else 0.0


def _tw_features(c) -> dict:
    """
    台股 ScanCandidate → 特徵向量。

    改進（2026-09）：原本全為 0/1 二元旗標，導致「信號組合相同的股票得到
    完全相同的預測」——實測 25 筆台股預測中有 19 筆同為 +4.80%，毫無區辨度。
    現在在保留二元旗標（延續 ScoreEngine 語意與權重初始化）之外，
    另加一組連續特徵（c_ 前綴），讓同組信號的個股也能依強弱產生梯度。
    """
    vr  = float(getattr(c, "volume_ratio", 0) or 0)
    ti  = int(getattr(c, "total_inst", 0) or 0)
    fn  = int(getattr(c, "foreign_net", 0) or 0)
    tn  = int(getattr(c, "trust_net", 0) or 0)
    rsi = float(getattr(c, "rsi", 0) or 0)
    vol = int(getattr(c, "volume", 0) or 0)
    mc  = int(getattr(c, "margin_change", 0) or 0)
    a20 = bool(getattr(c, "above_ma20", False))
    a60 = bool(getattr(c, "above_ma60", False))
    chg = float(getattr(c, "change_pct", 0) or 0)
    score = float(getattr(c, "score", 0) or 0)
    return {
        # ── 二元旗標（與 ScoreEngine.WEIGHTS 同詞彙）──────────────
        "inst_buy_strong":    1.0 if ti > 3000 else 0.0,
        "inst_buy_normal":    1.0 if 500 < ti <= 3000 else 0.0,
        "volume_surge_high":  1.0 if vr >= 3.0 else 0.0,
        "volume_surge_mid":   1.0 if 2.0 <= vr < 3.0 else 0.0,
        "ma_bullish_full":    1.0 if (a20 and a60) else 0.0,
        "ma_bullish_partial": 1.0 if (a20 and not a60) else 0.0,
        "rsi_sweet_spot":     1.0 if 40 <= rsi <= 65 else 0.0,
        "margin_healthy":     1.0 if (vol > 0 and mc < vol * 0.01) else 0.0,
        # 負向（風險）特徵
        "margin_surge":       1.0 if (vol > 0 and mc > vol * 0.03) else 0.0,
        "inst_sell":          1.0 if ti < -500 else 0.0,
        "top_reversal":       1.0 if (vr >= 2.0 and chg < -1.0 and a60) else 0.0,
        "rsi_overbought":     1.0 if rsi > 75 else 0.0,
        # ── 連續特徵（提供區辨梯度）────────────────────────────
        "c_score":         score / 100.0,                    # 0~1
        "c_vol_ratio":     _clip(vr, 0, 5) / 5.0,            # 0~1（>5x 視為飽和）
        "c_inst_flow":     _squash(ti, 3000),                # -1~1
        "c_foreign_flow":  _squash(fn, 3000),                # -1~1
        "c_trust_flow":    _squash(tn, 1500),                # -1~1（投信量級較小）
        "c_rsi_dev":       _clip((rsi - 50.0) / 50.0, -1, 1),  # -1~1（正=偏熱）
        "c_change":        _clip(chg, -10, 10) / 10.0,       # -1~1
    }


def _crypto_features(c) -> dict:
    """
    虛擬幣 CryptoCandidate → 特徵向量。
    同樣在二元旗標外加上 c_ 連續特徵以提供區辨梯度（見 _tw_features 說明）。
    """
    vr   = float(getattr(c, "volume_ratio", 0) or 0)
    c1h  = float(getattr(c, "change_1h", 0) or 0)
    c4h  = float(getattr(c, "change_4h", 0) or 0)
    c24  = float(getattr(c, "change_24h", 0) or 0)
    rsi1 = float(getattr(c, "rsi_1h", 0) or 0)
    a20  = bool(getattr(c, "above_ma20_4h", False))
    rank = int(getattr(c, "market_cap_rank", 999) or 999)
    score = float(getattr(c, "score", 0) or 0)
    return {
        # ── 二元旗標 ────────────────────────────────────────────
        "vol_surge_high":  1.0 if vr >= 4.0 else 0.0,
        "vol_surge_mid":   1.0 if 2.5 <= vr < 4.0 else 0.0,
        "vol_mild":        1.0 if 1.5 <= vr < 2.5 else 0.0,
        "mom_1h_strong":   1.0 if c1h >= 3.0 else 0.0,
        "mom_1h_mid":      1.0 if 1.5 <= c1h < 3.0 else 0.0,
        "trend_4h_strong": 1.0 if c4h >= 8.0 else 0.0,
        "trend_4h_mid":    1.0 if 4.0 <= c4h < 8.0 else 0.0,
        "trend_4h_mild":   1.0 if 1.5 <= c4h < 4.0 else 0.0,
        "day_bull_ma":     1.0 if (c24 > 0 and a20) else 0.0,
        "day_bull":        1.0 if (c24 > 0 and not a20) else 0.0,
        "liq_top20":       1.0 if rank <= 20 else 0.0,
        "liq_top50":       1.0 if 20 < rank <= 50 else 0.0,
        "rsi_sweet":       1.0 if 45 <= rsi1 <= 70 else 0.0,
        # 負向
        "mom_1h_drop":     1.0 if c1h <= -5.0 else 0.0,
        "day_crash":       1.0 if c24 < -10.0 else 0.0,
        "rsi_overbought":  1.0 if rsi1 > 80 else 0.0,
        # ── 連續特徵 ────────────────────────────────────────────
        "c_score":       score / 100.0,
        "c_vol_ratio":   _clip(vr, 0, 6) / 6.0,
        "c_mom_1h":      _clip(c1h, -8, 8) / 8.0,
        "c_trend_4h":    _clip(c4h, -15, 15) / 15.0,
        "c_change_24h":  _clip(c24, -25, 25) / 25.0,
        "c_rsi_dev":     _clip((rsi1 - 50.0) / 50.0, -1, 1),
        "c_liquidity":   _clip((200 - rank) / 200.0, 0, 1),   # 排名越前越接近1
    }


def extract_features(candidate, market: str) -> dict:
    """依市場抽取特徵向量"""
    if market == "tw_stock":
        return _tw_features(candidate)
    if market == "crypto":
        return _crypto_features(candidate)
    raise ValueError(f"未知市場: {market}")


# 連續特徵的初始權重（%-報酬單位）。
# 刻意設得比二元旗標小：最強情境下總貢獻約 +2.5%，讓 day-1 預測幅度維持
# 在合理區間（強勢股 5 日約 6~7%，而非動輒 +10%），同時提供足夠的區辨梯度。
# 這些只是起始猜測，真正的權重由 calibrator 依實際報酬回歸逐步修正。
_INIT_CONT_TW = {
    "c_score":        0.6,    # 評分越高預期報酬越高
    "c_vol_ratio":    0.4,    # 量能確認
    "c_inst_flow":    0.5,    # 三大法人淨流入
    "c_foreign_flow": 0.5,    # 外資淨流入
    "c_trust_flow":   0.25,   # 投信淨流入
    "c_rsi_dev":     -0.45,   # RSI 偏熱 → 扣分（過熱風險）
    "c_change":       0.15,   # 當日動能（小幅正向）
}

_INIT_CONT_CRYPTO = {
    "c_score":       0.7,
    "c_vol_ratio":   0.5,
    "c_mom_1h":      0.4,
    "c_trend_4h":    0.6,
    "c_change_24h":  0.3,
    "c_rsi_dev":    -0.5,
    "c_liquidity":   0.25,
}


# 初始權重（%-報酬單位）：由 ScoreEngine 權重 × scale 換算，再併入連續特徵
def _initial_tw_weights() -> dict:
    from src.scanner.post_market_scanner import ScoreEngine
    w = ScoreEngine.WEIGHTS
    s = _SCALE["tw_stock"]
    out = {k: round(v * s, 4) for k, v in w.items()}
    out.update(_INIT_CONT_TW)
    return out


def _initial_crypto_weights() -> dict:
    """crypto 權重內聯於 score()，此處用與其一致的點數 × scale"""
    pts = {
        "vol_surge_high": 25, "vol_surge_mid": 18, "vol_mild": 8,
        "mom_1h_strong": 15, "mom_1h_mid": 8,
        "trend_4h_strong": 20, "trend_4h_mid": 12, "trend_4h_mild": 6,
        "day_bull_ma": 20, "day_bull": 10,
        "liq_top20": 10, "liq_top50": 7,
        "rsi_sweet": 10,
        "mom_1h_drop": -10, "day_crash": -5, "rsi_overbought": -15,
    }
    s = _SCALE["crypto"]
    out = {k: round(v * s, 4) for k, v in pts.items()}
    out.update(_INIT_CONT_CRYPTO)
    return out


_INITIAL_WEIGHTS = {
    "tw_stock": _initial_tw_weights,
    "crypto":   _initial_crypto_weights,
}


# ──────────────────────────────────────────────────────────────
# 預測結果
# ──────────────────────────────────────────────────────────────

@dataclass
class Forecast:
    market:        str
    symbol:        str
    asset_name:    str
    source:        str            # TW_SCAN / CRYPTO_SCAN / ROUNDTABLE
    score:         int
    entry_price:   float
    expected_return_pct: float    # 預測 N 日報酬
    direction:     str            # UP / DOWN / FLAT
    horizon_days:  int
    raw_confidence: float         # 0~100
    cal_confidence: float         # 校準後 0~100
    features:      dict = field(default_factory=dict)

    def to_line(self) -> str:
        icon = {"UP": "📈", "DOWN": "📉", "FLAT": "➡️"}.get(self.direction, "?")
        return (
            f"  {icon} [{self.market}] {self.symbol} {self.asset_name}  "
            f"預測{self.horizon_days}日: {self.expected_return_pct:+.1f}% ({self.direction})  "
            f"信心:{self.cal_confidence:.0f}  評分:{self.score}"
        )


# ──────────────────────────────────────────────────────────────
# 預測器
# ──────────────────────────────────────────────────────────────

class Predictor:
    """線性預測模型；冷啟動用 ScoreEngine 權重初始化"""

    def __init__(self, model_path: str = None, calibration_path: str = None):
        self.model_path = Path(model_path) if model_path else MODEL_PATH
        self.calibration_path = Path(calibration_path) if calibration_path else CALIBRATION_PATH
        self.model = self._load_or_init_model()
        self.calibration = self._load_calibration()

    def _load_or_init_model(self) -> dict:
        if self.model_path.exists():
            try:
                model = json.loads(self.model_path.read_text(encoding="utf-8"))
                return self._migrate_model(model)
            except Exception as e:
                logger.warning(f"prediction_model.json 解析失敗，改用初始權重: {e}")
        # 冷啟動初始化
        model = {
            "version": 1,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "models": {
                "tw_stock": {"bias": 0.0, "weights": _initial_tw_weights(), "n_trained": 0},
                "crypto":   {"bias": 0.0, "weights": _initial_crypto_weights(), "n_trained": 0},
            },
            "history": [],
        }
        self._save_model(model)
        logger.info("🆕 初始化 prediction_model.json（權重來自 ScoreEngine）")
        return model

    def _migrate_model(self, model: dict) -> dict:
        """
        補齊舊模型缺少的特徵權重。

        新增連續特徵（c_ 前綴）後，既有 model json 不含這些鍵，
        predict() 會以 0 代入而完全失效。此處補上初始值並存檔，
        使既有累積的訓練成果（其他權重）得以保留。
        """
        changed = []
        for market, init_fn in _INITIAL_WEIGHTS.items():
            m = model.setdefault("models", {}).setdefault(
                market, {"bias": 0.0, "weights": {}, "n_trained": 0})
            weights = m.setdefault("weights", {})
            for k, v in init_fn().items():
                if k not in weights:
                    weights[k] = v
                    changed.append(f"{market}.{k}")
        if changed:
            self._save_model(model)
            logger.info(f"🔄 模型已補齊 {len(changed)} 個新特徵權重（連續特徵）")
        return model

    def _save_model(self, model: dict) -> None:
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        self.model_path.write_text(
            json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _load_calibration(self) -> dict:
        if self.calibration_path.exists():
            try:
                return json.loads(self.calibration_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    # ── 預測 ────────────────────────────────────────────────────

    def predict(self, candidate, market: str, source: str) -> Forecast:
        feats = extract_features(candidate, market)
        m = self.model["models"].get(market, {})
        bias = float(m.get("bias", 0.0))
        weights = m.get("weights", {})

        er = bias + sum(weights.get(k, 0.0) * v for k, v in feats.items())

        thr = _DIR_THRESHOLD.get(market, 1.0)
        direction = "UP" if er > thr else ("DOWN" if er < -thr else "FLAT")

        # 原始信心：er 絕對值越大越有把握
        raw_conf = 50.0 + 50.0 * tanh(abs(er) / 4.0)
        cal_conf = self._apply_calibration(market, raw_conf)

        # 取代號/名稱/價格（台股 vs 幣）
        if market == "tw_stock":
            symbol = getattr(candidate, "stock_id", "")
            name   = getattr(candidate, "stock_name", "")
            price  = float(getattr(candidate, "close", 0) or 0)
        else:
            symbol = getattr(candidate, "base", "") or getattr(candidate, "symbol", "")
            name   = symbol
            price  = float(getattr(candidate, "price", 0) or 0)

        return Forecast(
            market=market, symbol=symbol, asset_name=name, source=source,
            score=int(getattr(candidate, "score", 0) or 0),
            entry_price=price,
            expected_return_pct=round(er, 3),
            direction=direction,
            horizon_days=DEFAULT_HORIZON.get(market, 5),
            raw_confidence=round(raw_conf, 1),
            cal_confidence=round(cal_conf, 1),
            features=feats,
        )

    def _apply_calibration(self, market: str, raw_conf: float) -> float:
        """套用信心校準映射（無映射時回傳原值）"""
        buckets = (self.calibration.get(market, {}) or {}).get("buckets", [])
        for lo, hi, empirical_hit in buckets:
            if lo <= raw_conf < hi or (hi >= 100 and raw_conf >= lo):
                return round(empirical_hit * 100, 1)
        return raw_conf
