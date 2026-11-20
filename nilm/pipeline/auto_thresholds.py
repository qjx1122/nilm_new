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

import numpy as np
import pandas as pd

from nilm.common.logging import get_logger, red_text, yellow_text
from nilm.evaluation import build_comparison_table, summarize
from nilm.postprocess.state import postprocess_state

log = get_logger("pipeline.auto_thresholds")

NICE_ON_THRESHOLDS: tuple[float, ...] = (
    0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 20.0, 25.0,
    30.0, 40.0, 50.0, 60.0, 80.0, 100.0, 120.0, 150.0, 200.0, 300.0, 500.0,
)

STANDARD_DECISION_GRID: tuple[float, ...] = (
    0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0,
    60.0, 80.0, 100.0, 120.0, 150.0, 200.0, 250.0, 300.0, 350.0, 400.0, 450.0, 500.0,
)

MIN_VAL_RECALL = 0.95
UPPER_ON_P10_RATIO = 0.55
MIN_DECISION_F1_GAIN = 0.003
TIE_F1_TOL = 0.002


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
        day_on = pd.Series(t_on, index=idx).groupby(idx.normalize()).transform("max").to_numpy()
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
) -> tuple[float, dict]:
    """在选定模型的 val 验证集上执行带三重护栏的零泄漏 decision_thr_w 校准。

    三重护栏：
    1. 召回率硬下限：val_recall >= 0.95（宁留少量虚报，绝不切穿真开机主干）；
    2. 物理幅度硬上限：decision_thr_w <= max(on_thr_w, 0.55 * P10(y_on))；
    3. 保守最小增益门：若相比基准 on_thr_w 的 val_F1 提升 < 0.003，保持 on_thr_w 不做过拟合微调；
       同档最优（0.002 内）按「全关日 FP 更少 -> 总 FP 更少 -> 离 on_thr_w 更近」优选。
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
        "eval_split": eval_split,
        "on_thr_w": base_thr,
        "y_on_p10": round(y_on_p10, 3),
        "upper_guard_w": round(upper_guard_w, 3),
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
    if isinstance(cfg_dec, (int, float)) and not isinstance(cfg_dec, bool):
        dec_thr = float(cfg_dec)
        dec_source = f"explicit({prov_dec})"
    elif isinstance(cfg_dec, str) and cfg_dec.strip().lower() == "on_thr":
        dec_thr = float(on_thr)
        dec_source = f"explicit_on_thr({prov_dec})"
    else:
        model_dec_map = meta.get("model_decision_thr_w") or {}
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
) -> dict | None:
    """只读离线跨期漂移诊断：存在推理分路真值时检测 decision_thr_w 是否发生显著跨月偏移。

    严格零泄漏：本函数在 inference_result.csv 生成完毕后只读运行，绝不修改任何推理预测结果；
    若发现重校准可使 infer 链 F1 显著提升（>=0.02）且当前 F1<0.95，输出红色告警供月度校准参考。
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
    )
    gain = float(rec_info.get("val_f1_selected", 0.0)) - float(cur["f1"])
    shift = abs(rec_thr - current_dec_thr_w)
    if cur["f1"] < 0.95 and gain >= 0.02 and shift >= max(15.0, 0.25 * current_dec_thr_w):
        advice = {
            "drift_detected": True,
            "current_decision_thr_w": current_dec_thr_w,
            "current_infer_chain_f1": cur["f1"],
            "current_infer_recall": cur["recall"],
            "current_infer_off_fp": cur["off_day_fp"],
            "recommended_decision_thr_w": rec_thr,
            "recommended_infer_chain_f1": rec_info["val_f1_selected"],
            "recommended_infer_recall": rec_info["val_recall_selected"],
            "recommended_infer_off_fp": rec_info["val_off_fp_selected"],
            "f1_potential_gain": round(gain, 4),
        }
        log.warning("%s", red_text(
            f"[{user_key}] [AUTO-DEC-THR-DRIFT] 离线评估检测到跨期基线漂移：当前 "
            f"decision_thr_w={current_dec_thr_w:.4g} 下 infer 链 F1={cur['f1']:.4f} "
            f"(全关日FP={cur['off_day_fp']})；离线重校准建议更新为 "
            f"decision_thr_w={rec_thr:.4g} (F1={rec_info['val_f1_selected']:.4f}, "
            f"recall={rec_info['val_recall_selected']:.4f}, 全关日FP={rec_info['val_off_fp_selected']})！"
        ))
        return advice
    return {
        "drift_detected": False,
        "current_decision_thr_w": current_dec_thr_w,
        "current_infer_chain_f1": cur["f1"],
    }
