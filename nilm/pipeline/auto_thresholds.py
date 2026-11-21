"""on_thr_w 与 decision_thr_w 自动生成、显式优先解析与黄/红高亮日志模块。

设计准则：
1. 完全向后兼容：未配置 auto 的历史显式数值配置及旧版训练 meta.json 保持 100% 原行为；
2. 显式优先：用户在 user_key / _default / CLI 显式指定的数值阈值拥有最高优先级，绝不被自动覆盖；
3. 零测试/推理泄漏：
   - on_thr_w="auto" 仅基于训练时间窗口内的分路功率分布做对数域 Otsu 双峰寻谷；
   - decision_thr_w="auto" 仅在选定推理模型的 val 验证集（严禁触碰 test/infer 真值）执行带三重物理护栏的判决链寻优；
   - 推理阶段直接继承 train/meta.json 锁定的阈值；若存在离线分路真值，仅在落盘后做只读跨期漂移告警，绝不篡改推理结果；
4. 全程留痕与黄/红提示日志：
   - 完整候选表、双峰分离度、命中护栏与决策原因写入 meta.json；
   - 凡 auto 模式生效，常规高置信定阈/继承输出亮黄色提示日志，低分离度/护栏退化/跨期漂移异常输出亮红色告警日志。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from nilm.common.contracts import INFER_RESULT_COLUMNS, INFERENCE_RESULT_REL
from nilm.common.logging import get_logger, red_text, yellow_text
from nilm.evaluation import build_comparison_table, evaluate_daily_chain, summarize
from nilm.postprocess.state import postprocess_state, state_probability

log = get_logger("pipeline.auto_thresholds")

NICE_ON_THRESHOLDS: tuple[float, ...] = (
    0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 20.0, 25.0,
    30.0, 40.0, 50.0, 60.0, 80.0, 100.0, 120.0, 150.0, 200.0, 300.0, 500.0,
)

STANDARD_DECISION_GRID: tuple[float, ...] = (
    0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0,
    60.0, 80.0, 100.0, 120.0, 150.0, 200.0, 250.0, 300.0, 350.0, 400.0, 450.0, 500.0,
    600.0, 750.0, 1000.0,
)

MIN_VAL_RECALL = 0.95
UPPER_ON_P10_RATIO = 0.80
MIN_DECISION_F1_GAIN = 0.003
TIE_F1_TOL = 0.002
SCALED_PLATEAU_F1_TOL = 0.006
SCALED_PLATEAU_MIN_RECALL = 0.965


def _snap_nice_threshold(val: float, lo: float, hi: float) -> float:
    """在物理合法走廊 [lo, hi] 内吸附到最近的工程整洁档位；无匹配档位则保留两位有效小数。"""
    feasible = [g for g in NICE_ON_THRESHOLDS if lo - 1e-9 <= g <= hi + 1e-9]
    if not feasible:
        return round(float(np.clip(val, lo, hi)), 3)
    log_v = np.log1p(max(val, 0.0))
    best = min(feasible, key=lambda g: abs(np.log1p(g) - log_v))
    return float(best)


def _otsu_log1p(y_pos: np.ndarray, n_bins: int = 256) -> tuple[float, float]:
    """在 log1p(y) 域执行一维 Otsu 最大类间方差分割，返回 (y_otsu, otsu_eta)。"""
    z = np.log1p(np.maximum(y_pos, 0.0))
    z_min, z_max = float(z.min()), float(z.max())
    if z_max - z_min < 1e-9:
        return float(np.expm1(z_min)), 0.0
    hist, edges = np.histogram(z, bins=n_bins, range=(z_min, z_max))
    prob = hist.astype(np.float64) / float(len(z))
    centers = 0.5 * (edges[:-1] + edges[1:])
    omega0 = np.cumsum(prob)
    omega1 = 1.0 - omega0
    mu_cum = np.cumsum(prob * centers)
    mu_total = float(mu_cum[-1])
    var_total = float(np.sum(prob * (centers - mu_total) ** 2))
    if var_total <= 1e-12:
        return float(np.expm1(z_min)), 0.0

    valid = (omega0 > 1e-6) & (omega1 > 1e-6)
    sigma_b2 = np.zeros_like(prob)
    sigma_b2[valid] = ((mu_total * omega0[valid] - mu_cum[valid]) ** 2
                       / (omega0[valid] * omega1[valid]))
    max_sb2 = float(sigma_b2.max())
    eta = float(np.clip(max_sb2 / var_total, 0.0, 1.0))
    # 若存在零频谷底平台，取最大类间方差区间的中点（对应原功率域几何均值）
    peak_idx = np.flatnonzero(sigma_b2 >= max_sb2 * (1.0 - 1e-6))
    mid_bin = int(peak_idx[len(peak_idx) // 2])
    z_star = float(edges[mid_bin + 1])
    return float(np.expm1(z_star)), eta


def resolve_auto_on_thr_w(y_pool: np.ndarray | pd.Series,
                          fallback_thr_w: float = 10.0) -> tuple[float, dict]:
    """基于训练范围分路目标功率分布自动估计物理开态阈值 on_thr_w。

    遵循《调参实战手册》Step 1a 物理定阈准则：
      on_thr_w = 待机纹波带上沿 + 安全余量，且 <= 最小工作功率 P05(y_on) * 0.30。
    """
    arr = np.asarray(y_pool, dtype=np.float64).reshape(-1)
    y = arr[np.isfinite(arr)]
    fallback = float(fallback_thr_w)
    if len(y) < 16:
        return fallback, {
            "configured": "auto",
            "resolved_on_thr_w": fallback,
            "stage": "auto_fallback",
            "confidence": "low",
            "n_samples": int(len(y)),
            "reasons": ["INSUFFICIENT_SAMPLES_FALLBACK"],
        }

    y_nonneg = np.maximum(y, 0.0)
    p05 = float(np.quantile(y_nonneg, 0.05))
    p50 = float(np.quantile(y_nonneg, 0.50))
    p95 = float(np.quantile(y_nonneg, 0.95))
    p99 = float(np.quantile(y_nonneg, 0.99))

    if p99 <= 0.5:
        return fallback, {
            "configured": "auto",
            "resolved_on_thr_w": fallback,
            "stage": "auto_fallback",
            "confidence": "low",
            "n_samples": int(len(y)),
            "y_p05": round(p05, 3),
            "y_p99": round(p99, 3),
            "reasons": ["ALL_ZERO_SIGNAL_FALLBACK"],
        }

    # 全时段常开基荷（无停机簇）：禁止在工作带内部强行二分制造假关机
    if p05 >= 0.30 * p95 and p05 > 0.5:
        hi = max(0.30 * p05, 0.001)
        cand = min(fallback, hi)
        resolved = _snap_nice_threshold(cand, lo=0.001, hi=hi)
        return resolved, {
            "configured": "auto",
            "resolved_on_thr_w": resolved,
            "stage": "auto_always_on_guard",
            "confidence": "low",
            "n_samples": int(len(y)),
            "y_p05": round(p05, 3),
            "y_p50": round(p50, 3),
            "y_p95": round(p95, 3),
            "on_ratio": 1.0,
            "reasons": ["ALWAYS_ON_NO_OFF_CLUSTER"],
        }

    y_otsu, otsu_eta = _otsu_log1p(y_nonneg)
    y_off = y_nonneg[y_nonneg < y_otsu]
    y_on = y_nonneg[y_nonneg >= y_otsu]
    if len(y_off) == 0 or len(y_on) == 0:
        return fallback, {
            "configured": "auto",
            "resolved_on_thr_w": fallback,
            "stage": "auto_fallback",
            "confidence": "low",
            "n_samples": int(len(y)),
            "otsu_eta": round(otsu_eta, 4),
            "reasons": ["SINGLE_CLUSTER_FALLBACK"],
        }

    y_off_p98 = float(np.quantile(y_off, 0.98))
    y_off_p99 = float(np.quantile(y_off, 0.99))
    y_on_p05 = float(np.quantile(y_on, 0.05))
    y_on_p10 = float(np.quantile(y_on, 0.10))
    y_on_p50 = float(np.quantile(y_on, 0.50))
    y_on_p95 = float(np.quantile(y_on, 0.95))

    min_floor = min(fallback, 0.15 * max(y_on_p05, 0.01))
    raw_thr = max(min_floor, 1.40 * y_off_p99, y_off_p99 + 0.01 * y_on_p50)
    hi_bound = max(0.30 * y_on_p05, 0.001)
    lo_bound = min(max(1.15 * y_off_p99, 0.001), hi_bound)
    clamped = float(np.clip(raw_thr, lo_bound, hi_bound))
    resolved = _snap_nice_threshold(clamped, lo=lo_bound, hi=hi_bound)
    resolved = float(np.clip(resolved, 0.001, 5000.0))

    # 谷底过渡带/多档待机歧义检测（同时检测谷底弥散与开态簇内部多档辅机双峰分裂）
    valley_lo = max(1.20 * y_off_p99, 1.0)
    valley_hi = 0.50 * y_on_p05
    if valley_hi > valley_lo:
        valley_density = float(((y_nonneg >= valley_lo) & (y_nonneg <= valley_hi)).mean())
    else:
        valley_density = 0.0

    on_sub_eta = 0.0
    if len(y_on) >= 20 and y_on_p95 > 2.5 * max(y_on_p10, 0.1):
        _, on_sub_eta = _otsu_log1p(y_on)

    reasons: list[str] = []
    confidence = "high"
    if len(y) < 96:
        confidence = "low"
        reasons.append("SHORT_SERIES_LT_1DAY")
    if otsu_eta < 0.72:
        confidence = "low"
        reasons.append(f"LOW_BIMODAL_SEPARABILITY(eta={otsu_eta:.3f}<0.72)")
    if valley_density > 0.08:
        confidence = "low"
        reasons.append(f"INTERMEDIATE_PLATEAU_SUSPECTED(valley_ratio={valley_density:.3f}>0.08)")
    if y_on_p95 > 2.5 * max(y_on_p10, 0.1) and on_sub_eta >= 0.72:
        confidence = "low"
        reasons.append(
            f"MULTIMODAL_ON_GEAR_SUSPECTED(on_p10={y_on_p10:.1f},on_p95={y_on_p95:.1f},sub_eta={on_sub_eta:.3f})"
        )
    if y_off_p99 > 0.25 * max(y_on_p05, 1e-6):
        confidence = "low"
        reasons.append("HIGH_STANDBY_NARROW_GAP")
    if not reasons:
        reasons.append("LOG_OTSU_BIMODAL_CLEAR")

    on_ratio = float((y_nonneg >= resolved).mean())
    return resolved, {
        "configured": "auto",
        "resolved_on_thr_w": resolved,
        "stage": "auto_bimodal",
        "confidence": confidence,
        "n_samples": int(len(y)),
        "otsu_valley_w": round(y_otsu, 3),
        "otsu_eta": round(otsu_eta, 4),
        "y_off_p98": round(y_off_p98, 3),
        "y_off_p99": round(y_off_p99, 3),
        "y_on_p05": round(y_on_p05, 3),
        "y_on_p10": round(y_on_p10, 3),
        "y_on_p50": round(y_on_p50, 3),
        "valley_density": round(valley_density, 4),
        "on_ratio": round(on_ratio, 4),
        "reasons": reasons,
    }


def resolve_train_on_thr_w(user_key: str, user_cfg: dict,
                           target_train_window: pd.Series | np.ndarray) -> tuple[float, dict]:
    """解析训练阶段 on_thr_w：显式数值优先；配置为 'auto' 时自动探测并输出黄/红高亮日志。"""
    cfg_val = user_cfg.get("on_thr_w", 10.0)
    prov = (user_cfg.get("_provenance") or {}).get("on_thr_w", "default(硬编码)")
    if isinstance(cfg_val, str) and cfg_val.strip().lower() == "auto":
        resolved, decision = resolve_auto_on_thr_w(target_train_window, fallback_thr_w=10.0)
        decision["provenance"] = prov
        if decision.get("confidence") == "high":
            log.warning("%s", yellow_text(
                f"[{user_key}] [AUTO-ON-THR] on_thr_w=auto 自动定阈生效 -> "
                f"on_thr_w={resolved:.4g} (双峰分离度 eta={decision.get('otsu_eta')}, "
                f"待机P99={decision.get('y_off_p99')}, 工况P10={decision.get('y_on_p10')}, "
                f"开机率={decision.get('on_ratio', 0.0) * 100:.1f}%)"
            ))
        else:
            log.warning("%s", red_text(
                f"[{user_key}] [AUTO-ON-THR-WARN] on_thr_w=auto 触发低分离度/多档位歧义告警 "
                f"({'; '.join(decision.get('reasons', []))}) -> 暂取保守值 "
                f"on_thr_w={resolved:.4g}，请结合设备铭牌人工复核并显式固化至配置！"
            ))
        return resolved, decision

    resolved = float(cfg_val)
    return resolved, {
        "configured": resolved,
        "resolved_on_thr_w": resolved,
        "stage": "explicit",
        "confidence": "explicit",
        "provenance": prov,
        "reasons": ["EXPLICIT_OR_DEFAULT_NUMERIC"],
    }


def _eval_chain_candidate(y_true: np.ndarray, pred: np.ndarray,
                          idx: pd.DatetimeIndex | None,
                          on_thr_w: float, cand_thr: float,
                          post_min_on: int, post_fill_off: int) -> dict:
    """在给定评估集上计算单个候选 decision_thr_w 的完整判决链指标（含全关日 FP）。"""
    t_on = y_true >= on_thr_w
    st = postprocess_state(pred, cand_thr, post_min_on, post_fill_off)
    tp = int((st & t_on).sum())
    fp = int((st & ~t_on).sum())
    fn = int((~st & t_on).sum())
    tn = int((~st & ~t_on).sum())
    prec = tp / (tp + fp) if (tp + fp) > 0 else (1.0 if fn == 0 else 0.0)
    rec = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    f1 = (2.0 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0

    off_day_fp = fp
    if idx is not None and len(idx) == len(t_on) and len(idx) > 0:
        d_idx = pd.DatetimeIndex(idx)
        day_on = pd.Series(t_on, index=d_idx).groupby(d_idx.normalize()).transform("max").to_numpy()
        off_day_fp = int((st[~day_on]).sum())
    return {
        "thr": round(float(cand_thr), 4),
        "f1": round(float(f1), 4),
        "precision": round(float(prec), 4),
        "recall": round(float(rec), 4),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "off_day_fp": off_day_fp,
    }


def resolve_auto_decision_thr_w(
    y_train: np.ndarray,
    y_val: np.ndarray,
    pred_val: np.ndarray,
    val_index: pd.DatetimeIndex | None,
    on_thr_w: float,
    post_min_on: int = 1,
    post_fill_off: int = 3,
    pred_train: np.ndarray | None = None,
    train_index: pd.DatetimeIndex | None = None,
    calibrated_model: str = "",
    pbus_mode: str = "raw",
    upper_guard_override: float | None = None,
) -> tuple[float, dict]:
    """在选定模型的 val 验证集上执行带三重护栏与抗漂移裕度的零泄漏 decision_thr_w 校准。

    三重护栏 + scaled 抗漂移裕度：
    1. 召回率硬下限：val_recall >= 0.95（宁留少量虚报，绝不切穿真开机主干）；
    2. 物理幅度硬上限：decision_thr_w <= max(on_thr_w, 0.80 * P10(y_on))（放行复合多路 400kW 稳健点，拦截 500kW 悬崖）；
    3. 保守最小增益门：若相比基准 on_thr_w 的 val_F1 提升 < 0.003，保持 on_thr_w 不做过拟合微调；
       同档最优（0.002 内）按「全关日 FP 更少 -> 总 FP 更少 -> 离 on_thr_w 更近」优选；
    4. scaled 模式平台期抗漂移裕度：当 pbus_mode='scaled' 存在跨期均值漂移风险时，若高分平台期
       （F1 距峰值 <=0.006 且 recall >=0.965 且 FP 不增）存在更宽安全走廊，向抗漂移锚点
       min(max(1.35*P99(pred_off), 1.25*thr_min), 0.70*P10(y_on)) 靠拢，避免紧贴历史噪声上沿。
    """
    base_thr = float(on_thr_w)
    y_tr = np.asarray(y_train, dtype=np.float64).reshape(-1)
    y_va = np.asarray(y_val, dtype=np.float64).reshape(-1)
    p_va = np.asarray(pred_val, dtype=np.float64).reshape(-1)

    eval_split = "val"
    eval_y, eval_p, eval_idx = y_va, p_va, val_index
    fallback_reasons: list[str] = []

    if len(eval_y) == 0 or (eval_y >= base_thr).sum() == 0:
        if pred_train is not None and len(y_tr) > 0 and (y_tr >= base_thr).sum() > 0:
            eval_split = "train_fallback"
            eval_y = y_tr
            eval_p = np.asarray(pred_train, dtype=np.float64).reshape(-1)
            eval_idx = train_index
            fallback_reasons.append("VAL_NO_POSITIVE_FALLBACK_TRAIN")
        else:
            return base_thr, {
                "configured": "auto",
                "resolved_decision_thr_w": base_thr,
                "stage": "auto_fallback_on_thr",
                "confidence": "low",
                "calibrated_model": calibrated_model,
                "eval_split": eval_split,
                "reasons": ["NO_POSITIVE_TARGET_FALLBACK_ON_THR"],
            }

    y_on_ref = y_tr[y_tr >= base_thr]
    if len(y_on_ref) == 0:
        y_on_ref = eval_y[eval_y >= base_thr]
    y_on_p10 = float(np.quantile(y_on_ref, 0.10))
    if upper_guard_override is not None and np.isfinite(upper_guard_override) and upper_guard_override > 0:
        upper_guard_w = max(base_thr, float(upper_guard_override))
    else:
        upper_guard_w = max(base_thr, UPPER_ON_P10_RATIO * y_on_p10)
    min_cand_w = max(0.001, 0.60 * base_thr)

    raw_cands: set[float] = {round(base_thr, 4)}
    for g in STANDARD_DECISION_GRID:
        if min_cand_w - 1e-9 <= g <= upper_guard_w + 1e-9:
            raw_cands.add(round(float(g), 4))
    # 自适应补充关态预测噪声分位点（兼顾小功率 1~5kW 设备与大功率工业设备）
    off_preds = eval_p[(eval_y < base_thr) & np.isfinite(eval_p) & (eval_p > 0)]
    if len(off_preds) >= 8:
        for q in (0.75, 0.90, 0.95, 0.98, 0.99):
            qv = round(float(np.quantile(off_preds, q)) * 1.05, 2)
            if min_cand_w <= qv <= upper_guard_w:
                raw_cands.add(qv)

    cands = sorted(c for c in raw_cands if 0.001 <= c <= 5000.0)
    if not cands:
        cands = [round(base_thr, 4)]

    rows = [
        _eval_chain_candidate(eval_y, eval_p, eval_idx, base_thr, c, post_min_on, post_fill_off)
        for c in cands
    ]
    baseline_row = min(rows, key=lambda r: abs(r["thr"] - base_thr))
    feasible = [r for r in rows if r["recall"] >= MIN_VAL_RECALL]

    reasons = list(fallback_reasons)
    confidence = "high" if not fallback_reasons else "low"
    drift_anchor_w: float | None = None

    if not feasible:
        confidence = "low"
        reasons.append(f"RECALL_BELOW_{MIN_VAL_RECALL:.2f}_GUARD")
        best_any = max(rows, key=lambda r: (r["f1"], r["recall"], -abs(r["thr"] - base_thr)))
        selected = best_any if best_any["f1"] > baseline_row["f1"] + 1e-6 else baseline_row
    else:
        best_f1 = max(r["f1"] for r in feasible)
        if baseline_row["recall"] >= MIN_VAL_RECALL and (best_f1 - baseline_row["f1"]) < MIN_DECISION_F1_GAIN:
            selected = baseline_row
            reasons.append("KEEP_ON_THR_NEAR_OPTIMAL")
        else:
            top_tier = [r for r in feasible if r["f1"] >= best_f1 - TIE_F1_TOL]
            selected = min(
                top_tier,
                key=lambda r: (r["off_day_fp"], r["fp"], abs(r["thr"] - base_thr), -r["f1"]),
            )
            reasons.append("VAL_CHAIN_OPTIMIZED")

            # Step 1(b): pbus_mode="scaled" 平台期抗漂移裕度锚定（预防跨月基荷均值上移击穿底噪上沿）
            if pbus_mode == "scaled" and len(off_preds) > 0:
                off_p99 = float(np.quantile(off_preds, 0.99))
                raw_anchor = max(1.35 * off_p99, selected["thr"] * 1.25)
                drift_anchor_w = round(min(raw_anchor, 0.70 * y_on_p10, upper_guard_w), 3)
                if drift_anchor_w > selected["thr"] + 1e-6:
                    margin_pool = [
                        r for r in feasible
                        if r["thr"] >= selected["thr"] - 1e-9
                        and r["recall"] >= SCALED_PLATEAU_MIN_RECALL
                        and r["f1"] >= best_f1 - SCALED_PLATEAU_F1_TOL
                        and r["off_day_fp"] <= selected["off_day_fp"]
                        and r["fp"] <= selected["fp"]
                    ]
                    if margin_pool:
                        anchored = min(
                            margin_pool,
                            key=lambda r: (r["off_day_fp"], r["fp"], abs(r["thr"] - drift_anchor_w), -r["f1"]),
                        )
                        if anchored["thr"] > selected["thr"] + 1e-6:
                            selected = anchored
                            reasons.append(f"SCALED_ANTI_DRIFT_MARGIN(anchor={drift_anchor_w:.1f})")

    if selected["f1"] < 0.50:
        confidence = "low"
        reasons.append(f"LOW_VAL_CHAIN_F1({selected['f1']:.4f}<0.50)")

    resolved = float(selected["thr"])
    return resolved, {
        "configured": "auto",
        "resolved_decision_thr_w": resolved,
        "stage": "auto_val_calibrated",
        "confidence": confidence,
        "calibrated_model": calibrated_model,
        "pbus_mode": pbus_mode,
        "eval_split": eval_split,
        "on_thr_w": base_thr,
        "y_on_p10": round(y_on_p10, 3),
        "upper_guard_w": round(upper_guard_w, 3),
        "scaled_drift_anchor_w": drift_anchor_w,
        "min_recall_guard": MIN_VAL_RECALL,
        "val_f1_at_on_thr": baseline_row["f1"],
        "val_off_fp_at_on_thr": baseline_row["off_day_fp"],
        "val_f1_selected": selected["f1"],
        "val_recall_selected": selected["recall"],
        "val_precision_selected": selected["precision"],
        "val_off_fp_selected": selected["off_day_fp"],
        "candidates": rows,
        "reasons": reasons,
    }


def select_calibration_model_zero_leakage(
    user_cfg: dict,
    base_cfg: dict,
    res_by_split: dict[str, dict],
    split_bands: dict[str, dict[str, float]],
    on_thr_w: float,
) -> str:
    """零泄漏选择训练阶段校准 decision_thr_w 的目标模型（严禁查看 test 指标）。"""
    all_models = list(res_by_split.keys())
    if not all_models:
        return ""
    explicit_model = user_cfg.get("infer_model") or base_cfg.get("infer_model")
    if explicit_model and explicit_model in res_by_split:
        return str(explicit_model)

    # 仅使用 val 集综合排名（过滤 val 预测坍缩模型），无 val 时回退 train
    val_results = {
        m: by["val"]
        for m, by in res_by_split.items()
        if "val" in by and split_bands.get(m, {}).get("val", on_thr_w) >= on_thr_w
    }
    if not val_results:
        val_results = {m: by["val"] for m, by in res_by_split.items() if "val" in by}
    if not val_results:
        val_results = {m: by["train"] for m, by in res_by_split.items() if "train" in by}
    if val_results:
        best_val = summarize(build_comparison_table(val_results)).get("overall_best")
        if best_val:
            return str(best_val)
    return str(all_models[0])


def log_auto_decision_thr_train(user_key: str, decision: dict) -> None:
    """当训练阶段 decision_thr_w 配置为 'auto' 时输出亮黄（正常）或亮红（风险）提示日志。"""
    if decision.get("configured") != "auto":
        return
    resolved = float(decision["resolved_decision_thr_w"])
    model = decision.get("calibrated_model") or "unknown"
    f1_base = decision.get("val_f1_at_on_thr")
    f1_sel = decision.get("val_f1_selected")
    rec_sel = decision.get("val_recall_selected")
    off_base = decision.get("val_off_fp_at_on_thr")
    off_sel = decision.get("val_off_fp_selected")
    up_g = decision.get("upper_guard_w")
    reasons = "; ".join(decision.get("reasons", []))

    if decision.get("confidence") == "high":
        log.warning("%s", yellow_text(
            f"[{user_key}] [AUTO-DEC-THR] decision_thr_w=auto 验证集自动校准生效 -> "
            f"decision_thr_w={resolved:.4g} (model={model}, val_F1: {f1_base}->{f1_sel}, "
            f"val_recall={rec_sel}, 全关日FP: {off_base}->{off_sel}, 护栏上限={up_g}, {reasons})"
        ))
    else:
        log.warning("%s", red_text(
            f"[{user_key}] [AUTO-DEC-THR-WARN] decision_thr_w=auto 触发验证集退化/护栏告警 "
            f"({reasons}) -> 锁定保守值 decision_thr_w={resolved:.4g} "
            f"(model={model}, val_F1={f1_sel}, val_recall={rec_sel})！"
        ))


def resolve_infer_thresholds(
    user_key: str,
    user_cfg: dict,
    meta: dict,
    model_name: str | None = None,
) -> tuple[float, float, dict]:
    """推理阶段统一解析 (on_thr_w, decision_thr_w)，严格遵循显式优先 + 训练产物继承 + 黄/红日志。"""
    prov = user_cfg.get("_provenance") or {}
    cfg_on = user_cfg.get("on_thr_w", 10.0)
    prov_on = prov.get("on_thr_w", "default(硬编码)")
    meta_on = meta.get("on_thr_w")
    trained_on = float(meta_on) if isinstance(meta_on, (int, float)) else 10.0

    # 1) on_thr_w 解析
    if isinstance(cfg_on, str) and cfg_on.strip().lower() == "auto":
        on_thr = trained_on
        on_source = "inherited_train_meta(auto)"
        log.warning("%s", yellow_text(
            f"[{user_key}] [AUTO-ON-THR-INFER] on_thr_w=auto 推理继承训练产物阈值 -> "
            f"on_thr_w={on_thr:.4g}"
        ))
    elif prov_on == "default(硬编码)":
        on_thr = trained_on
        on_source = "inherited_train_meta(default)"
        if meta.get("on_thr_w_configured") == "auto" and abs(on_thr - 10.0) > 1e-6:
            log.warning("%s", yellow_text(
                f"[{user_key}] [AUTO-ON-THR-INFER] 推理未显式配置 on_thr_w，自动沿用训练 auto 阈值 -> "
                f"on_thr_w={on_thr:.4g}"
            ))
    else:
        on_thr = float(cfg_on)
        on_source = f"explicit({prov_on})"
        if isinstance(meta_on, (int, float)) and abs(on_thr - float(meta_on)) > 1e-6:
            log.warning("%s", red_text(
                f"[{user_key}] [ON-THR-MISMATCH] 推理显式配置 on_thr_w={on_thr:.4g} 与训练产物 "
                f"meta.on_thr_w={float(meta_on):.4g} 不一致！现按显式优先采用 {on_thr:.4g}，"
                "建议加 --force 重新训练以统一口径！"
            ))

    # 2) decision_thr_w 解析
    cfg_dec = user_cfg.get("decision_thr_w", "auto")
    prov_dec = prov.get("decision_thr_w", "default(硬编码)")
    model_dec_map = meta.get("model_decision_thr_w") or {}
    trained_dec_ref = (
        float(model_dec_map[model_name])
        if (model_name and model_name in model_dec_map)
        else (float(meta["decision_thr_w"]) if isinstance(meta.get("decision_thr_w"), (int, float)) else None)
    )
    if isinstance(cfg_dec, (int, float)) and not isinstance(cfg_dec, bool):
        dec_thr = float(cfg_dec)
        dec_source = f"explicit({prov_dec})"
        if trained_dec_ref is not None and abs(dec_thr - trained_dec_ref) > 1e-6:
            log.warning("%s", yellow_text(
                f"[{user_key}] [DEC-THR-OVERRIDE] 推理显式配置 decision_thr_w={dec_thr:.4g} "
                f"覆盖训练产物阈值 {trained_dec_ref:.4g}（显式优先）；如需零重训同步统一双侧产物可执行: "
                f"python scripts/auto_run_steps2to5.py --user-key {user_key} --stage sync-thr --decision-thr {dec_thr:.4g}"
            ))
    elif isinstance(cfg_dec, str) and cfg_dec.strip().lower() == "on_thr":
        dec_thr = float(on_thr)
        dec_source = f"explicit_on_thr({prov_dec})"
    else:
        if model_name and model_name in model_dec_map:
            dec_thr = float(model_dec_map[model_name])
            dec_source = f"meta.model_decision_thr_w[{model_name}]"
        elif isinstance(meta.get("decision_thr_w"), (int, float)):
            dec_thr = float(meta["decision_thr_w"])
            dec_source = "meta.decision_thr_w"
        else:
            dec_thr = float(on_thr)
            dec_source = "legacy_fallback_on_thr_w"
        log.warning("%s", yellow_text(
            f"[{user_key}] [AUTO-DEC-THR-INFER] decision_thr_w=auto 推理阈值生效 -> "
            f"decision_thr_w={dec_thr:.4g} (来源={dec_source}, model={model_name or 'auto'}, "
            f"on_thr_w={on_thr:.4g})"
        ))

    return on_thr, dec_thr, {
        "on_thr_w": on_thr,
        "on_thr_w_configured": cfg_on,
        "on_thr_w_source": on_source,
        "decision_thr_w": dec_thr,
        "decision_thr_w_configured": cfg_dec,
        "decision_thr_w_source": dec_source,
    }


def check_offline_decision_drift(
    user_key: str,
    y_infer_true: np.ndarray,
    pred_infer: np.ndarray,
    infer_index: pd.DatetimeIndex,
    on_thr_w: float,
    current_dec_thr_w: float,
    post_min_on: int,
    post_fill_off: int,
    model_name: str,
    upper_guard_w: float | None = None,
) -> dict | None:
    """只读离线跨期漂移诊断：存在推理分路真值时检测 decision_thr_w 是否发生显著跨月偏移。

    严格零泄漏：本函数在 inference_result.csv 生成完毕后只读运行，绝不修改任何推理预测结果；
    优先使用训练集物理上限 upper_guard_w（0.80*P10(y_train_on)），避免短期推理子集分位数偏低漏扫高档阈值（如 2844 的 500kW）；
    同时覆盖「低占空比 F1 跌破 0.95」与「高占空比大户（如 733）虚报激增且重扫可大幅削减 FP>=60%」两类场景。
    """
    y_arr = np.asarray(y_infer_true, dtype=np.float64).reshape(-1)
    p_arr = np.asarray(pred_infer, dtype=np.float64).reshape(-1)
    if len(y_arr) < 16 or (y_arr >= on_thr_w).sum() == 0:
        return None

    cur = _eval_chain_candidate(
        y_arr, p_arr, infer_index, on_thr_w, current_dec_thr_w, post_min_on, post_fill_off
    )
    rec_thr, rec_info = resolve_auto_decision_thr_w(
        y_train=y_arr,
        y_val=y_arr,
        pred_val=p_arr,
        val_index=infer_index,
        on_thr_w=on_thr_w,
        post_min_on=post_min_on,
        post_fill_off=post_fill_off,
        calibrated_model=model_name,
        upper_guard_override=upper_guard_w,
    )
    gain = float(rec_info.get("val_f1_selected", 0.0)) - float(cur["f1"])
    shift = abs(rec_thr - current_dec_thr_w)
    rec_row = min(
        rec_info.get("candidates", [cur]),
        key=lambda r: abs(float(r["thr"]) - rec_thr),
    )
    rec_fp = int(rec_row.get("fp", cur["fp"]))
    fp_cut_ratio = ((cur["fp"] - rec_fp) / max(cur["fp"], 1)) if cur["fp"] > 0 else 0.0

    shift_significant = shift >= max(15.0, 0.20 * current_dec_thr_w)
    low_f1_drift = cur["f1"] < 0.95 and gain >= 0.02
    high_duty_fp_drift = cur["fp"] >= 15 and fp_cut_ratio >= 0.60 and gain >= 0.004

    if shift_significant and (low_f1_drift or high_duty_fp_drift):
        advice = {
            "drift_detected": True,
            "current_decision_thr_w": current_dec_thr_w,
            "current_infer_chain_f1": cur["f1"],
            "current_infer_recall": cur["recall"],
            "current_infer_fp": cur["fp"],
            "current_infer_off_fp": cur["off_day_fp"],
            "recommended_decision_thr_w": rec_thr,
            "recommended_infer_chain_f1": rec_info["val_f1_selected"],
            "recommended_infer_recall": rec_info["val_recall_selected"],
            "recommended_infer_fp": rec_fp,
            "recommended_infer_off_fp": rec_info["val_off_fp_selected"],
            "f1_potential_gain": round(gain, 4),
            "fp_cut_ratio": round(fp_cut_ratio, 4),
        }
        log.warning("%s", red_text(
            f"[{user_key}] [AUTO-DEC-THR-DRIFT] 离线评估检测到跨期基线漂移：当前 "
            f"decision_thr_w={current_dec_thr_w:.4g} 下 infer 链 F1={cur['f1']:.4f} "
            f"(FP={cur['fp']}, 全关日FP={cur['off_day_fp']})；离线重校准建议更新为 "
            f"decision_thr_w={rec_thr:.4g} (F1={rec_info['val_f1_selected']:.4f}, "
            f"recall={rec_info['val_recall_selected']:.4f}, FP={rec_fp}, "
            f"全关日FP={rec_info['val_off_fp_selected']})！"
        ))
        return advice
    return {
        "drift_detected": False,
        "current_decision_thr_w": current_dec_thr_w,
        "current_infer_chain_f1": cur["f1"],
        "current_infer_fp": cur["fp"],
    }


def resolve_joint_val_infer_decision_thr_w(
    train_dir: Path,
    infer_dir: Path,
) -> tuple[float | None, dict]:
    """双链（train/val + infer 离线评估）联合校准最优 decision_thr_w。

    严格遵守《调参实战手册》Step 4 铁律与测试集隔离不变量：
    - 严禁读取 train_predictions.csv 的 test 折（保持留出测试集独立纯洁）；
    - 若 inference_result.csv 无有效分路真值（纯盲推），立即返回 (None, {'skipped': ...})；
    - 在候选走廊 [0.6*on_thr_w, max(on_thr_w, 0.80*P10(y_train_on))] 内同时满足：
      ① infer_recall >= 0.95；
      ② val_recall >= 0.95 且 val_F1 >= val_peak_F1 - 0.02（防止顾头不顾尾）；
      按「infer_F1 最高（0.0025 同档内优先 infer 全关日 FP 更少 -> infer 总 FP 更少 -> val 全关日 FP 更少 -> 离现值更近）」选定。
    """
    tr_meta_path = train_dir / "meta.json"
    inf_meta_path = infer_dir / "meta.json"
    tp_csv = train_dir / "predictions" / "train_predictions.csv"
    inf_csv = infer_dir / INFERENCE_RESULT_REL
    if not (tr_meta_path.exists() and inf_meta_path.exists() and tp_csv.exists() and inf_csv.exists()):
        return None, {"skipped": "MISSING_ARTIFACTS"}

    tr_meta = json.loads(tr_meta_path.read_text(encoding="utf-8"))
    inf_meta = json.loads(inf_meta_path.read_text(encoding="utf-8"))
    inf_df = pd.read_csv(inf_csv)
    valid_mask = inf_df["target"].notna() & inf_df["pred"].notna()
    if int(valid_mask.sum()) < 16:
        return None, {"skipped": "NO_INFER_GROUND_TRUTH"}

    # 遵循 infer/excluded_days.json 剔除无效天（与 offline_metrics/metrics_daily_chain 同口径）
    excl_path = infer_dir / "excluded_days.json"
    if excl_path.exists():
        excl_days = set(json.loads(excl_path.read_text(encoding="utf-8")).get("excluded_days") or [])
        if excl_days:
            day_str = inf_df["timestamp"].astype(str).str.slice(0, 10)
            valid_mask = valid_mask & (~day_str.isin(excl_days))
    have_df = inf_df.loc[valid_mask].copy()
    on_thr = float(inf_df["on_thr_w"].iloc[0])
    cur_dec = float(inf_df["decision_thr_w"].iloc[0])
    y_inf = have_df["target"].to_numpy(dtype=np.float64)
    p_inf = have_df["pred"].to_numpy(dtype=np.float64)
    idx_inf = pd.DatetimeIndex(pd.to_datetime(have_df["timestamp"]))
    if (y_inf >= on_thr).sum() == 0:
        return None, {"skipped": "NO_POSITIVE_TARGET_IN_INFER"}

    model_name = str(inf_meta.get("model") or tr_meta.get("best_model") or "")
    pred_col = f"pred_{model_name}"
    tp_df = pd.read_csv(tp_csv)
    if pred_col not in tp_df.columns:
        pred_cols = [c for c in tp_df.columns if c.startswith("pred_") and not c.startswith("pred_state_")]
        if not pred_cols:
            return None, {"skipped": "NO_TRAIN_PRED_COL"}
        pred_col = pred_cols[0]

    # 推断 post_min_on / post_fill_short_off（优先 meta -> metrics_daily_chain -> 默认 1/3）
    post_min_on = int(inf_meta.get("post_min_on", tr_meta.get("post_min_on", 1)))
    post_fill_off = int(inf_meta.get("post_fill_short_off", tr_meta.get("post_fill_short_off", 3)))
    chain_csv = infer_dir / "metrics_daily_chain.csv"
    if "post_min_on" not in inf_meta and chain_csv.exists():
        cdf = pd.read_csv(chain_csv)
        if len(cdf) > 0 and "post_min_on" in cdf.columns:
            post_min_on = int(cdf["post_min_on"].iloc[0])
            post_fill_off = int(cdf["post_fill_short_off"].iloc[0])

    # 严格只取 train 与 val 折，绝不读取 test 折
    tr_sub = tp_df[tp_df["split"] == "train"].sort_values("timestamp")
    va_sub = tp_df[tp_df["split"] == "val"].sort_values("timestamp")
    y_tr = tr_sub["target"].to_numpy(dtype=np.float64)
    y_va = va_sub["target"].to_numpy(dtype=np.float64) if len(va_sub) > 0 else y_tr
    p_va = va_sub[pred_col].to_numpy(dtype=np.float64) if len(va_sub) > 0 else tr_sub[pred_col].to_numpy(dtype=np.float64)
    idx_va = (
        pd.DatetimeIndex(pd.to_datetime(va_sub["timestamp"]))
        if len(va_sub) > 0
        else pd.DatetimeIndex(pd.to_datetime(tr_sub["timestamp"]))
    )

    y_on_ref = y_tr[y_tr >= on_thr]
    if len(y_on_ref) == 0:
        y_on_ref = y_inf[y_inf >= on_thr]
    y_on_p10 = float(np.quantile(y_on_ref, 0.10))
    upper_guard_w = max(on_thr, UPPER_ON_P10_RATIO * y_on_p10)
    min_cand_w = max(0.001, 0.60 * on_thr)

    raw_cands: set[float] = {round(on_thr, 4), round(cur_dec, 4)}
    for g in STANDARD_DECISION_GRID:
        if min_cand_w - 1e-9 <= g <= upper_guard_w + 1e-9:
            raw_cands.add(round(float(g), 4))
    for y_s, p_s in ((y_va, p_va), (y_inf, p_inf)):
        off_p = p_s[(y_s < on_thr) & np.isfinite(p_s) & (p_s > 0)]
        if len(off_p) >= 8:
            for q in (0.75, 0.90, 0.95, 0.98, 0.99):
                qv = round(float(np.quantile(off_p, q)) * 1.05, 2)
                if min_cand_w <= qv <= upper_guard_w:
                    raw_cands.add(qv)
    cands = sorted(c for c in raw_cands if 0.001 <= c <= 5000.0)

    val_map = {
        c: _eval_chain_candidate(y_va, p_va, idx_va, on_thr, c, post_min_on, post_fill_off)
        for c in cands
    }
    inf_map = {
        c: _eval_chain_candidate(y_inf, p_inf, idx_inf, on_thr, c, post_min_on, post_fill_off)
        for c in cands
    }

    val_feas = [c for c in cands if val_map[c]["recall"] >= MIN_VAL_RECALL]
    val_peak_f1 = max((val_map[c]["f1"] for c in val_feas), default=0.0)
    inf_feas = [c for c in cands if inf_map[c]["recall"] >= MIN_VAL_RECALL]

    # 计算无历史 val 否决约束的纯 infer 链最优候选（严格受 infer_recall >= 0.95 与训练物理上限 upper_guard_w 保护）
    pure_inf_c: float | None = None
    if inf_feas:
        pure_inf_peak_f1 = max(inf_map[c]["f1"] for c in inf_feas)
        pure_top = [c for c in inf_feas if inf_map[c]["f1"] >= pure_inf_peak_f1 - 0.0025]
        pure_inf_c = min(
            pure_top,
            key=lambda c: (
                inf_map[c]["off_day_fp"],
                inf_map[c]["fp"],
                -inf_map[c]["f1"],
                -inf_map[c]["recall"],
                abs(c - cur_dec),
            ),
        )

    joint_feas = [
        c for c in cands
        if inf_map[c]["recall"] >= MIN_VAL_RECALL
        and (not val_feas or (val_map[c]["recall"] >= MIN_VAL_RECALL and val_map[c]["f1"] >= val_peak_f1 - 0.02))
    ]
    confidence = "high"
    reasons: list[str] = []
    heteropeak_override = False
    val_capped_candidate: float | None = None

    if not joint_feas:
        # 极端跨期偏移兜底：放宽 val_F1 下限但仍尝试兼顾 infer_recall >= 0.95 与 val_recall >= 0.93
        joint_feas = [
            c for c in cands
            if inf_map[c]["recall"] >= MIN_VAL_RECALL and val_map[c]["recall"] >= 0.93
        ]
        if joint_feas:
            confidence = "low"
            reasons.append("RELAXED_VAL_F1_FLOOR_FOR_SEVERE_SHIFT")
    if not joint_feas:
        joint_feas = list(inf_feas)
        if joint_feas:
            confidence = "low"
            heteropeak_override = True
            reasons.append("INFER_RECALL_ONLY_FALLBACK")

    if not joint_feas:
        confidence = "low"
        reasons.append("RECALL_BELOW_095_GUARD")
        best_c = max(cands, key=lambda c: (inf_map[c]["f1"], inf_map[c]["recall"], -abs(c - cur_dec)))
    else:
        best_inf_f1 = max(inf_map[c]["f1"] for c in joint_feas)
        cur_inf = inf_map[round(cur_dec, 4)]
        min_inf_fp = min(inf_map[c]["fp"] for c in joint_feas if inf_map[c]["f1"] >= best_inf_f1 - 0.0025)
        f1_gain = best_inf_f1 - cur_inf["f1"]
        fp_saved = cur_inf["fp"] - min_inf_fp

        if (
            round(cur_dec, 4) in joint_feas
            and f1_gain < 0.002
            and fp_saved < max(3, int(0.20 * cur_inf["fp"]))
        ):
            joint_best_c = round(cur_dec, 4)
            joint_reason = "DUAL_CHAIN_CONSISTENT_KEEP"
        else:
            top_cands = [c for c in joint_feas if inf_map[c]["f1"] >= best_inf_f1 - 0.0025]
            joint_best_c = min(
                top_cands,
                key=lambda c: (
                    inf_map[c]["off_day_fp"],
                    inf_map[c]["fp"],
                    -inf_map[c]["f1"],
                    val_map[c]["off_day_fp"],
                    abs(c - cur_dec),
                ),
            )
            joint_reason = "DUAL_CHAIN_JOINT_OPTIMIZED"

        # 升级 ①（《调参实战手册》Step 4 铁律——双链异峰以 infer 链为准）：
        # 当历史季节 val 召回贴线（如 2844: val 仅放行 [6,20]）把阈值卡死在低位，
        # 而当期 infer 因基荷偏移在更高阈值（<= 0.80*P10(y_train_on) 且 infer_recall >= 0.95）
        # 可显著提升 infer_F1 (>=0.02) 或大幅削减虚报 (FP>=15 且砍掉 >=50%) 时，解除历史 val 一票否决。
        if pure_inf_c is not None:
            j_inf = inf_map[joint_best_c]
            p_inf_row = inf_map[pure_inf_c]
            shift_vs_joint = abs(pure_inf_c - joint_best_c)
            f1_gain_vs_joint = p_inf_row["f1"] - j_inf["f1"]
            fp_cut_vs_joint = (
                (j_inf["fp"] - p_inf_row["fp"]) / max(j_inf["fp"], 1)
                if j_inf["fp"] > 0 else 0.0
            )
            if (
                shift_vs_joint >= max(15.0, 0.20 * max(joint_best_c, 1.0))
                and (
                    f1_gain_vs_joint >= 0.02
                    or (j_inf["fp"] >= 15 and fp_cut_vs_joint >= 0.50 and f1_gain_vs_joint >= 0.005)
                )
            ):
                val_capped_candidate = float(joint_best_c)
                best_c = pure_inf_c
                heteropeak_override = True
                reasons.append(
                    f"CROSS_SEASON_INFER_DOMINANT_OVERRIDE("
                    f"val_capped={joint_best_c:.4g}->infer_opt={pure_inf_c:.4g},"
                    f"infer_f1_gain={f1_gain_vs_joint:+.4f},fp_cut={fp_cut_vs_joint:.1%})"
                )
            else:
                best_c = joint_best_c
                reasons.append(joint_reason)
        else:
            best_c = joint_best_c
            reasons.append(joint_reason)

    cur_key = round(cur_dec, 4)
    return float(best_c), {
        "stage": "joint_val_infer_calibrated",
        "confidence": confidence,
        "heteropeak_override": heteropeak_override,
        "val_capped_candidate_w": val_capped_candidate,
        "pure_infer_optimal_w": pure_inf_c,
        "model": model_name,
        "on_thr_w": on_thr,
        "previous_decision_thr_w": cur_dec,
        "resolved_decision_thr_w": float(best_c),
        "upper_guard_w": round(upper_guard_w, 3),
        "y_on_p10": round(y_on_p10, 3),
        "post_min_on": post_min_on,
        "post_fill_short_off": post_fill_off,
        "val_before": val_map[cur_key],
        "val_after": val_map[best_c],
        "infer_before": inf_map[cur_key],
        "infer_after": inf_map[best_c],
        "reasons": reasons,
    }


def sync_decision_thr_artifacts(
    user_key: str,
    train_dir: Path | None,
    infer_dir: Path | None,
    new_dec_thr: float,
    reason: str = "manual_sync",
    sync_details: dict | None = None,
) -> dict:
    """零模型重训原子同步刷新 train 与 infer 全套后处理判决链产物，确保 T1-T4 / I1-I10 100% 一致。"""
    dec_val = float(new_dec_thr)
    ts_now = datetime.now().isoformat(timespec="seconds")
    updated: dict[str, str] = {}
    old_train_dec: float | None = None
    old_infer_dec: float | None = None

    # ---------------- 1) 同步刷新 train_dir ----------------
    if train_dir is not None and (train_dir / "meta.json").exists():
        tr_meta_path = train_dir / "meta.json"
        tr_meta = json.loads(tr_meta_path.read_text(encoding="utf-8"))
        on_thr = float(tr_meta.get("on_thr_w", 10.0))
        old_train_dec = (
            float(tr_meta["decision_thr_w"])
            if isinstance(tr_meta.get("decision_thr_w"), (int, float))
            else None
        )
        post_min_on = int(tr_meta.get("post_min_on", 1))
        post_fill_off = int(tr_meta.get("post_fill_short_off", 3))

        tr_chain_csv = train_dir / "metrics_daily_chain.csv"
        tr_daily_csv = train_dir / "metrics_daily.csv"
        metric_names = ["mae", "rmse", "r2", "sae", "f1", "accuracy", "precision", "recall", "tp", "fp", "fn", "tn"]
        if tr_daily_csv.exists():
            mdf_d = pd.read_csv(tr_daily_csv, nrows=1)
            inferred_m = [
                c for c in mdf_d.columns
                if c not in ("model", "split", "date", "n_points", "state_thr_w")
            ]
            if inferred_m:
                metric_names = inferred_m
        if "post_min_on" not in tr_meta and tr_chain_csv.exists():
            cdf_old = pd.read_csv(tr_chain_csv, nrows=1)
            if len(cdf_old) > 0 and "post_min_on" in cdf_old.columns:
                post_min_on = int(cdf_old["post_min_on"].iloc[0])
                post_fill_off = int(cdf_old["post_fill_short_off"].iloc[0])

        tp_csv = train_dir / "predictions" / "train_predictions.csv"
        if tp_csv.exists():
            preds = pd.read_csv(tp_csv)
            if old_train_dec is None and "decision_thr_w" in preds.columns and len(preds) > 0:
                old_train_dec = float(preds["decision_thr_w"].iloc[0])
            preds["decision_thr_w"] = dec_val
            models_in_pred = [
                c[len("pred_state_"):] for c in preds.columns if c.startswith("pred_state_")
            ]
            chain_frames: list[pd.DataFrame] = []
            for mname in models_in_pred:
                p_col = f"pred_{mname}"
                s_col = f"pred_state_{mname}"
                for sp in ("train", "val", "test"):
                    mask_sp = preds["split"] == sp
                    if not mask_sp.any():
                        continue
                    sub = preds.loc[mask_sp].sort_values("timestamp")
                    p_arr = sub[p_col].to_numpy(dtype=np.float64)
                    y_arr = sub["target"].to_numpy(dtype=np.float64)
                    idx_sp = pd.DatetimeIndex(pd.to_datetime(sub["timestamp"]))
                    st_arr = postprocess_state(p_arr, dec_val, post_min_on, post_fill_off).astype(int)
                    preds.loc[sub.index, s_col] = st_arr
                    dc = evaluate_daily_chain(
                        y_arr[:, None], p_arr[:, None], st_arr, idx_sp, metric_names, on_thr_w=on_thr
                    )
                    dc.insert(0, "split", sp)
                    dc.insert(0, "model", mname)
                    dc["state_thr_w"] = on_thr
                    dc["decision_thr_w"] = dec_val
                    dc["post_min_on"] = post_min_on
                    dc["post_fill_short_off"] = post_fill_off
                    chain_frames.append(dc)
            preds.to_csv(tp_csv, index=False, encoding="utf-8")
            if chain_frames:
                pd.concat(chain_frames, ignore_index=True).to_csv(tr_chain_csv, index=False, encoding="utf-8")

            # 同步刷新 state_strategy_metrics.csv（保留 raw_on_thr 对照行不动，重算 decision+runs 行）
            strat_csv = train_dir / "state_strategy_metrics.csv"
            test_sub = preds[preds["split"] == "test"].sort_values("timestamp")
            if strat_csv.exists() and len(test_sub) > 0:
                sdf_old = pd.read_csv(strat_csv)
                raw_rows = sdf_old[sdf_old["strategy"] == "raw_on_thr"].to_dict("records")
                idx_te = pd.DatetimeIndex(pd.to_datetime(test_sub["timestamp"]))
                t_on_te = test_sub["target_state"].to_numpy(dtype=int).astype(bool)
                day_on = pd.Series(t_on_te, index=idx_te).groupby(idx_te.normalize()).transform("max").to_numpy()
                out_rows: list[dict] = []
                raw_by_model = {}
                for r in raw_rows:
                    raw_by_model.setdefault(r["model"], []).append(r)
                for mname in models_in_pred:
                    out_rows.extend(raw_by_model.get(mname, []))
                    st = test_sub[f"pred_state_{mname}"].to_numpy(dtype=int).astype(bool)
                    for scope, m in (("all_days", np.ones(len(st), bool)), ("on_days_only", day_on)):
                        tp = int((st[m] & t_on_te[m]).sum())
                        fp = int((st[m] & ~t_on_te[m]).sum())
                        fn = int((~st[m] & t_on_te[m]).sum())
                        prec = tp / (tp + fp) if (tp + fp) > 0 else (1.0 if fn == 0 else 0.0)
                        rec = tp / (tp + fn) if (tp + fn) > 0 else 1.0
                        f1 = (2.0 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0
                        out_rows.append({
                            "model": mname,
                            "strategy": "decision+runs",
                            "scope": scope,
                            "decision_thr_w": dec_val,
                            "post_min_on": post_min_on,
                            "post_fill_short_off": post_fill_off,
                            "f1": round(f1, 4),
                            "precision": round(prec, 4),
                            "recall": round(rec, 4),
                            "tp": tp,
                            "fp": fp,
                            "fn": fn,
                        })
                pd.DataFrame(out_rows).to_csv(strat_csv, index=False, encoding="utf-8")

        comp_md = train_dir / "comparison.md"
        if comp_md.exists():
            txt = comp_md.read_text(encoding="utf-8")
            txt_new = re.sub(
                r"decision_thr_w=[0-9.]+",
                f"decision_thr_w={dec_val:g}",
                txt,
            )
            if txt_new != txt:
                comp_md.write_text(txt_new, encoding="utf-8")

        if "decision_thr_w_val_initial" not in tr_meta and old_train_dec is not None:
            tr_meta["decision_thr_w_val_initial"] = old_train_dec
        tr_meta["decision_thr_w"] = dec_val
        model_map = dict(tr_meta.get("model_decision_thr_w") or {})
        target_model = (sync_details or {}).get("model") or tr_meta.get("best_model")
        if target_model:
            model_map[str(target_model)] = dec_val
        for m in tr_meta.get("models") or []:
            if reason == "explicit_sync":
                model_map[str(m)] = dec_val
        tr_meta["model_decision_thr_w"] = model_map
        tr_meta["decision_thr_w_sync"] = {
            "synced_at": ts_now,
            "reason": reason,
            "previous_decision_thr_w": old_train_dec,
            "synced_decision_thr_w": dec_val,
            "details": sync_details,
        }
        tr_meta_path.write_text(json.dumps(tr_meta, ensure_ascii=False, indent=2), encoding="utf-8")
        updated["train_dir"] = str(train_dir)

    # ---------------- 2) 同步刷新 infer_dir ----------------
    if infer_dir is not None and (infer_dir / INFERENCE_RESULT_REL).exists():
        inf_csv = infer_dir / INFERENCE_RESULT_REL
        inf_meta_path = infer_dir / "meta.json"
        inf_meta = json.loads(inf_meta_path.read_text(encoding="utf-8")) if inf_meta_path.exists() else {}
        df_inf = pd.read_csv(inf_csv)
        on_thr_inf = float(df_inf["on_thr_w"].iloc[0])
        old_infer_dec = float(df_inf["decision_thr_w"].iloc[0])
        model_inf = str(inf_meta.get("model") or "unknown")

        post_min_on_i = int(inf_meta.get("post_min_on", 1))
        post_fill_off_i = int(inf_meta.get("post_fill_short_off", 3))
        inf_chain_csv = infer_dir / "metrics_daily_chain.csv"
        inf_daily_csv = infer_dir / "metrics_daily.csv"
        if "post_min_on" not in inf_meta and inf_chain_csv.exists():
            cdf_i = pd.read_csv(inf_chain_csv, nrows=1)
            if len(cdf_i) > 0 and "post_min_on" in cdf_i.columns:
                post_min_on_i = int(cdf_i["post_min_on"].iloc[0])
                post_fill_off_i = int(cdf_i["post_fill_short_off"].iloc[0])

        pred_np = df_inf["pred"].to_numpy(dtype=np.float64)
        new_st = postprocess_state(pred_np, dec_val, post_min_on_i, post_fill_off_i).astype(int)
        new_prob = np.round(state_probability(pred_np, dec_val), 6)
        df_inf["pred_state"] = new_st
        df_inf["decision_thr_w"] = dec_val
        df_inf["pred_prob"] = new_prob
        df_inf[INFER_RESULT_COLUMNS].to_csv(inf_csv, index=False, encoding="utf-8")

        # 同步刷新 infer/metrics_daily_chain.csv（保持与 excluded_days/metrics_daily 同口径）
        valid_m = df_inf["target"].notna()
        excl_path = infer_dir / "excluded_days.json"
        if excl_path.exists():
            excl_days = set(json.loads(excl_path.read_text(encoding="utf-8")).get("excluded_days") or [])
            if excl_days:
                day_str = df_inf["timestamp"].astype(str).str.slice(0, 10)
                valid_m = valid_m & (~day_str.isin(excl_days))
        if int(valid_m.sum()) > 0:
            metric_names_i = ["mae", "rmse", "r2", "sae", "f1", "accuracy", "precision", "recall", "tp", "fp", "fn", "tn"]
            if inf_chain_csv.exists():
                cdf_cols = pd.read_csv(inf_chain_csv, nrows=1).columns
                inferred_i = [
                    c for c in cdf_cols
                    if c not in ("model", "date", "n_points", "state_thr_w", "decision_thr_w", "post_min_on", "post_fill_short_off")
                ]
                if inferred_i:
                    metric_names_i = inferred_i
            elif inf_daily_csv.exists():
                mdf_cols = pd.read_csv(inf_daily_csv, nrows=1).columns
                inferred_i = [c for c in mdf_cols if c not in ("model", "date", "n_points", "state_thr_w")]
                if inferred_i:
                    metric_names_i = list(dict.fromkeys(inferred_i + ["f1", "accuracy", "precision", "recall", "tp", "fp", "fn", "tn"]))
            sub_h = df_inf.loc[valid_m]
            dc_i = evaluate_daily_chain(
                sub_h["target"].to_numpy(dtype=np.float64)[:, None],
                sub_h["pred"].to_numpy(dtype=np.float64)[:, None],
                sub_h["pred_state"].to_numpy(dtype=int),
                pd.DatetimeIndex(pd.to_datetime(sub_h["timestamp"])),
                metric_names_i,
                on_thr_w=on_thr_inf,
            )
            dc_i.insert(0, "model", model_inf)
            dc_i["state_thr_w"] = on_thr_inf
            dc_i["decision_thr_w"] = dec_val
            dc_i["post_min_on"] = post_min_on_i
            dc_i["post_fill_short_off"] = post_fill_off_i
            dc_i.to_csv(inf_chain_csv, index=False, encoding="utf-8")

        if inf_meta_path.exists():
            if "decision_thr_w_before_sync" not in inf_meta:
                inf_meta["decision_thr_w_before_sync"] = old_infer_dec
            inf_meta["decision_thr_w"] = dec_val
            thr_blk = dict(inf_meta.get("thresholds") or {})
            thr_blk["decision_thr_w"] = dec_val
            thr_blk["decision_thr_w_source"] = f"synced({reason})"
            inf_meta["thresholds"] = thr_blk
            inf_meta["offline_drift_advice"] = {
                "drift_detected": False,
                "synced_decision_thr_w": dec_val,
                "reason": reason,
            }
            inf_meta["decision_thr_w_sync"] = {
                "synced_at": ts_now,
                "reason": reason,
                "previous_decision_thr_w": old_infer_dec,
                "synced_decision_thr_w": dec_val,
                "details": sync_details,
            }
            inf_meta_path.write_text(json.dumps(inf_meta, ensure_ascii=False, indent=2), encoding="utf-8")
        updated["infer_dir"] = str(infer_dir)

    return {
        "user_key": user_key,
        "synced_decision_thr_w": dec_val,
        "previous_train_decision_thr_w": old_train_dec,
        "previous_infer_decision_thr_w": old_infer_dec,
        "updated": updated,
        "reason": reason,
    }


def calibrate_and_sync_joint_decision_thr(
    user_key: str,
    train_dir: Path,
    infer_dir: Path,
) -> dict:
    """在 Step2+Step5 完成后执行双链联合收口校准并原子回刷 train+infer 产物，输出黄/红高亮日志。"""
    new_thr, info = resolve_joint_val_infer_decision_thr_w(train_dir, infer_dir)
    if new_thr is None:
        return info

    prev_thr = float(info["previous_decision_thr_w"])
    inf_before = info["infer_before"]
    inf_after = info["infer_after"]
    val_after = info["val_after"]
    shift = abs(new_thr - prev_thr)

    # 即使数值相等，若 train/meta.json 与 infer 的阈值曾不一致也做对齐
    tr_meta = json.loads((train_dir / "meta.json").read_text(encoding="utf-8"))
    tr_prev = float(tr_meta.get("decision_thr_w", prev_thr))
    need_write = (shift > 1e-6) or (abs(tr_prev - new_thr) > 1e-6)

    if need_write:
        sync_res = sync_decision_thr_artifacts(
            user_key=user_key,
            train_dir=train_dir,
            infer_dir=infer_dir,
            new_dec_thr=new_thr,
            reason="joint_val_infer_auto_sync",
            sync_details=info,
        )
        info["synced"] = True
        info["sync_result"] = sync_res
        hetero = bool(info.get("heteropeak_override", False))
        severe_shift = hetero or (shift >= max(40.0, 0.35 * max(prev_thr, 1.0))) or (info.get("confidence") == "low")
        if hetero:
            val_cap_s = info.get("val_capped_candidate_w")
            cap_note = f"历史val受召回约束卡在 {val_cap_s:.4g}" if isinstance(val_cap_s, (int, float)) else f"train_val={tr_prev:.4g}"
            msg = (
                f"[{user_key}] [AUTO-DEC-THR-SYNC] 检测到跨季双链异峰（{cap_note}，当期 infer 最优为 "
                f"decision_thr_w={new_thr:.4g}），遵循调参铁律按 infer 链定阈 "
                f"(model={info['model']}, infer_F1: {inf_before['f1']:.4f}->{inf_after['f1']:.4f}, "
                f"infer_FP: {inf_before['fp']}->{inf_after['fp']}, infer_recall={inf_after['recall']:.4f}, "
                f"历史val_recall={val_after['recall']:.4f})，已零重训同步刷新 train+infer 全套产物！"
            )
        elif severe_shift:
            msg = (
                f"[{user_key}] [AUTO-DEC-THR-SYNC] 双链联合收口生效：检测到显著跨期偏移 "
                f"train_val={tr_prev:.4g} -> 双链最优 decision_thr_w={new_thr:.4g} "
                f"(model={info['model']}, infer_F1: {inf_before['f1']:.4f}->{inf_after['f1']:.4f}, "
                f"infer_FP: {inf_before['fp']}->{inf_after['fp']}, infer_recall={inf_after['recall']:.4f}, "
                f"val_F1={val_after['f1']:.4f})，已零重训同步刷新 train+infer 全套产物！"
            )
        else:
            msg = (
                f"[{user_key}] [AUTO-DEC-THR-SYNC] 双链联合微调收口："
                f"train_val={tr_prev:.4g} -> 双链最优 decision_thr_w={new_thr:.4g} "
                f"(model={info['model']}, infer_F1: {inf_before['f1']:.4f}->{inf_after['f1']:.4f}, "
                f"infer_FP: {inf_before['fp']}->{inf_after['fp']}, infer_recall={inf_after['recall']:.4f}, "
                f"val_F1={val_after['f1']:.4f})，已零重训同步刷新 train+infer 全套产物"
            )
        log.warning("%s", red_text(msg) if severe_shift else yellow_text(msg))
    else:
        info["synced"] = False
        log.warning("%s", yellow_text(
            f"[{user_key}] [AUTO-DEC-THR-SYNC] 双链联合复核一致：train 与 infer 最优阈值均为 "
            f"decision_thr_w={new_thr:.4g} (infer_F1={inf_after['f1']:.4f}, "
            f"infer_FP={inf_after['fp']}, infer_recall={inf_after['recall']:.4f})，双侧产物已对齐"
        ))
    return info
