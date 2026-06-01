"""
校準器 (Calibrator) — 時間累積疊代修正的核心

讀取 prediction_history.csv 中「已驗證」的預測，做兩件修正：
  1. 信心校準：依 raw_conf 分桶算實際方向命中率 → 校準映射（prediction_calibration.json）
  2. 權重微調：對實際報酬做一步帶 L2 的梯度更新（有界 nudge）→ 寫回 prediction_model.json (version+1)

並產出準確度報告（方向命中率 / 幅度 MAE / 分信心桶 / 分環境 / 權重變動）。

需累積 ≥ MIN_VERIFIED 筆已驗證預測才啟動（沿用 SOL 的 15 筆慣例）。
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

import numpy as np
from loguru import logger

from .predictor import MODEL_PATH, CALIBRATION_PATH, _SCALE
from .prediction_tracker import DEFAULT_HISTORY, PRED_HEADERS

MIN_VERIFIED = 15            # 啟動校準的最低已驗證筆數
_LR = 0.02                   # 權重梯度步長（小 → 只 nudge）
_L2 = 0.01                   # L2 正則
_MAX_STEP = 0.5              # 單次每權重最大變動（%-報酬單位）
_CONF_BUCKETS = [(50, 60), (60, 70), (70, 80), (80, 90), (90, 101)]
_MIN_BUCKET_N = 3
_REPORTS_DIR = Path(__file__).parent.parent.parent / "data" / "reports"


class Calibrator:
    def __init__(self, history_file: str = None, model_path: str = None,
                 calibration_path: str = None):
        self.history_file = Path(history_file) if history_file else DEFAULT_HISTORY
        self.model_path = Path(model_path) if model_path else MODEL_PATH
        self.calibration_path = Path(calibration_path) if calibration_path else CALIBRATION_PATH

    # ── 主入口 ──────────────────────────────────────────────────

    def calibrate(self) -> dict:
        """執行校準 + 權重微調，回傳統計摘要"""
        rows = self._load_verified()
        if len(rows) < MIN_VERIFIED:
            logger.info(f"🧠 預測校準：{len(rows)}/{MIN_VERIFIED} 筆已驗證（資料不足，暫不校準）")
            return {"status": "insufficient", "verified": len(rows)}

        model = json.loads(self.model_path.read_text(encoding="utf-8"))
        calibration = {}
        summary = {"status": "ok", "verified": len(rows), "markets": {}}

        for market in ("tw_stock", "crypto"):
            mrows = [r for r in rows if r["market"] == market]
            if len(mrows) < MIN_VERIFIED:
                continue

            # 1) 信心校準
            calibration[market] = {"buckets": self._calc_calibration(mrows)}

            # 2) 權重微調
            old_w = dict(model["models"][market]["weights"])
            old_b = float(model["models"][market].get("bias", 0.0))
            new_w, new_b, mae = self._update_weights(market, mrows, old_w, old_b)
            model["models"][market]["weights"] = new_w
            model["models"][market]["bias"] = new_b
            model["models"][market]["n_trained"] = len(mrows)

            summary["markets"][market] = {
                "n": len(mrows),
                "direction_hit_rate": self._hit_rate(mrows),
                "mae": round(mae, 3),
                "weight_changes": self._weight_diff(old_w, new_w),
            }

        # 持久化 model（version+1）+ calibration
        model["version"] = int(model.get("version", 1)) + 1
        model["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        model.setdefault("history", []).append({
            "version": model["version"],
            "updated_at": model["updated_at"],
            "verified": len(rows),
        })
        self.model_path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
        self.calibration_path.write_text(json.dumps(calibration, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"✅ 預測模型校準完成 → version {model['version']}")

        # 產出報告
        report = self._build_report(rows, summary, model)
        self._save_report(report)
        summary["report"] = report
        return summary

    # ── 載入 ────────────────────────────────────────────────────

    def _load_verified(self) -> list[dict]:
        if not self.history_file.exists():
            return []
        with open(self.history_file, "r", encoding="utf-8") as f:
            return [r for r in csv.DictReader(f)
                    if r.get("verified") == "true" and r.get("actual_return_pct")]

    # ── 信心校準 ────────────────────────────────────────────────

    @staticmethod
    def _calc_calibration(rows: list[dict]) -> list:
        """依 raw_conf 分桶，算各桶實際方向命中率 → [[lo,hi,hit], ...]"""
        overall = Calibrator._hit_rate(rows) / 100.0
        buckets = []
        for lo, hi in _CONF_BUCKETS:
            in_bucket = [r for r in rows if lo <= float(r.get("raw_conf", 0) or 0) < hi]
            if len(in_bucket) >= _MIN_BUCKET_N:
                hits = sum(1 for r in in_bucket if r.get("direction_hit") == "true")
                emp = hits / len(in_bucket)
            else:
                emp = overall   # 樣本不足 → 用整體命中率回退
            buckets.append([lo, hi, round(emp, 4)])
        return buckets

    # ── 權重微調（一步帶 L2 的梯度更新，有界）─────────────────

    @staticmethod
    def _update_weights(market, rows, old_w, old_b):
        feat_keys = list(old_w.keys())
        X, y = [], []
        for r in rows:
            try:
                feats = json.loads(r.get("features_json", "{}"))
                X.append([float(feats.get(k, 0.0)) for k in feat_keys])
                y.append(float(r["actual_return_pct"]))
            except Exception:
                continue
        if not X:
            return old_w, old_b, 0.0

        X = np.array(X); y = np.array(y)
        w = np.array([old_w[k] for k in feat_keys], dtype=float)
        b = float(old_b)

        pred = X @ w + b
        resid = pred - y
        mae = float(np.mean(np.abs(resid)))

        # 梯度（MSE + L2），一步更新
        n = len(y)
        grad_w = (X.T @ resid) / n + _L2 * w
        grad_b = float(np.mean(resid))
        step_w = np.clip(_LR * grad_w, -_MAX_STEP, _MAX_STEP)
        w_new = w - step_w
        b_new = b - _LR * grad_b

        new_w = {k: round(float(w_new[i]), 4) for i, k in enumerate(feat_keys)}
        return new_w, round(b_new, 4), mae

    # ── 統計輔助 ────────────────────────────────────────────────

    @staticmethod
    def _hit_rate(rows: list[dict]) -> float:
        if not rows:
            return 0.0
        hits = sum(1 for r in rows if r.get("direction_hit") == "true")
        return round(hits / len(rows) * 100, 1)

    @staticmethod
    def _weight_diff(old_w, new_w) -> list:
        diffs = []
        for k in old_w:
            d = new_w.get(k, 0) - old_w[k]
            if abs(d) > 1e-6:
                diffs.append((k, round(old_w[k], 3), round(new_w.get(k, 0), 3), round(d, 3)))
        return sorted(diffs, key=lambda x: -abs(x[3]))[:8]

    @staticmethod
    def _context_hit(rows: list[dict]) -> dict:
        """依環境分組算方向命中率"""
        groups = {}
        for r in rows:
            key = f"{r.get('ctx_phase','')}|{r.get('ctx_season','')}|{r.get('ctx_fg_trend','')}"
            g = groups.setdefault(key, {"n": 0, "hits": 0, "ret": 0.0})
            g["n"] += 1
            g["hits"] += 1 if r.get("direction_hit") == "true" else 0
            g["ret"] += float(r.get("actual_return_pct", 0) or 0)
        for g in groups.values():
            g["hit_rate"] = round(g["hits"] / g["n"] * 100, 1)
            g["avg_ret"] = round(g["ret"] / g["n"], 2)
        return groups

    # ── 報告 ────────────────────────────────────────────────────

    def _build_report(self, rows, summary, model) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        L = [
            "# 🎯 預測模型準確度報告",
            f"**生成時間：** {now}  ｜  **模型版本：** v{model['version']}",
            f"**已驗證預測：** {len(rows)} 筆  ｜  **整體方向命中率：** {self._hit_rate(rows)}%",
            "", "---", "",
        ]
        for market, s in summary.get("markets", {}).items():
            label = {"tw_stock": "台股", "crypto": "虛擬幣"}.get(market, market)
            L += [
                f"## {label}（{s['n']} 筆）",
                f"- 方向命中率：**{s['direction_hit_rate']}%**　幅度 MAE：**{s['mae']}%**",
                "",
                "### 信心校準曲線",
                "| 原始信心區間 | 實際命中率 |",
                "|---|---|",
            ]
            cal = json.loads(self.calibration_path.read_text(encoding="utf-8")) \
                if self.calibration_path.exists() else {}
            for lo, hi, emp in cal.get(market, {}).get("buckets", []):
                L.append(f"| {lo}~{hi} | {emp*100:.0f}% |")
            L += ["", "### 權重變動（前 8 大）", "| 特徵 | 舊 | 新 | Δ |", "|---|---|---|---|"]
            for k, o, nw, d in s["weight_changes"]:
                L.append(f"| {k} | {o} | {nw} | {d:+.3f} |")
            # 建議 ScoreEngine 權重（換回點數）
            scale = _SCALE.get(market, 0.06)
            L += ["", "### 建議 ScoreEngine 權重（換算點數，供人工檢視）", "| 特徵 | 建議點數 |", "|---|---|"]
            for k in model["models"][market]["weights"]:
                pts = model["models"][market]["weights"][k] / scale if scale else 0
                L.append(f"| {k} | {pts:+.1f} |")
            L.append("")

        # 分環境命中率
        ctx = self._context_hit(rows)
        if ctx:
            L += ["---", "", "## 分市場環境命中率", "| 環境 | 筆數 | 命中率 | 平均報酬 |", "|---|---|---|---|"]
            for key, g in sorted(ctx.items(), key=lambda x: -x[1]["n"]):
                L.append(f"| {key} | {g['n']} | {g['hit_rate']}% | {g['avg_ret']:+.2f}% |")
            L.append("")

        L += ["---", "", "> ⚠️ 模型透過時間累積疊代修正，樣本越多越可靠。權重為 nudge 式更新，可由 model.history 回滾。"]
        return "\n".join(L)

    def _save_report(self, report: str):
        _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        path = _REPORTS_DIR / f"prediction_accuracy_{datetime.now():%Y-%m-%d}.md"
        path.write_text(report, encoding="utf-8")
        logger.info(f"📄 預測準確度報告：{path}")
