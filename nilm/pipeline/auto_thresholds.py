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
from nilm.postprocess.state import (
    _run_lengths,
    enforce_min_on,
    fill_short_off,
    postprocess_state,
    power_to_state,
    state_probability,
)

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

MIN_ON_GRID: tuple[int, ...] = (1, 2, 3, 4, 6, 8)
FILL_OFF_GRID: tuple[int, ...] = (0, 1, 2, 3, 4, 6)
MAX_GT_ON_LOSS_RATIO = 0.005
MAX_GT_BRIDGE_RATIO = 0.010
MAX_GT_GAP_BRIDGED_RATE = 0.10
INTRADAY_MAX_OFF_POINTS = 24

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


def _spearman_1d(a: np.ndarray, b: np.ndarray) -> float | None:
    """轻量级一维 Spearman 秩相关系数（不引额外重依赖，常数序列安全返回 None）。"""
    if len(a) < 8 or len(b) != len(a):
        return None
    sa = pd.Series(a).rank().to_numpy(dtype=np.float64)
    sb = pd.Series(b).rank().to_numpy(dtype=np.float64)
    std_a = float(sa.std())
    std_b = float(sb.std())
    if std_a < 1e-12 or std_b < 1e-12:
        return None
    r = float(np.corrcoef(sa, sb)[0, 1])
    return round(r, 4) if np.isfinite(r) else None


def _evaluate_bus_observability_for_trimodal(
    y_pool: np.ndarray | pd.Series,
    pbus_pool: np.ndarray | pd.Series | None,
    thr_1: float,
    thr_2: float,
    c0_p50: float,
    c1_p50: float,
) -> dict:
    """尺度不变母线可观测性仲裁器：对比首谷 thr_1（待机子簇）与次谷 thr_2（主工作簇）的母线边沿信噪比。

    无论 pbus 处于 PT/CT 校正前（二次侧）或校正后（一次侧 kW），常数倍率在 |Δpbus| 分子与背景 P90 分母中严格约消。
    """
    if pbus_pool is None:
        return {"available": False}

    if isinstance(y_pool, pd.Series) and isinstance(pbus_pool, pd.Series):
        aligned = pd.concat({"y": y_pool, "pbus": pbus_pool}, axis=1).dropna()
        if len(aligned) < 32:
            return {"available": False}
        if isinstance(aligned.index, pd.DatetimeIndex):
            if not aligned.index.is_monotonic_increasing:
                aligned = aligned.sort_index()
            yb = np.maximum(aligned["y"].to_numpy(dtype=np.float64), 0.0)
            pb = aligned["pbus"].to_numpy(dtype=np.float64)
            adj = np.asarray(
                (aligned.index[1:] - aligned.index[:-1]) == pd.Timedelta(minutes=15),
                dtype=bool,
            )
        else:
            yb = np.maximum(aligned["y"].to_numpy(dtype=np.float64), 0.0)
            pb = aligned["pbus"].to_numpy(dtype=np.float64)
            adj = np.ones(len(yb) - 1, dtype=bool)
    else:
        ya = np.asarray(y_pool, dtype=np.float64).reshape(-1)
        pa = np.asarray(pbus_pool, dtype=np.float64).reshape(-1)
        if len(ya) != len(pa):
            return {"available": False}
        vm = np.isfinite(ya) & np.isfinite(pa)
        if int(vm.sum()) < 32:
            return {"available": False}
        yb = np.maximum(ya[vm], 0.0)
        pb = pa[vm]
        if isinstance(y_pool, pd.Series) and isinstance(y_pool.index, pd.DatetimeIndex):
            ts_v = pd.DatetimeIndex(y_pool.index)[vm]
            if not ts_v.is_monotonic_increasing:
                ord_i = np.argsort(ts_v.asi8, kind="mergesort")
                ts_v = ts_v[ord_i]
                yb = yb[ord_i]
                pb = pb[ord_i]
            adj = np.asarray(
                (ts_v[1:] - ts_v[:-1]) == pd.Timedelta(minutes=15),
                dtype=bool,
            )
        else:
            adj = np.ones(len(yb) - 1, dtype=bool)

    if int(adj.sum()) < 16:
        return {"available": False}

    y_prev = yb[:-1][adj]
    y_curr = yb[1:][adj]
    dy_signed = y_curr - y_prev
    dy_abs = np.abs(dy_signed)
    dbus_signed = (pb[1:] - pb[:-1])[adj]
    dbus_abs = np.abs(dbus_signed)
    dir_dbus = np.sign(dy_signed) * dbus_signed

    edge_1 = ((y_prev < thr_1) & (y_curr >= 2.0 * thr_1)) | ((y_prev >= 2.0 * thr_1) & (y_curr < thr_1))
    edge_2 = ((y_prev < thr_2) & (y_curr >= 2.0 * thr_2)) | ((y_prev >= 2.0 * thr_2) & (y_curr < thr_2))
    edge_sub = (
        ((y_prev < thr_1) & (y_curr >= thr_1) & (y_curr < thr_2))
        | ((y_prev >= thr_1) & (y_prev < thr_2) & (y_curr < thr_1))
    )

    # 母线纯背景波动基准：优先取目标分路近似平稳步（无分路自身跳变污染），不足时回退非主簇跳变步
    flat_tol = max(0.25 * thr_1, 0.05 * max(c1_p50, 1e-6), 1e-6)
    flat_mask = dy_abs <= flat_tol
    if int(flat_mask.sum()) >= 10:
        bg_p90 = float(np.quantile(dbus_abs[flat_mask], 0.90))
    elif int((~edge_2).sum()) >= 10:
        bg_p90 = float(np.quantile(dbus_abs[~edge_2], 0.90))
    else:
        bg_p90 = float(np.quantile(dbus_abs, 0.90))
    bg_p90 = max(bg_p90, 1e-9)

    n_e1 = int(edge_1.sum())
    n_e2 = int(edge_2.sum())
    n_esub = int(edge_sub.sum())

    snr_1 = round(float(np.median(dir_dbus[edge_1]) / bg_p90), 4) if n_e1 >= 6 else None
    snr_2 = round(float(np.median(dir_dbus[edge_2]) / bg_p90), 4) if n_e2 >= 6 else None

    s_bus_br: float | None = None
    e2_sign_rate: float | None = None
    if n_e2 >= 4:
        valid_e2 = edge_2 & (dy_abs > 1e-6)
        if int(valid_e2.sum()) >= 4:
            s_bus_br = max(0.0, float(np.median(dir_dbus[valid_e2] / dy_abs[valid_e2])))
            e2_sign_rate = round(float(np.mean(dir_dbus[valid_e2] > 0)), 4)

    sub_sign_rate: float | None = None
    if n_esub >= 6:
        snr_sub = round(float(np.median(dir_dbus[edge_sub]) / bg_p90), 4)
        sub_sign_rate = round(float(np.mean(dir_dbus[edge_sub] > 0)), 4)
        snr_sub_mode = "direct_edges"
    elif s_bus_br is not None and np.isfinite(s_bus_br):
        implied_dbus_c1 = s_bus_br * max(c1_p50 - c0_p50, 0.0)
        snr_sub = round(float(implied_dbus_c1 / bg_p90), 4)
        sub_sign_rate = e2_sign_rate
        snr_sub_mode = "implied_step_ratio"
    else:
        snr_sub = None
        snr_sub_mode = "unavailable"

    sub_mask = yb < thr_2
    r_sub = _spearman_1d(pb[sub_mask], yb[sub_mask]) if int(sub_mask.sum()) >= 16 else None

    # 反向否决制（物理守恒律）：若子簇为真低功率工作档，其在母线上必须具备正向同号可观测阶跃
    # (snr_sub >= 1.2 且同向率 >= 0.65)，且同母线上幅值 >=4x 的主工作簇绝不可能反而被淹没 (snr_2 >= 1.2)。
    multigear_veto = bool(
        snr_sub is not None
        and snr_sub >= 1.2
        and (sub_sign_rate is None or sub_sign_rate >= 0.65)
        and (snr_2 is None or snr_2 >= 1.2)
    )
    bus_main_observable = bool(snr_2 is not None and snr_2 >= 1.0)
    bus_verified_promote = bool(
        bus_main_observable
        and not multigear_veto
        and (snr_sub is None or snr_sub < 1.0)
    )

    return {
        "available": True,
        "n_aligned": int(len(yb)),
        "n_edges_thr1": n_e1,
        "n_edges_thr2": n_e2,
        "n_edges_sub": n_esub,
        "snr_thr1": snr_1,
        "snr_thr2": snr_2,
        "snr_sub": snr_sub,
        "sub_sign_rate": sub_sign_rate,
        "snr_sub_mode": snr_sub_mode,
        "r_sub_spearman": r_sub,
        "multigear_veto": multigear_veto,
        "bus_main_observable": bus_main_observable,
        "promote_ok": bus_verified_promote,
    }


def resolve_auto_on_thr_w(y_pool: np.ndarray | pd.Series,
                          fallback_thr_w: float = 10.0,
                          pbus_pool: np.ndarray | pd.Series | None = None) -> tuple[float, dict]:
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
    if 1.15 * y_off_p99 <= 0.30 * y_on_p05 or y_off_p99 >= 0.60 * y_on_p05:
        hi_bound = max(0.30 * y_on_p05, 0.001)
    else:
        # 多路合并走廊保护（如 4 台待机 ~155W vs 1 台单开轻载 ~480W）：防止 0.30*y_on_p05 倒挂压穿待机 P99
        hi_bound = max(min(max(0.50 * y_on_p05, float(y_otsu)), 0.70 * y_on_p05), 0.001)
    lo_bound = min(max(1.15 * y_off_p99, 0.001), hi_bound)
    clamped = float(np.clip(raw_thr, lo_bound, hi_bound))
    thr_1 = _snap_nice_threshold(clamped, lo=lo_bound, hi=hi_bound)
    thr_1 = float(np.clip(thr_1, 0.001, 5000.0))
    resolved = thr_1

    # 谷底过渡带/多档待机歧义检测 + 三峰寄生待机簇跨越仲裁（两级递归 Otsu + 能量占比 + 母线 SNR）
    valley_lo = max(1.20 * y_off_p99, 1.0)
    valley_hi = 0.50 * y_on_p05
    if valley_hi > valley_lo:
        valley_density = float(((y_nonneg >= valley_lo) & (y_nonneg <= valley_hi)).mean())
    else:
        valley_density = 0.0

    on_sub_eta = 0.0
    v_2: float | None = None
    thr_2: float | None = None
    energy_share_c2: float | None = None
    bus_obs: dict = {"available": False}
    promoted_trimodal = False
    verified_multigear = False

    y_on_low_q = (
        float(np.quantile(y_on, min(0.10, max(0.005, 6.0 / float(len(y_on))))))
        if len(y_on) > 0 else 0.0
    )
    if len(y_on) >= 20 and y_on_p95 > 2.5 * max(y_on_low_q, 0.1):
        v_2, on_sub_eta = _otsu_log1p(y_on)
        c0 = y_off
        c1 = y_nonneg[(y_nonneg >= y_otsu) & (y_nonneg < v_2)]
        # 多路合并分路（如 p1+p2+p3+p4）四模态细化：当二级 Otsu 将“待机簇 + 单台轻载簇”一并划入 c1 时，
        # 下探定位待机与单台轻载间的真实谷底，避免把单台运行误判为待机或被 c1_p90 门禁误拦
        if len(c1) >= 16 and float(np.quantile(c1, 0.95)) > 2.5 * max(float(np.quantile(c1, 0.10)), 1.0):
            v_1b, sub1_eta = _otsu_log1p(c1)
            c1_lo = c1[c1 < v_1b]
            c1_hi = c1[c1 >= v_1b]
            if (
                sub1_eta >= 0.76
                and len(c1_lo) >= 8
                and len(c1_hi) >= 6
                and float(np.median(c1_hi)) >= 3.0 * max(float(np.median(c1_lo)), 1e-3)
            ):
                v_2 = v_1b
                on_sub_eta = max(on_sub_eta, sub1_eta)
                c1 = c1_lo
        c2 = y_nonneg[y_nonneg >= v_2]
        c01 = y_nonneg[y_nonneg < v_2]
        if len(c1) >= 8 and len(c2) >= 16 and len(c01) >= 8:
            c0_p50 = float(np.quantile(c0, 0.50)) if len(c0) > 0 else 0.0
            c0_p99 = float(np.quantile(c0, 0.99)) if len(c0) > 0 else 0.0
            c1_p50 = float(np.quantile(c1, 0.50))
            c1_p90 = float(np.quantile(c1, 0.90))
            c01_p98 = float(np.quantile(c01, 0.98))
            c01_p99 = float(np.quantile(c01, 0.99))
            c2_p05 = float(np.quantile(c2, 0.05))
            c2_p10 = float(np.quantile(c2, 0.10))
            c2_p50 = float(np.quantile(c2, 0.50))
            # 多路合并兼容走廊：兼顾“4 台同时待机 (~160W) vs 1 台单开轻载 (~480W)”的物理跨度，防止 lo_2 > hi_2 倒挂
            hi_2 = max(0.50 * c2_p05, min(float(v_2), 0.70 * c2_p05), 0.001)
            lo_2 = min(max(1.15 * c01_p98, 0.001), hi_2)
            energy_share_c2 = float(c2.sum() / max(float(y_on.sum()), 1e-9))
            raw_2 = max(1.25 * c01_p98, 1.08 * c01_p99, c01_p98 + 0.01 * c2_p50)
            cand_thr_2 = _snap_nice_threshold(float(np.clip(raw_2, lo_2, hi_2)), lo=lo_2, hi=hi_2)
            thr_2 = float(np.clip(cand_thr_2, 0.001, 5000.0))
            duty_c2 = float((y_nonneg >= thr_2).mean())
            gate_a = (
                on_sub_eta >= 0.76
                and thr_2 >= max(2.0 * thr_1, thr_1 + 1e-6)
                and c2_p50 >= 4.0 * max(c1_p50, 1e-3)
                and duty_c2 >= 0.05
                and energy_share_c2 >= 0.88
            )
            bus_obs = _evaluate_bus_observability_for_trimodal(
                y_pool, pbus_pool, thr_1, thr_2, c0_p50=c0_p50, c1_p50=c1_p50
            )
            if gate_a:
                if bus_obs.get("multigear_veto"):
                    verified_multigear = True
                elif bus_obs.get("promote_ok"):
                    promoted_trimodal = True
                elif not bus_obs.get("bus_main_observable", False):
                    # 反向否决制闭环：当总厂母线背景过大淹没全分路（TARGET_EDGE_BURIED_IN_BUS，snr_thr2 < 1.0）
                    # 或无母线输入时，只要未触发真多档否决且分路自身呈强三峰 + 主簇能量绝对主导（>=90%），予以晋升
                    if (
                        on_sub_eta >= 0.78
                        and c2_p50 >= 4.5 * max(c1_p50, 1e-3)
                        and c1_p90 <= 0.25 * max(c2_p50, 1e-9)
                        and energy_share_c2 >= 0.90
                    ):
                        promoted_trimodal = True

            if promoted_trimodal and thr_2 is not None:
                resolved = thr_2
                y_off_p98 = c01_p98
                y_off_p99 = c01_p99
                y_on_p05 = c2_p05
                y_on_p10 = c2_p10
                y_on_p50 = c2_p50
                v_lo2 = max(1.20 * c01_p98, 1.0)
                v_hi2 = 0.50 * c2_p05
                valley_density = (
                    float(((y_nonneg >= v_lo2) & (y_nonneg <= v_hi2)).mean())
                    if v_hi2 > v_lo2 else 0.0
                )

    reasons: list[str] = []
    confidence = "high"
    stage_name = "auto_trimodal_standby_promoted" if promoted_trimodal else "auto_bimodal"
    eff_eta = on_sub_eta if promoted_trimodal else otsu_eta

    if len(y) < 96:
        confidence = "low"
        reasons.append("SHORT_SERIES_LT_1DAY")

    if promoted_trimodal:
        reasons.append(
            f"BURIED_STANDBY_CLUSTER_PROMOTED(thr1={thr_1:.4g}->thr2={resolved:.4g},"
            f"sub_eta={on_sub_eta:.3f},c2_energy={energy_share_c2:.3f},"
            f"snr_sub={bus_obs.get('snr_sub')},snr2={bus_obs.get('snr_thr2')})"
        )
        if valley_density > 0.08:
            confidence = "low"
            reasons.append(f"INTERMEDIATE_PLATEAU_SUSPECTED(valley_ratio={valley_density:.3f}>0.08)")
    else:
        if otsu_eta < 0.72:
            confidence = "low"
            reasons.append(f"LOW_BIMODAL_SEPARABILITY(eta={otsu_eta:.3f}<0.72)")
        if valley_density > 0.08:
            confidence = "low"
            reasons.append(f"INTERMEDIATE_PLATEAU_SUSPECTED(valley_ratio={valley_density:.3f}>0.08)")
        if verified_multigear:
            reasons.append(
                f"IDENTIFIABLE_MULTIGEAR_KEPT_LOW_THR(thr1={thr_1:.4g},"
                f"snr_sub={bus_obs.get('snr_sub')},sub_eta={on_sub_eta:.3f})"
            )
        elif y_on_p95 > 2.5 * max(y_on_p10, 0.1) and on_sub_eta >= 0.72:
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
    out_info = {
        "configured": "auto",
        "resolved_on_thr_w": resolved,
        "stage": stage_name,
        "confidence": confidence,
        "n_samples": int(len(y)),
        "otsu_valley_w": round(float(v_2 if promoted_trimodal and v_2 is not None else y_otsu), 3),
        "otsu_eta": round(float(eff_eta), 4),
        "first_valley_thr_w": round(thr_1, 3),
        "second_valley_thr_w": round(thr_2, 3) if thr_2 is not None else None,
        "sub_otsu_eta": round(on_sub_eta, 4) if on_sub_eta > 0 else None,
        "c2_energy_share": round(energy_share_c2, 4) if energy_share_c2 is not None else None,
        "bus_observability": bus_obs if bus_obs.get("available") else None,
        "y_off_p98": round(y_off_p98, 3),
        "y_off_p99": round(y_off_p99, 3),
        "y_on_p05": round(y_on_p05, 3),
        "y_on_p10": round(y_on_p10, 3),
        "y_on_p50": round(y_on_p50, 3),
        "valley_density": round(valley_density, 4),
        "on_ratio": round(on_ratio, 4),
        "reasons": reasons,
    }
    return resolved, out_info


def resolve_train_on_thr_w(user_key: str, user_cfg: dict,
                           target_train_window: pd.Series | np.ndarray,
                           pbus_pool: pd.Series | np.ndarray | None = None) -> tuple[float, dict]:
    """解析训练阶段 on_thr_w：显式数值优先；配置为 'auto' 时自动探测并输出黄/红高亮日志。"""
    cfg_val = user_cfg.get("on_thr_w", 10.0)
    prov = (user_cfg.get("_provenance") or {}).get("on_thr_w", "default(硬编码)")
    if isinstance(cfg_val, str) and cfg_val.strip().lower() == "auto":
        resolved, decision = resolve_auto_on_thr_w(
            target_train_window, fallback_thr_w=10.0, pbus_pool=pbus_pool
        )
        decision["provenance"] = prov
        if decision.get("confidence") == "high":
            extra_note = ""
            if decision.get("stage") == "auto_trimodal_standby_promoted":
                extra_note = f", 三峰跨越待机底噪 {decision.get('first_valley_thr_w')}->{resolved:.4g}"
            log.warning("%s", yellow_text(
                f"[{user_key}] [AUTO-ON-THR] on_thr_w=auto 自动定阈生效 -> "
                f"on_thr_w={resolved:.4g} (双峰分离度 eta={decision.get('otsu_eta')}, "
                f"待机P99={decision.get('y_off_p99')}, 工况P10={decision.get('y_on_p10')}, "
                f"开机率={decision.get('on_ratio', 0.0) * 100:.1f}%{extra_note})"
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


def _add_off_pred_quantile_candidates(
    raw_cands: set[float],
    off_preds: np.ndarray,
    min_cand_w: float,
    upper_guard_w: float,
) -> None:
    """自适应补充关态预测噪声分位候选点（统一保留 5% 抗噪安全裕度，高基数样本加密 P96~P99.5）。"""
    if len(off_preds) >= 8:
        for q in (0.75, 0.90, 0.95, 0.98, 0.99):
            qv = round(float(np.quantile(off_preds, q)) * 1.05, 2)
            if min_cand_w <= qv <= upper_guard_w:
                raw_cands.add(qv)
    if len(off_preds) >= 20:
        for q in (0.96, 0.97, 0.985, 0.995):
            qv = round(float(np.quantile(off_preds, q)) * 1.05, 2)
            if min_cand_w <= qv <= upper_guard_w:
                raw_cands.add(qv)


def _apply_anti_collapse_guard(items: list, get_row_fn, tot_neg: int) -> tuple[list, bool]:
    """反全开坍缩特异性护栏：当负样本 >=12 且存在 Spec>=0.50 的可行点时，剔除 Spec<0.25（TN->0）的退化点。"""
    if tot_neg < 12 or len(items) <= 1:
        return items, False
    has_healthy_spec = any((float(get_row_fn(it)["tn"]) / float(tot_neg)) >= 0.50 for it in items)
    if not has_healthy_spec:
        return items, False
    kept = [it for it in items if (float(get_row_fn(it)["tn"]) / float(tot_neg)) >= 0.25]
    if kept and len(kept) < len(items):
        return kept, True
    return items, False


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
    _add_off_pred_quantile_candidates(raw_cands, off_preds, min_cand_w, upper_guard_w)

    cands = sorted(c for c in raw_cands if 0.001 <= c <= 5000.0)
    if not cands:
        cands = [round(base_thr, 4)]

    rows = [
        _eval_chain_candidate(eval_y, eval_p, eval_idx, base_thr, c, post_min_on, post_fill_off)
        for c in cands
    ]
    baseline_row = min(rows, key=lambda r: abs(r["thr"] - base_thr))
    feasible = [r for r in rows if r["recall"] >= MIN_VAL_RECALL]
    tot_neg_eval = int((eval_y < base_thr).sum())
    feasible, collapse_guarded = _apply_anti_collapse_guard(feasible, lambda r: r, tot_neg_eval)

    reasons = list(fallback_reasons)
    if collapse_guarded:
        reasons.append("ANTI_COLLAPSE_SPEC_GUARD")
    confidence = "high" if not fallback_reasons else "low"
    drift_anchor_w: float | None = None

    if not feasible:
        confidence = "low"
        reasons.append(f"RECALL_BELOW_{MIN_VAL_RECALL:.2f}_GUARD")
        # 欠召回止损护栏：当验证集原始召回已低于 0.95 时，首要矛盾是漏检，严禁无约束按 max(F1)
        # 将 decision_thr_w 大幅拔高（如 2844 从 20 拔高到 250）继续吞噬残存召回率。
        max_avail_rec = max((r["recall"] for r in rows), default=baseline_row["recall"])
        rec_stop_floor = max(baseline_row["recall"] - 0.01, max_avail_rec - 0.02) - 1e-9
        guarded_rows = [r for r in rows if r["recall"] >= rec_stop_floor]
        guarded_rows, low_rec_collapse = _apply_anti_collapse_guard(
            guarded_rows, lambda r: r, tot_neg_eval
        )
        if low_rec_collapse and "ANTI_COLLAPSE_SPEC_GUARD" not in reasons:
            reasons.append("ANTI_COLLAPSE_SPEC_GUARD")
        cand_pool = guarded_rows if guarded_rows else [baseline_row]
        best_guarded = max(
            cand_pool,
            key=lambda r: (r["f1"], -r["off_day_fp"], r["recall"], -abs(r["thr"] - base_thr)),
        )
        if best_guarded["f1"] > baseline_row["f1"] + MIN_DECISION_F1_GAIN:
            selected = best_guarded
            reasons.append("LOW_RECALL_STOP_LOSS_GUARDED")
        else:
            selected = baseline_row
            reasons.append("LOW_RECALL_KEEP_ON_THR_STOP_LOSS")
    else:
        best_f1 = max(r["f1"] for r in feasible)
        hi_rec_better = [
            r for r in feasible
            if r["f1"] >= baseline_row["f1"] and r["recall"] >= 0.97
        ]
        min_fp_hi_rec = min((r["fp"] for r in hi_rec_better), default=baseline_row["fp"])
        sig_fp_cut = (
            baseline_row["fp"] >= 8
            and (baseline_row["fp"] - min_fp_hi_rec) >= max(5, int(np.ceil(0.35 * baseline_row["fp"])))
        )
        baseline_spec_collapsed = (
            tot_neg_eval >= 12 and (float(baseline_row["tn"]) / float(tot_neg_eval)) < 0.25 and collapse_guarded
        )
        if (
            baseline_row["recall"] >= MIN_VAL_RECALL
            and (best_f1 - baseline_row["f1"]) < MIN_DECISION_F1_GAIN
            and not sig_fp_cut
            and not baseline_spec_collapsed
        ):
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


def _split_contiguous_15m_blocks(
    s_gt: np.ndarray,
    index_pool: pd.DatetimeIndex | None = None,
) -> list[np.ndarray]:
    """按相邻点时间差 == 15min 切分为严格连续的时间片段，杜绝跳日抽样或停电缺测伪拼接游程。"""
    arr = np.asarray(s_gt, dtype=bool).reshape(-1)
    if len(arr) == 0:
        return []
    if index_pool is None or len(index_pool) != len(arr):
        return [arr]

    ts = pd.DatetimeIndex(pd.to_datetime(index_pool))
    if not ts.is_monotonic_increasing:
        order = np.argsort(ts.asi8, kind="mergesort")
        ts = ts[order]
        arr = arr[order]

    if len(arr) == 1:
        return [arr]

    diffs = ts[1:] - ts[:-1]
    breaks = np.flatnonzero(diffs != pd.Timedelta(minutes=15)) + 1
    if len(breaks) == 0:
        return [arr]
    return [blk for blk in np.split(arr, breaks) if len(blk) > 0]


def resolve_postprocess_physical_bounds(
    y_pool: np.ndarray | pd.Series,
    index_pool: pd.DatetimeIndex | None = None,
    on_thr_w: float = 10.0,
) -> dict:
    """阶段一：基于训练全池有效真值序列提取无截断游程画像，锁定物理安全硬上界 (M_max, K_max)。

    核心物理约束（零模型依赖、零泄漏）：
    1. post_min_on 上界 M_max：
       - 分位数安全裕度：完整开态段数 >=5 时取 clip(floor(0.75 * P10(L_on)), 1, 8)，
         3~4 段时取 clip(floor(0.60 * min(L_on)), 1, 4)，<3 段时锁定 1；
       - 真值自回放硬校验：在连续真值块上执行 enforce_min_on(s_gt, m) 的真开态折损率 <= 0.5%。
    2. post_fill_short_off 上界 K_max：
       - 真值防桥接校验：在连续真值块上执行 fill_short_off(s_gt, k) 被错误桥接的真关态膨胀率 <= 1.0%，
         且当日内短停机间隙数 >=5 时，真实停机次数被桥接占比 <= 10%（保护间歇循环负荷与固定午休停机）。
    """
    y_arr = np.asarray(y_pool, dtype=np.float64).reshape(-1)
    valid_m = np.isfinite(y_arr)
    if index_pool is not None and len(index_pool) == len(y_arr):
        idx_valid: pd.DatetimeIndex | None = pd.DatetimeIndex(index_pool)[valid_m]
    else:
        idx_valid = None
    y_valid = y_arr[valid_m]
    s_gt = y_valid >= float(on_thr_w)
    blocks = _split_contiguous_15m_blocks(s_gt, idx_valid)

    total_gt_on = int(sum(int(b.sum()) for b in blocks))
    if total_gt_on == 0 or not blocks:
        return {
            "m_max": 1,
            "k_max": 3,
            "m_anchor": 1,
            "k_anchor": 3,
            "m_quant_bound": 1,
            "confidence": "low",
            "n_points": int(len(s_gt)),
            "n_contiguous_blocks": len(blocks),
            "total_gt_on_points": 0,
            "n_complete_on_runs": 0,
            "on_run_p10": None,
            "on_run_median": None,
            "n_inner_off_runs": 0,
            "inner_off_min": None,
            "inner_off_p10": None,
            "gt_on_loss_by_m": {str(m): 0.0 for m in MIN_ON_GRID},
            "gt_bridge_ratio_by_k": {str(k): 0.0 for k in FILL_OFF_GRID},
            "gt_gap_bridged_rate_by_k": {str(k): 0.0 for k in FILL_OFF_GRID},
            "gt_on_loss_at_m_max": 0.0,
            "gt_bridge_ratio_at_k_max": 0.0,
            "reasons": ["NO_POSITIVE_GT_FALLBACK_DEFAULT_BOUNDS"],
        }

    complete_on_runs: list[int] = []
    all_on_runs: list[int] = []
    inner_off_runs: list[int] = []
    max_grid_m = max(MIN_ON_GRID)

    for b in blocks:
        runs = _run_lengths(b)
        lb = len(b)
        for r_idx, (s, e, val) in enumerate(runs):
            r_len = int(e - s)
            if val:
                all_on_runs.append(r_len)
                # 严格内部完整游程，或虽触碰断点边界但已知长度 >= max_grid_m（不会拉低短游程分位数）
                if (s > 0 and e < lb) or r_len >= max_grid_m:
                    complete_on_runs.append(r_len)
            else:
                if 0 < r_idx < len(runs) - 1 and runs[r_idx - 1][2] and runs[r_idx + 1][2]:
                    if 1 <= r_len <= INTRADAY_MAX_OFF_POINTS:
                        inner_off_runs.append(r_len)

    eff_on_runs = complete_on_runs if len(complete_on_runs) > 0 else all_on_runs
    n_on_runs = len(eff_on_runs)
    l_on_p10: float | None = None
    if n_on_runs >= 5:
        l_on_p10 = float(np.quantile(eff_on_runs, 0.10))
        m_quant = int(np.clip(np.floor(0.75 * l_on_p10), 1, max_grid_m))
    elif n_on_runs >= 3:
        l_on_p10 = float(np.min(eff_on_runs))
        m_quant = int(np.clip(np.floor(0.60 * l_on_p10), 1, 4))
    else:
        l_on_p10 = float(np.min(eff_on_runs)) if n_on_runs > 0 else None
        m_quant = 1

    gt_on_loss_by_m: dict[int, float] = {}
    for m in MIN_ON_GRID:
        lost = sum(int((b & ~enforce_min_on(b, m)).sum()) for b in blocks)
        gt_on_loss_by_m[m] = float(lost) / float(max(1, total_gt_on))

    feas_m = [
        m for m in MIN_ON_GRID
        if m <= m_quant and gt_on_loss_by_m[m] <= MAX_GT_ON_LOSS_RATIO
    ]
    m_max = int(max(feas_m)) if feas_m else 1

    gt_bridge_by_k: dict[int, float] = {}
    gt_gap_rate_by_k: dict[int, float] = {}
    for k in FILL_OFF_GRID:
        bridged = sum(int((fill_short_off(b, k) & ~b).sum()) for b in blocks)
        gt_bridge_by_k[k] = float(bridged) / float(max(1, total_gt_on))
        if len(inner_off_runs) > 0:
            gt_gap_rate_by_k[k] = float(sum(1 for g in inner_off_runs if g <= k)) / float(len(inner_off_runs))
        else:
            gt_gap_rate_by_k[k] = 0.0

    feas_k = [0]
    for k in FILL_OFF_GRID:
        if k == 0:
            continue
        if gt_bridge_by_k[k] <= MAX_GT_BRIDGE_RATIO and (
            len(inner_off_runs) < 5 or gt_gap_rate_by_k[k] <= MAX_GT_GAP_BRIDGED_RATE
        ):
            feas_k.append(k)
    k_max = int(max(feas_k))
    if n_on_runs < 3:
        k_max = int(min(k_max, 3))
    k_anchor = int(min(3, k_max))

    bound_reasons: list[str] = [f"GT_BOUNDS_LOCKED(M_max={m_max},K_max={k_max})"]
    if k_max < 3:
        bound_reasons.append(f"PROTECT_SHORT_TRUE_OFF_CYCLES(K_max={k_max}<3)")
    if m_max == 1 and n_on_runs >= 3:
        bound_reasons.append("PROTECT_SHORT_TRUE_ON_BURSTS(M_max=1)")
    if n_on_runs < 3:
        bound_reasons.append(f"SPARSE_POSITIVE_ON_RUNS(n={n_on_runs}<3)")

    return {
        "m_max": m_max,
        "k_max": k_max,
        "m_anchor": 1,
        "k_anchor": k_anchor,
        "m_quant_bound": m_quant,
        "confidence": "high" if n_on_runs >= 3 else "low",
        "n_points": int(len(s_gt)),
        "n_contiguous_blocks": len(blocks),
        "total_gt_on_points": total_gt_on,
        "n_complete_on_runs": n_on_runs,
        "on_run_p10": round(l_on_p10, 2) if l_on_p10 is not None else None,
        "on_run_median": round(float(np.median(eff_on_runs)), 2) if eff_on_runs else None,
        "n_inner_off_runs": len(inner_off_runs),
        "inner_off_min": int(min(inner_off_runs)) if inner_off_runs else None,
        "inner_off_p10": round(float(np.quantile(inner_off_runs, 0.10)), 2) if inner_off_runs else None,
        "gt_on_loss_by_m": {str(m): round(v, 5) for m, v in gt_on_loss_by_m.items()},
        "gt_bridge_ratio_by_k": {str(k): round(v, 5) for k, v in gt_bridge_by_k.items()},
        "gt_gap_bridged_rate_by_k": {str(k): round(v, 5) for k, v in gt_gap_rate_by_k.items()},
        "gt_on_loss_at_m_max": round(gt_on_loss_by_m[m_max], 5),
        "gt_bridge_ratio_at_k_max": round(gt_bridge_by_k[k_max], 5),
        "reasons": bound_reasons,
    }


def _eval_3d_grid_fast(
    y_true: np.ndarray,
    pred: np.ndarray,
    idx: pd.DatetimeIndex | None,
    on_thr_w: float,
    c_cands: list[float],
    m_cands: list[int],
    k_eval_cands: list[int],
) -> tuple[dict[tuple[float, int, int], dict], int, bool]:
    """高效批量评估三维候选网格 (decision_thr_w, post_min_on, post_fill_short_off)。"""
    t_on = y_true >= float(on_thr_w)
    has_off_days = False
    off_day_mask: np.ndarray | None = None
    n_off_pts = int((~t_on).sum())
    unpad_pos: np.ndarray | None = None
    padded_size = len(t_on)

    if idx is not None and len(idx) == len(t_on) and len(idx) > 0:
        d_idx = pd.DatetimeIndex(idx)
        if not d_idx.is_monotonic_increasing:
            order = np.argsort(d_idx.asi8, kind="mergesort")
            d_idx = d_idx[order]
            t_on = t_on[order]
            pred = np.asarray(pred, dtype=np.float64)[order]
        day_on = pd.Series(t_on, index=d_idx).groupby(d_idx.normalize()).transform("max").to_numpy()
        off_day_mask = ~day_on
        n_off_pts = int(off_day_mask.sum())
        has_off_days = n_off_pts > 0
        blocks = _split_contiguous_15m_blocks(t_on, d_idx)
        if len(blocks) > 1:
            pad_gap = max(max(k_eval_cands, default=0) + 2, 2)
            pos_parts: list[np.ndarray] = []
            offset = 0
            for bi, blk in enumerate(blocks):
                if bi > 0:
                    offset += pad_gap
                blen = len(blk)
                pos_parts.append(np.arange(offset, offset + blen, dtype=np.int64))
                offset += blen
            unpad_pos = np.concatenate(pos_parts)
            padded_size = offset

    table: dict[tuple[float, int, int], dict] = {}
    tot_pos = int(t_on.sum())
    tot_neg = int((~t_on).sum())

    for c in c_cands:
        s0_raw = power_to_state(pred, c)
        if unpad_pos is not None:
            s0_in = np.zeros(padded_size, dtype=bool)
            s0_in[unpad_pos] = s0_raw
        else:
            s0_in = s0_raw
        for m in m_cands:
            s1 = enforce_min_on(s0_in, m)
            for k in k_eval_cands:
                st_full = fill_short_off(s1, k)
                st = st_full[unpad_pos] if unpad_pos is not None else st_full
                tp = int((st & t_on).sum())
                fp = int((st & ~t_on).sum())
                fn = tot_pos - tp
                tn = tot_neg - fp
                prec = tp / (tp + fp) if (tp + fp) > 0 else (1.0 if fn == 0 else 0.0)
                rec = tp / tot_pos if tot_pos > 0 else 1.0
                f1 = (2.0 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0
                off_fp = int(st[off_day_mask].sum()) if off_day_mask is not None else fp
                table[(c, m, k)] = {
                    "thr": round(float(c), 4),
                    "post_min_on": int(m),
                    "post_fill_short_off": int(k),
                    "f1": round(float(f1), 4),
                    "precision": round(float(prec), 4),
                    "recall": round(float(rec), 4),
                    "tp": tp,
                    "fp": fp,
                    "fn": fn,
                    "tn": tn,
                    "off_day_fp": off_fp,
                }
    return table, max(1, n_off_pts), has_off_days


def resolve_auto_postprocess_and_decision_thr(
    y_train: np.ndarray,
    y_val: np.ndarray,
    pred_val: np.ndarray,
    val_index: pd.DatetimeIndex | None,
    on_thr_w: float,
    cfg_decision_thr_w: float | str = "auto",
    cfg_post_min_on: int | str = "auto",
    cfg_post_fill_off: int | str = "auto",
    bounds: dict | None = None,
    pred_train: np.ndarray | None = None,
    train_index: pd.DatetimeIndex | None = None,
    calibrated_model: str = "",
    pbus_mode: str = "raw",
    upper_guard_override: float | None = None,
) -> tuple[float, int, int, dict, dict]:
    """阶段二：在阶段一锁定的物理安全框 (M_max, K_max) 内，按模型在验证集上协同寻优
    (decision_thr_w, post_min_on, post_fill_short_off)。

    三道验证集硬护栏 + 奥卡姆剃刀正则 + 惰性保持门：
    1. 召回护栏：val_recall >= 0.95 且增大 m 相比 m_anchor 的同幅值召回折损 <= 0.03；
    2. 全关日防桥接护栏：存在全关日且 k=auto 时，off_day_fp(c, m, k) <= off_day_fp(c, m, 0)；
    3. 非退化护栏：val_F1 >= anchor_best_F1 - 0.005；
    4. 正则化评分优先以「低幅值阈值 + 适度时间滤波」消除尖峰虚报，若无实质改善则锁定先验锚点 (m_anchor, k_anchor)。
    """
    base_thr = float(on_thr_w)
    m_is_auto = isinstance(cfg_post_min_on, str) and cfg_post_min_on.strip().lower() == "auto"
    k_is_auto = isinstance(cfg_post_fill_off, str) and cfg_post_fill_off.strip().lower() == "auto"
    c_is_auto = isinstance(cfg_decision_thr_w, str) and cfg_decision_thr_w.strip().lower() == "auto"
    c_is_on_thr = isinstance(cfg_decision_thr_w, str) and cfg_decision_thr_w.strip().lower() == "on_thr"

    if bounds is None:
        bounds = resolve_postprocess_physical_bounds(y_train, train_index, on_thr_w=base_thr)

    m_max = int(bounds.get("m_max", 1))
    k_max = int(bounds.get("k_max", 3))
    m_anchor = 1 if m_is_auto else int(cfg_post_min_on)
    k_anchor = int(min(3, k_max)) if k_is_auto else int(cfg_post_fill_off)

    m_cands = sorted({m for m in MIN_ON_GRID if m <= m_max} | {m_anchor}) if m_is_auto else [m_anchor]
    k_cands = sorted({k for k in FILL_OFF_GRID if k <= k_max} | {k_anchor}) if k_is_auto else [k_anchor]

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
            fb_c = base_thr if (c_is_auto or c_is_on_thr) else float(cfg_decision_thr_w)
            dec_fb = {
                "configured": cfg_decision_thr_w,
                "resolved_decision_thr_w": fb_c,
                "stage": "auto_fallback_on_thr" if c_is_auto else "explicit",
                "confidence": "low" if c_is_auto else "explicit",
                "calibrated_model": calibrated_model,
                "eval_split": eval_split,
                "reasons": ["NO_POSITIVE_TARGET_FALLBACK_ON_THR"],
            }
            post_fb = {
                "configured_post_min_on": cfg_post_min_on,
                "configured_post_fill_short_off": cfg_post_fill_off,
                "resolved_post_min_on": m_anchor,
                "resolved_post_fill_short_off": k_anchor,
                "stage": "auto_fallback_anchor",
                "confidence": "low",
                "calibrated_model": calibrated_model,
                "eval_split": eval_split,
                "m_max": m_max,
                "k_max": k_max,
                "m_anchor": m_anchor,
                "k_anchor": k_anchor,
                "gt_on_loss_at_selected_m": float(bounds.get("gt_on_loss_by_m", {}).get(str(m_anchor), 0.0)),
                "gt_bridge_ratio_at_selected_k": float(bounds.get("gt_bridge_ratio_by_k", {}).get(str(k_anchor), 0.0)),
                "reasons": ["NO_POSITIVE_TARGET_FALLBACK_ANCHOR"] + list(bounds.get("reasons", [])),
            }
            return fb_c, m_anchor, k_anchor, dec_fb, post_fb

    # 先按锚点 (m_anchor, k_anchor) 计算纯幅值基准（确保不启用游程增益时与 resolve_auto_decision_thr_w 完全同构）
    if c_is_auto:
        c_best_anchor, anchor_dec_info = resolve_auto_decision_thr_w(
            y_train=y_tr,
            y_val=eval_y,
            pred_val=eval_p,
            val_index=eval_idx,
            on_thr_w=base_thr,
            post_min_on=m_anchor,
            post_fill_off=k_anchor,
            pred_train=pred_train,
            train_index=train_index,
            calibrated_model=calibrated_model,
            pbus_mode=pbus_mode,
            upper_guard_override=upper_guard_override,
        )
        upper_guard_w = float(anchor_dec_info.get("upper_guard_w", base_thr))
        y_on_p10 = float(anchor_dec_info.get("y_on_p10", base_thr))
        c_cands = sorted({round(float(r["thr"]), 4) for r in anchor_dec_info.get("candidates", [])} | {round(base_thr, 4), round(c_best_anchor, 4)})
    elif c_is_on_thr:
        c_best_anchor = round(base_thr, 4)
        c_cands = [c_best_anchor]
        y_on_ref = y_tr[y_tr >= base_thr]
        if len(y_on_ref) == 0:
            y_on_ref = eval_y[eval_y >= base_thr]
        y_on_p10 = float(np.quantile(y_on_ref, 0.10))
        upper_guard_w = max(base_thr, UPPER_ON_P10_RATIO * y_on_p10)
    else:
        c_best_anchor = round(float(cfg_decision_thr_w), 4)
        c_cands = [c_best_anchor]
        y_on_ref = y_tr[y_tr >= base_thr]
        if len(y_on_ref) == 0:
            y_on_ref = eval_y[eval_y >= base_thr]
        y_on_p10 = float(np.quantile(y_on_ref, 0.10))
        upper_guard_w = max(base_thr, c_best_anchor, UPPER_ON_P10_RATIO * y_on_p10)

    k_eval_cands = sorted(set(k_cands) | ({0} if k_is_auto else set()))
    table, n_off_pts, has_off_days = _eval_3d_grid_fast(
        eval_y, eval_p, eval_idx, base_thr, c_cands, m_cands, k_eval_cands
    )

    c_ref_on = min(c_cands, key=lambda c: abs(c - base_thr))
    on_anchor_row = table[(c_ref_on, m_anchor, k_anchor)]
    anchor_best_row = table[(round(c_best_anchor, 4), m_anchor, k_anchor)]

    # 三道验证集硬护栏过滤
    feasible_keys: list[tuple[float, int, int]] = []
    for c in c_cands:
        for m in m_cands:
            for k in k_cands:
                row = table[(c, m, k)]
                if row["recall"] < MIN_VAL_RECALL:
                    continue
                # 护栏 ①b：增大 m 相比同幅值锚点 m_anchor 的召回折损 <= 0.03（严防剃掉真实短开机）
                if m > m_anchor and row["recall"] < table[(c, m_anchor, k)]["recall"] - 0.03 - 1e-9:
                    continue
                # 护栏 ②：全关日防桥接（k>0 不得比 k=0 在全关日凭空桥接放大虚报）
                if k_is_auto and has_off_days and k > 0:
                    if row["off_day_fp"] > table[(c, m, 0)]["off_day_fp"]:
                        continue
                # 护栏 ③：非退化门
                if row["f1"] < anchor_best_row["f1"] - 0.005 - 1e-9:
                    continue
                feasible_keys.append((c, m, k))

    tot_neg_3d = int((eval_y < base_thr).sum())
    feasible_keys, collapse_3d = _apply_anti_collapse_guard(
        feasible_keys, lambda key: table[key], tot_neg_3d
    )

    reasons = list(fallback_reasons) + list(bounds.get("reasons", []))
    if collapse_3d:
        reasons.append("ANTI_COLLAPSE_SPEC_GUARD")
    confidence = "high" if not fallback_reasons else "low"
    amp_span = max(upper_guard_w - base_thr, base_thr, 1.0)
    off_preds_3d = eval_p[(eval_y < base_thr) & np.isfinite(eval_p) & (eval_p > 0)]
    tau_floor = (
        max(base_thr, min(float(np.quantile(off_preds_3d, 0.95)), upper_guard_w))
        if len(off_preds_3d) >= 20 else base_thr
    )

    def _occam_score(key: tuple[float, int, int]) -> tuple[float, int, int, float]:
        c_v, m_v, k_v = key
        r = table[key]
        amp_ratio = max(0.0, c_v - tau_floor) / amp_span if c_is_auto else 0.0
        if has_off_days:
            off_pen = 0.50 * r["off_day_fp"] / n_off_pts
        elif tot_neg_3d >= 12:
            off_pen = 0.12 * r["fp"] / float(tot_neg_3d)
        else:
            off_pen = 0.15 * r["fp"] / max(1, len(eval_y))
        reg_score = (
            r["f1"]
            - off_pen
            - 0.0006 * abs(m_v - m_anchor)
            - 0.0006 * abs(k_v - k_anchor)
            - 0.0080 * amp_ratio
        )
        return (round(reg_score, 6), -r["off_day_fp"], -r["fp"], -abs(c_v - base_thr))

    if not feasible_keys:
        confidence = "low"
        reasons.append(f"RECALL_BELOW_{MIN_VAL_RECALL:.2f}_GUARD")
        # 欠召回 3D 止损护栏：当无任何 (c, m, k) 达到 recall >= 0.95 时，严禁回退无约束 max(F1)
        # 将幅值阈值拔高至上界（如 2844 从 20 飙升至 250），同时保留不进一步折损召回的安全 3D 游程滤波 (m, k)。
        max_anchor_rec = max(
            (table[(c, m_anchor, k_anchor)]["recall"] for c in c_cands),
            default=on_anchor_row["recall"],
        )
        rec_stop_floor = max(on_anchor_row["recall"] - 0.01, max_anchor_rec - 0.02) - 1e-9
        guarded_keys: list[tuple[float, int, int]] = []
        for c in c_cands:
            for m in m_cands:
                for k in k_cands:
                    row = table[(c, m, k)]
                    if row["recall"] < rec_stop_floor:
                        continue
                    if m > m_anchor and row["recall"] < table[(c, m_anchor, k)]["recall"] - 0.02 - 1e-9:
                        continue
                    if k_is_auto and has_off_days and k > 0:
                        if row["off_day_fp"] > table[(c, m, 0)]["off_day_fp"]:
                            continue
                    guarded_keys.append((c, m, k))
        guarded_keys, low_rec_collapse_3d = _apply_anti_collapse_guard(
            guarded_keys, lambda key: table[key], tot_neg_3d
        )
        if low_rec_collapse_3d and "ANTI_COLLAPSE_SPEC_GUARD" not in reasons:
            reasons.append("ANTI_COLLAPSE_SPEC_GUARD")

        if guarded_keys and max_anchor_rec > 1e-9:
            cand_guard_key = max(guarded_keys, key=_occam_score)
            cand_guard_row = table[cand_guard_key]
            off_fp_saved_g = on_anchor_row["off_day_fp"] - cand_guard_row["off_day_fp"]
            off_fp_cleared_g = on_anchor_row["off_day_fp"] > 0 and cand_guard_row["off_day_fp"] == 0
            f1_gain_g = cand_guard_row["f1"] - on_anchor_row["f1"]
            if f1_gain_g >= MIN_DECISION_F1_GAIN or off_fp_saved_g >= 2 or off_fp_cleared_g:
                sel_c, sel_m, sel_k = cand_guard_key
                reasons.append(f"LOW_RECALL_STOP_LOSS_3D_GUARDED(m={sel_m},k={sel_k})")
            else:
                sel_c, sel_m, sel_k = (c_ref_on, m_anchor, k_anchor)
                reasons.append("LOW_RECALL_KEEP_ON_THR_STOP_LOSS")
        else:
            sel_c, sel_m, sel_k = (c_ref_on, m_anchor, k_anchor)
            reasons.append("LOW_RECALL_KEEP_ON_THR_STOP_LOSS")
    else:
        cand_best_key = max(feasible_keys, key=_occam_score)
        cand_row = table[cand_best_key]
        cand_c, cand_m, cand_k = cand_best_key

        # 惰性保持门（Keep-Anchor Gate）：仅当游程调整带来实质收益时偏离 (m_anchor, k_anchor)
        off_fp_saved = anchor_best_row["off_day_fp"] - cand_row["off_day_fp"]
        off_fp_cleared = anchor_best_row["off_day_fp"] > 0 and cand_row["off_day_fp"] == 0
        f1_gain_vs_anchor = cand_row["f1"] - anchor_best_row["f1"]
        thr_lowered_substantially = (
            c_is_auto
            and cand_c <= 0.80 * c_best_anchor
            and (c_best_anchor - cand_c) >= max(10.0, 0.20 * base_thr)
            and cand_row["f1"] >= anchor_best_row["f1"] - 0.002
            and cand_row["off_day_fp"] <= anchor_best_row["off_day_fp"]
            and cand_row["fp"] <= anchor_best_row["fp"] + 2
        )

        if (cand_m, cand_k) != (m_anchor, k_anchor) and (
            off_fp_saved >= 2 or off_fp_cleared or f1_gain_vs_anchor >= 0.003 or thr_lowered_substantially
        ):
            sel_c, sel_m, sel_k = cand_c, cand_m, cand_k
            if thr_lowered_substantially and f1_gain_vs_anchor < 0.003:
                reasons.append(
                    f"POSTPROC_REPLACED_HIGH_THR(thr:{c_best_anchor:.4g}->{sel_c:.4g},m={sel_m},k={sel_k})"
                )
            else:
                reasons.append(f"VAL_POSTPROC_SYNERGY_OPTIMIZED(m={sel_m},k={sel_k})")
        else:
            sel_c, sel_m, sel_k = round(c_best_anchor, 4), m_anchor, k_anchor
            reasons.append(f"KEEP_POSTPROC_ANCHOR(m={sel_m},k={sel_k})")

        # 若为 scaled 模式且幅值阈值 auto，在选定的 (sel_m, sel_k) 上保留平台期抗漂移裕度
        if c_is_auto and pbus_mode == "scaled":
            off_preds = eval_p[(eval_y < base_thr) & np.isfinite(eval_p) & (eval_p > 0)]
            if len(off_preds) > 0:
                sel_row_cur = table[(sel_c, sel_m, sel_k)]
                fixed_mk_feas = [
                    table[(c, sel_m, sel_k)]
                    for c in c_cands
                    if table[(c, sel_m, sel_k)]["recall"] >= MIN_VAL_RECALL
                ]
                best_f1_mk = max((r["f1"] for r in fixed_mk_feas), default=sel_row_cur["f1"])
                off_p99 = float(np.quantile(off_preds, 0.99))
                raw_anchor = max(1.35 * off_p99, sel_c * 1.25)
                drift_anchor_w = round(min(raw_anchor, 0.70 * y_on_p10, upper_guard_w), 3)
                if drift_anchor_w > sel_c + 1e-6:
                    margin_pool = [
                        r for r in fixed_mk_feas
                        if r["thr"] >= sel_c - 1e-9
                        and r["recall"] >= SCALED_PLATEAU_MIN_RECALL
                        and r["f1"] >= best_f1_mk - SCALED_PLATEAU_F1_TOL
                        and r["off_day_fp"] <= sel_row_cur["off_day_fp"]
                        and r["fp"] <= sel_row_cur["fp"]
                    ]
                    if margin_pool:
                        anchored = min(
                            margin_pool,
                            key=lambda r: (r["off_day_fp"], r["fp"], abs(r["thr"] - drift_anchor_w), -r["f1"]),
                        )
                        if anchored["thr"] > sel_c + 1e-6:
                            sel_c = round(float(anchored["thr"]), 4)
                            reasons.append(f"SCALED_ANTI_DRIFT_MARGIN(anchor={drift_anchor_w:.1f})")

    selected_row = table[(sel_c, sel_m, sel_k)]
    if selected_row["f1"] < 0.50:
        confidence = "low"
        reasons.append(f"LOW_VAL_CHAIN_F1({selected_row['f1']:.4f}<0.50)")

    # 构造与现有 decision_thr_w_decision 100% 同构的幅值决策字典（按最终 sel_m, sel_k 切片候选表）
    dec_candidates_at_sel_mk = [
        {k: v for k, v in table[(c, sel_m, sel_k)].items() if k not in ("post_min_on", "post_fill_short_off")}
        for c in c_cands
    ]
    base_row_at_sel_mk = table[(c_ref_on, sel_m, sel_k)]
    dec_reasons = list(reasons)
    if c_is_auto and abs(sel_c - c_ref_on) < 1e-6 and "KEEP_ON_THR_NEAR_OPTIMAL" not in dec_reasons:
        dec_reasons.append("KEEP_ON_THR_NEAR_OPTIMAL")

    dec_info = {
        "configured": cfg_decision_thr_w,
        "resolved_decision_thr_w": float(sel_c),
        "stage": "auto_val_joint_postproc_calibrated" if c_is_auto else ("explicit_on_thr" if c_is_on_thr else "explicit"),
        "confidence": confidence if c_is_auto else "explicit",
        "calibrated_model": calibrated_model,
        "pbus_mode": pbus_mode,
        "eval_split": eval_split,
        "on_thr_w": base_thr,
        "y_on_p10": round(y_on_p10, 3),
        "upper_guard_w": round(upper_guard_w, 3),
        "min_recall_guard": MIN_VAL_RECALL,
        "post_min_on": int(sel_m),
        "post_fill_short_off": int(sel_k),
        "val_f1_at_on_thr": base_row_at_sel_mk["f1"],
        "val_off_fp_at_on_thr": base_row_at_sel_mk["off_day_fp"],
        "val_f1_selected": selected_row["f1"],
        "val_recall_selected": selected_row["recall"],
        "val_precision_selected": selected_row["precision"],
        "val_off_fp_selected": selected_row["off_day_fp"],
        "candidates": dec_candidates_at_sel_mk,
        "reasons": dec_reasons,
    }

    gt_loss_sel = float(bounds.get("gt_on_loss_by_m", {}).get(str(sel_m), 0.0))
    gt_bridge_sel = float(bounds.get("gt_bridge_ratio_by_k", {}).get(str(sel_k), 0.0))
    postproc_info = {
        "configured_post_min_on": cfg_post_min_on,
        "configured_post_fill_short_off": cfg_post_fill_off,
        "resolved_post_min_on": int(sel_m),
        "resolved_post_fill_short_off": int(sel_k),
        "resolved_decision_thr_w": float(sel_c),
        "stage": "auto_two_stage_calibrated",
        "confidence": confidence,
        "calibrated_model": calibrated_model,
        "eval_split": eval_split,
        "m_max": m_max,
        "k_max": k_max,
        "m_anchor": m_anchor,
        "k_anchor": k_anchor,
        "m_candidates": m_cands,
        "k_candidates": k_cands,
        "gt_on_loss_at_selected_m": round(gt_loss_sel, 5),
        "gt_bridge_ratio_at_selected_k": round(gt_bridge_sel, 5),
        "val_f1_before": anchor_best_row["f1"],
        "val_f1_after": selected_row["f1"],
        "val_recall_after": selected_row["recall"],
        "val_off_fp_before": anchor_best_row["off_day_fp"],
        "val_off_fp_after": selected_row["off_day_fp"],
        "anchor_decision_thr_w": float(c_best_anchor),
        "reasons": reasons,
    }
    return float(sel_c), int(sel_m), int(sel_k), dec_info, postproc_info


def log_auto_postprocess_train(user_key: str, postproc_decision: dict) -> None:
    """当训练阶段 post_min_on 或 post_fill_short_off 配置为 'auto' 时输出黄/红高亮日志。"""
    c_m = postproc_decision.get("configured_post_min_on")
    c_k = postproc_decision.get("configured_post_fill_short_off")
    if not (
        (isinstance(c_m, str) and c_m.strip().lower() == "auto")
        or (isinstance(c_k, str) and c_k.strip().lower() == "auto")
    ):
        return
    res_m = int(postproc_decision["resolved_post_min_on"])
    res_k = int(postproc_decision["resolved_post_fill_short_off"])
    m_max = postproc_decision.get("m_max")
    k_max = postproc_decision.get("k_max")
    gt_loss = float(postproc_decision.get("gt_on_loss_at_selected_m", 0.0))
    gt_bridge = float(postproc_decision.get("gt_bridge_ratio_at_selected_k", 0.0))
    model = postproc_decision.get("calibrated_model") or "unknown"
    f1_b = postproc_decision.get("val_f1_before")
    f1_a = postproc_decision.get("val_f1_after")
    off_b = postproc_decision.get("val_off_fp_before")
    off_a = postproc_decision.get("val_off_fp_after")
    reasons = "; ".join(postproc_decision.get("reasons", []))

    if postproc_decision.get("confidence") == "high":
        log.warning("%s", yellow_text(
            f"[{user_key}] [AUTO-POSTPROC] 游程后处理两阶段自动定参生效 -> "
            f"post_min_on={res_m}, post_fill_short_off={res_k} "
            f"(物理上界 M_max={m_max}, K_max={k_max}, 真值开态折损={gt_loss:.2%}, "
            f"真值桥接率={gt_bridge:.2%}, model={model}, val_F1: {f1_b}->{f1_a}, "
            f"全关日FP: {off_b}->{off_a}, {reasons})"
        ))
    else:
        log.warning("%s", red_text(
            f"[{user_key}] [AUTO-POSTPROC-WARN] 游程后处理自动定参触发低置信/回退告警 "
            f"({reasons}) -> 暂取安全锚点 post_min_on={res_m}, post_fill_short_off={res_k} "
            f"(M_max={m_max}, K_max={k_max}, model={model})！"
        ))


def resolve_infer_postprocess_params(
    user_key: str,
    user_cfg: dict,
    meta: dict,
    model_name: str | None = None,
) -> tuple[int, int, dict]:
    """推理阶段统一解析 (post_min_on, post_fill_short_off)：显式整数 100% 优先，'auto' 按模型继承训练 meta.json。"""
    prov = user_cfg.get("_provenance") or {}
    cfg_m = user_cfg.get("post_min_on", 1)
    cfg_k = user_cfg.get("post_fill_short_off", 3)
    prov_m = prov.get("post_min_on", "default(硬编码)")
    prov_k = prov.get("post_fill_short_off", "default(硬编码)")

    model_pp_map = meta.get("model_postprocess_params") or {}
    model_entry = model_pp_map.get(model_name) if (model_name and model_name in model_pp_map) else None

    meta_m_raw = meta.get("post_min_on")
    meta_k_raw = meta.get("post_fill_short_off")
    trained_m = (
        int(model_entry["post_min_on"])
        if (isinstance(model_entry, dict) and model_entry.get("post_min_on") is not None)
        else (int(meta_m_raw) if meta_m_raw is not None else 1)
    )
    trained_k = (
        int(model_entry["post_fill_short_off"])
        if (isinstance(model_entry, dict) and model_entry.get("post_fill_short_off") is not None)
        else (int(meta_k_raw) if meta_k_raw is not None else 3)
    )

    m_auto_cfg = isinstance(cfg_m, str) and cfg_m.strip().lower() == "auto"
    k_auto_cfg = isinstance(cfg_k, str) and cfg_k.strip().lower() == "auto"

    # 1) post_min_on 解析
    if m_auto_cfg:
        res_m = trained_m
        m_source = (
            f"meta.model_postprocess_params[{model_name}]"
            if isinstance(model_entry, dict) and "post_min_on" in model_entry
            else "meta.post_min_on"
        )
    elif prov_m == "default(硬编码)" and meta.get("post_min_on_configured") == "auto":
        res_m = trained_m
        m_source = "inherited_train_meta(default_auto)"
    else:
        res_m = int(cfg_m)
        m_source = f"explicit({prov_m})"

    # 2) post_fill_short_off 解析
    if k_auto_cfg:
        res_k = trained_k
        k_source = (
            f"meta.model_postprocess_params[{model_name}]"
            if isinstance(model_entry, dict) and "post_fill_short_off" in model_entry
            else "meta.post_fill_short_off"
        )
    elif prov_k == "default(硬编码)" and meta.get("post_fill_short_off_configured") == "auto":
        res_k = trained_k
        k_source = "inherited_train_meta(default_auto)"
    else:
        res_k = int(cfg_k)
        k_source = f"explicit({prov_k})"

    if m_auto_cfg or k_auto_cfg or "default_auto" in m_source or "default_auto" in k_source:
        log.warning("%s", yellow_text(
            f"[{user_key}] [AUTO-POSTPROC-INFER] 推理继承训练产物游程参数 -> "
            f"post_min_on={res_m} ({m_source}), post_fill_short_off={res_k} ({k_source}), "
            f"model={model_name or 'auto'}"
        ))
    elif (
        (meta_m_raw is not None and res_m != trained_m and prov_m != "default(硬编码)")
        or (meta_k_raw is not None and res_k != trained_k and prov_k != "default(硬编码)")
    ):
        log.warning("%s", yellow_text(
            f"[{user_key}] [POSTPROC-OVERRIDE] 推理显式游程参数 (min_on={res_m}, fill_off={res_k}) "
            f"覆盖训练产物参数 (min_on={trained_m}, fill_off={trained_k})"
        ))

    return res_m, res_k, {
        "post_min_on": res_m,
        "post_min_on_configured": cfg_m,
        "post_min_on_source": m_source,
        "post_fill_short_off": res_k,
        "post_fill_short_off_configured": cfg_k,
        "post_fill_short_off_source": k_source,
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
    *,
    force_auto_dec: bool = False,
    force_auto_min_on: bool = False,
    force_auto_fill_off: bool = False,
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

    # 推断当前生效的 post_min_on / post_fill_short_off（优先 meta -> metrics_daily_chain -> 默认 1/3）
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

    dec_cfg_tr = tr_meta.get("decision_thr_w_configured", "auto")
    inf_thr_blk = inf_meta.get("thresholds") or {}
    inf_dec_src = str(inf_thr_blk.get("decision_thr_w_source", ""))
    inf_dec_cfg = inf_thr_blk.get("decision_thr_w_configured", inf_meta.get("decision_thr_w_configured"))
    c_joint_auto = bool(force_auto_dec) or (
        (dec_cfg_tr == "auto" or inf_dec_cfg == "auto") and not inf_dec_src.startswith("explicit")
    )

    if c_joint_auto or ("decision_thr_w_configured" not in tr_meta):
        raw_cands: set[float] = {round(on_thr, 4), round(cur_dec, 4)}
        for g in STANDARD_DECISION_GRID:
            if min_cand_w - 1e-9 <= g <= upper_guard_w + 1e-9:
                raw_cands.add(round(float(g), 4))
        for y_s, p_s in ((y_va, p_va), (y_inf, p_inf)):
            off_p = p_s[(y_s < on_thr) & np.isfinite(p_s) & (p_s > 0)]
            _add_off_pred_quantile_candidates(raw_cands, off_p, min_cand_w, upper_guard_w)
        cands = sorted(c for c in raw_cands if 0.001 <= c <= 5000.0)
    else:
        cands = [round(cur_dec, 4)]

    tot_neg_inf = int((y_inf < on_thr).sum())
    off_p_inf_all = p_inf[(y_inf < on_thr) & np.isfinite(p_inf) & (p_inf > 0)]
    tau_floor_inf = (
        max(on_thr, min(float(np.quantile(off_p_inf_all, 0.95)), upper_guard_w))
        if len(off_p_inf_all) >= 20 else on_thr
    )

    inf_pp = inf_meta.get("postprocess") or {}
    inf_m_cfg = inf_pp.get("post_min_on_configured", inf_meta.get("post_min_on_configured"))
    inf_k_cfg = inf_pp.get("post_fill_short_off_configured", inf_meta.get("post_fill_short_off_configured"))
    m_joint_auto = bool(force_auto_min_on) or (
        (tr_meta.get("post_min_on_configured") == "auto" or inf_m_cfg == "auto")
        and not str(inf_pp.get("post_min_on_source", "")).startswith("explicit(")
    )
    k_joint_auto = bool(force_auto_fill_off) or (
        (tr_meta.get("post_fill_short_off_configured") == "auto" or inf_k_cfg == "auto")
        and not str(inf_pp.get("post_fill_short_off_source", "")).startswith("explicit(")
    )

    cur_key = round(cur_dec, 4)

    # ---- 分支 A：当 post_min_on 或 post_fill_short_off 为 auto 时，在物理上界 (M_max, K_max) 内联合收口 ----
    if m_joint_auto or k_joint_auto:
        bounds = tr_meta.get("postprocess_bounds")
        if not isinstance(bounds, dict) or "m_max" not in bounds:
            # 严格只用 train + val 真值恢复物理上界，严禁触碰 test 折
            pool_df = pd.concat([tr_sub, va_sub], ignore_index=True).sort_values("timestamp")
            bounds = resolve_postprocess_physical_bounds(
                pool_df["target"].to_numpy(dtype=np.float64),
                pd.DatetimeIndex(pd.to_datetime(pool_df["timestamp"])),
                on_thr_w=on_thr,
            )
        m_max = int(bounds.get("m_max", 1))
        k_max = int(bounds.get("k_max", 3))
        m_anchor = 1 if m_joint_auto else post_min_on
        k_anchor = int(min(3, k_max)) if k_joint_auto else post_fill_off

        m_cands = sorted({m for m in MIN_ON_GRID if m <= m_max} | {m_anchor}) if m_joint_auto else [post_min_on]
        k_cands = sorted({k for k in FILL_OFF_GRID if k <= k_max} | {k_anchor}) if k_joint_auto else [post_fill_off]
        m_eval_all = sorted(set(m_cands) | {post_min_on})
        k_eval_all = sorted(set(k_cands) | {post_fill_off} | ({0} if k_joint_auto else set()))

        val_tab, _, val_has_off = _eval_3d_grid_fast(y_va, p_va, idx_va, on_thr, cands, m_eval_all, k_eval_all)
        inf_tab, inf_n_off, inf_has_off = _eval_3d_grid_fast(y_inf, p_inf, idx_inf, on_thr, cands, m_eval_all, k_eval_all)

        all_cand_3d = [(c, m, k) for c in cands for m in m_cands for k in k_cands]

        def _mk_ok_val(c_v: float, m_v: int, k_v: int) -> bool:
            r = val_tab[(c_v, m_v, k_v)]
            if r["recall"] < MIN_VAL_RECALL:
                return False
            if m_v > m_anchor and r["recall"] < val_tab[(c_v, m_anchor, k_v)]["recall"] - 0.03 - 1e-9:
                return False
            if k_joint_auto and val_has_off and k_v > 0 and r["off_day_fp"] > val_tab[(c_v, m_v, 0)]["off_day_fp"]:
                return False
            return True

        def _mk_ok_inf(c_v: float, m_v: int, k_v: int) -> bool:
            r = inf_tab[(c_v, m_v, k_v)]
            if r["recall"] < MIN_VAL_RECALL:
                return False
            if m_v > m_anchor and r["recall"] < inf_tab[(c_v, m_anchor, k_v)]["recall"] - 0.03 - 1e-9:
                return False
            if k_joint_auto and inf_has_off and k_v > 0 and r["off_day_fp"] > inf_tab[(c_v, m_v, 0)]["off_day_fp"]:
                return False
            return True

        val_feas_3d = [key for key in all_cand_3d if _mk_ok_val(*key)]
        val_peak_f1_3d = max((val_tab[key]["f1"] for key in val_feas_3d), default=0.0)
        inf_feas_3d = [key for key in all_cand_3d if _mk_ok_inf(*key)]
        inf_feas_3d, collapse_inf_3d = _apply_anti_collapse_guard(
            inf_feas_3d, lambda key: inf_tab[key], tot_neg_inf
        )

        amp_span = max(upper_guard_w - on_thr, on_thr, 1.0)

        def _inf_reg_score(key: tuple[float, int, int], include_val: bool = True) -> tuple[float, int, int, int, float]:
            c_v, m_v, k_v = key
            ri = inf_tab[key]
            rv = val_tab[key]
            amp_ratio = max(0.0, c_v - tau_floor_inf) / amp_span if len(cands) > 1 else 0.0
            if inf_has_off:
                off_pen = 0.50 * ri["off_day_fp"] / inf_n_off
            elif tot_neg_inf >= 12:
                off_pen = 0.12 * ri["fp"] / float(tot_neg_inf)
            else:
                off_pen = 0.15 * ri["fp"] / max(1, len(y_inf))
            score = (
                ri["f1"]
                - off_pen
                - 0.0006 * abs(m_v - m_anchor)
                - 0.0006 * abs(k_v - k_anchor)
                - 0.0080 * amp_ratio
            )
            return (
                round(score, 6),
                -ri["off_day_fp"],
                -ri["fp"],
                -(rv["off_day_fp"] if include_val else 0),
                -abs(c_v - cur_dec),
            )

        pure_inf_key: tuple[float, int, int] | None = None
        if inf_feas_3d:
            pure_inf_key = max(inf_feas_3d, key=lambda key: _inf_reg_score(key, include_val=False))

        joint_feas_3d = [
            key for key in inf_feas_3d
            if (not val_feas_3d or (_mk_ok_val(*key) and val_tab[key]["f1"] >= val_peak_f1_3d - 0.02))
        ]
        confidence = "high"
        reasons_3d: list[str] = []
        if collapse_inf_3d:
            reasons_3d.append("ANTI_COLLAPSE_SPEC_GUARD")
        heteropeak_override = False
        val_capped_candidate: float | None = None

        if not joint_feas_3d:
            joint_feas_3d = [key for key in inf_feas_3d if val_tab[key]["recall"] >= 0.93]
            if joint_feas_3d:
                confidence = "low"
                reasons_3d.append("RELAXED_VAL_F1_FLOOR_FOR_SEVERE_SHIFT")
        if not joint_feas_3d:
            joint_feas_3d = list(inf_feas_3d)
            if joint_feas_3d:
                confidence = "low"
                heteropeak_override = True
                reasons_3d.append("INFER_RECALL_ONLY_FALLBACK")

        cur_3d = (cur_key, post_min_on, post_fill_off)
        if joint_feas_3d and tot_neg_inf >= 12 and inf_tab[cur_3d]["recall"] >= MIN_VAL_RECALL:
            non_down_collapse = [
                key for key in joint_feas_3d
                if not (
                    key[0] < cur_dec - 1e-6
                    and inf_tab[key]["fp"] > inf_tab[cur_3d]["fp"]
                    and (float(inf_tab[key]["tn"]) / float(tot_neg_inf)) < 0.50
                )
            ]
            if non_down_collapse:
                if len(non_down_collapse) < len(joint_feas_3d) and "ANTI_DOWNWARD_COLLAPSE_GUARD" not in reasons_3d:
                    reasons_3d.append("ANTI_DOWNWARD_COLLAPSE_GUARD")
                joint_feas_3d = non_down_collapse

        if not joint_feas_3d:
            confidence = "low"
            reasons_3d.append("RECALL_BELOW_095_GUARD")
            max_inf_rec_3d = max((inf_tab[key]["recall"] for key in all_cand_3d), default=inf_tab[cur_3d]["recall"])
            rec_floor_3d = max(inf_tab[cur_3d]["recall"] - 0.01, max_inf_rec_3d - 0.02) - 1e-9
            guarded_inf_3d = [key for key in all_cand_3d if inf_tab[key]["recall"] >= rec_floor_3d]
            pool_3d = guarded_inf_3d if guarded_inf_3d else [cur_3d]
            best_key = max(
                pool_3d,
                key=lambda key: (inf_tab[key]["f1"], -inf_tab[key]["off_day_fp"], inf_tab[key]["recall"], -abs(key[0] - cur_dec)),
            )
        else:
            cand_joint_key = max(joint_feas_3d, key=lambda key: _inf_reg_score(key, include_val=True))
            cur_inf_r = inf_tab[cur_3d]
            cand_inf_r = inf_tab[cand_joint_key]
            f1_gain_cur = cand_inf_r["f1"] - cur_inf_r["f1"]
            fp_saved_cur = cur_inf_r["fp"] - cand_inf_r["fp"]
            thr_lowered_cur = (
                cand_joint_key[0] <= 0.80 * cur_dec
                and (cur_dec - cand_joint_key[0]) >= max(10.0, 0.20 * on_thr)
                and cand_inf_r["f1"] >= cur_inf_r["f1"] - 0.002
                and cand_inf_r["off_day_fp"] <= cur_inf_r["off_day_fp"]
                and cand_inf_r["fp"] <= cur_inf_r["fp"] + 2
            )
            if (
                cur_3d in joint_feas_3d
                and f1_gain_cur < 0.002
                and fp_saved_cur < max(2, int(0.20 * cur_inf_r["fp"]))
                and not thr_lowered_cur
            ):
                joint_best_key = cur_3d
                joint_reason = "DUAL_CHAIN_CONSISTENT_KEEP"
            else:
                joint_best_key = cand_joint_key
                joint_reason = "DUAL_CHAIN_JOINT_POSTPROC_OPTIMIZED"

            if pure_inf_key is not None:
                j_inf_r = inf_tab[joint_best_key]
                p_inf_r = inf_tab[pure_inf_key]
                shift_vs_joint = abs(pure_inf_key[0] - joint_best_key[0])
                f1_gain_vs_joint = p_inf_r["f1"] - j_inf_r["f1"]
                fp_cut_vs_joint = (
                    (j_inf_r["fp"] - p_inf_r["fp"]) / max(j_inf_r["fp"], 1)
                    if j_inf_r["fp"] > 0 else 0.0
                )
                if (
                    (shift_vs_joint >= max(15.0, 0.20 * max(joint_best_key[0], 1.0)) or pure_inf_key[1:] != joint_best_key[1:])
                    and (
                        f1_gain_vs_joint >= 0.02
                        or (j_inf_r["fp"] >= 15 and fp_cut_vs_joint >= 0.50 and f1_gain_vs_joint >= 0.005)
                    )
                ):
                    val_capped_candidate = float(joint_best_key[0])
                    best_key = pure_inf_key
                    heteropeak_override = True
                    reasons_3d.append(
                        f"CROSS_SEASON_INFER_DOMINANT_OVERRIDE("
                        f"val_capped={joint_best_key[0]:.4g}->infer_opt={pure_inf_key[0]:.4g},"
                        f"m={pure_inf_key[1]},k={pure_inf_key[2]},"
                        f"infer_f1_gain={f1_gain_vs_joint:+.4f},fp_cut={fp_cut_vs_joint:.1%})"
                    )
                else:
                    best_key = joint_best_key
                    reasons_3d.append(joint_reason)
            else:
                best_key = joint_best_key
                reasons_3d.append(joint_reason)

        best_c, best_m, best_k = best_key
        return float(best_c), {
            "stage": "joint_val_infer_postproc_calibrated",
            "confidence": confidence,
            "heteropeak_override": heteropeak_override,
            "val_capped_candidate_w": val_capped_candidate,
            "pure_infer_optimal_w": pure_inf_key[0] if pure_inf_key is not None else None,
            "model": model_name,
            "on_thr_w": on_thr,
            "previous_decision_thr_w": cur_dec,
            "resolved_decision_thr_w": float(best_c),
            "previous_post_min_on": post_min_on,
            "resolved_post_min_on": int(best_m),
            "previous_post_fill_short_off": post_fill_off,
            "resolved_post_fill_short_off": int(best_k),
            "m_max": m_max,
            "k_max": k_max,
            "upper_guard_w": round(upper_guard_w, 3),
            "y_on_p10": round(y_on_p10, 3),
            "post_min_on": int(best_m),
            "post_fill_short_off": int(best_k),
            "val_before": val_tab[cur_3d],
            "val_after": val_tab[best_key],
            "infer_before": inf_tab[cur_3d],
            "infer_after": inf_tab[best_key],
            "reasons": reasons_3d,
        }

    # ---- 分支 B：post_min_on 与 post_fill_short_off 均为显式/缺省整数（100% 保持既有 1D 双链收口行为） ----
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
    inf_feas, collapse_inf_1d = _apply_anti_collapse_guard(
        inf_feas, lambda c: inf_map[c], tot_neg_inf
    )

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
        c for c in inf_feas
        if (not val_feas or (val_map[c]["recall"] >= MIN_VAL_RECALL and val_map[c]["f1"] >= val_peak_f1 - 0.02))
    ]
    confidence = "high"
    reasons: list[str] = []
    if collapse_inf_1d:
        reasons.append("ANTI_COLLAPSE_SPEC_GUARD")
    heteropeak_override = False
    val_capped_candidate: float | None = None

    if not joint_feas:
        # 极端跨期偏移兜底：放宽 val_F1 下限但仍尝试兼顾 infer_recall >= 0.95 与 val_recall >= 0.93
        joint_feas = [
            c for c in inf_feas
            if val_map[c]["recall"] >= 0.93
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

    if joint_feas and tot_neg_inf >= 12 and inf_map[cur_key]["recall"] >= MIN_VAL_RECALL:
        non_down_1d = [
            c for c in joint_feas
            if not (
                c < cur_dec - 1e-6
                and inf_map[c]["fp"] > inf_map[cur_key]["fp"]
                and (float(inf_map[c]["tn"]) / float(tot_neg_inf)) < 0.50
            )
        ]
        if non_down_1d:
            if len(non_down_1d) < len(joint_feas) and "ANTI_DOWNWARD_COLLAPSE_GUARD" not in reasons:
                reasons.append("ANTI_DOWNWARD_COLLAPSE_GUARD")
            joint_feas = non_down_1d

    if not joint_feas:
        confidence = "low"
        reasons.append("RECALL_BELOW_095_GUARD")
        max_inf_rec_1d = max((inf_map[c]["recall"] for c in cands), default=inf_map[cur_key]["recall"])
        rec_floor_1d = max(inf_map[cur_key]["recall"] - 0.01, max_inf_rec_1d - 0.02) - 1e-9
        guarded_inf_1d = [c for c in cands if inf_map[c]["recall"] >= rec_floor_1d]
        pool_1d = guarded_inf_1d if guarded_inf_1d else [cur_key]
        best_c = max(
            pool_1d,
            key=lambda c: (inf_map[c]["f1"], -inf_map[c]["off_day_fp"], inf_map[c]["recall"], -abs(c - cur_dec)),
        )
    else:
        best_inf_f1 = max(inf_map[c]["f1"] for c in joint_feas)
        cur_inf = inf_map[cur_key]
        min_inf_fp = min(inf_map[c]["fp"] for c in joint_feas if inf_map[c]["f1"] >= best_inf_f1 - 0.0025)
        f1_gain = best_inf_f1 - cur_inf["f1"]
        fp_saved = cur_inf["fp"] - min_inf_fp

        if (
            cur_key in joint_feas
            and f1_gain < 0.002
            and fp_saved < max(3, int(0.20 * cur_inf["fp"]))
        ):
            joint_best_c = cur_key
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
        "previous_post_min_on": post_min_on,
        "resolved_post_min_on": post_min_on,
        "previous_post_fill_short_off": post_fill_off,
        "resolved_post_fill_short_off": post_fill_off,
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
    new_post_min_on: int | None = None,
    new_post_fill_short_off: int | None = None,
) -> dict:
    """零模型重训原子同步刷新 train 与 infer 全套后处理判决链产物，确保 T1-T4 / I1-I10 100% 一致。"""
    dec_val = float(new_dec_thr)
    ts_now = datetime.now().isoformat(timespec="seconds")
    updated: dict[str, str] = {}
    old_train_dec: float | None = None
    old_infer_dec: float | None = None
    old_train_min_on: int | None = None
    old_train_fill_off: int | None = None
    old_infer_min_on: int | None = None
    old_infer_fill_off: int | None = None
    eff_min_on_out: int | None = new_post_min_on
    eff_fill_off_out: int | None = new_post_fill_short_off

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
        old_train_min_on = post_min_on
        old_train_fill_off = post_fill_off
        if new_post_min_on is not None:
            post_min_on = int(new_post_min_on)
        if new_post_fill_short_off is not None:
            post_fill_off = int(new_post_fill_short_off)
        eff_min_on_out = post_min_on
        eff_fill_off_out = post_fill_off

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
            txt_new = re.sub(r"post_min_on=\d+", f"post_min_on={post_min_on}", txt_new)
            txt_new = re.sub(r"post_fill_short_off=\d+", f"post_fill_short_off={post_fill_off}", txt_new)
            if txt_new != txt:
                comp_md.write_text(txt_new, encoding="utf-8")

        if "decision_thr_w_val_initial" not in tr_meta and old_train_dec is not None:
            tr_meta["decision_thr_w_val_initial"] = old_train_dec
        if "post_min_on_val_initial" not in tr_meta and old_train_min_on is not None:
            tr_meta["post_min_on_val_initial"] = old_train_min_on
        if "post_fill_short_off_val_initial" not in tr_meta and old_train_fill_off is not None:
            tr_meta["post_fill_short_off_val_initial"] = old_train_fill_off

        tr_meta["decision_thr_w"] = dec_val
        tr_meta["post_min_on"] = post_min_on
        tr_meta["post_fill_short_off"] = post_fill_off

        model_map = dict(tr_meta.get("model_decision_thr_w") or {})
        model_pp_map = {k: dict(v) for k, v in (tr_meta.get("model_postprocess_params") or {}).items()}
        target_model = (sync_details or {}).get("model") or tr_meta.get("best_model")
        if target_model:
            model_map[str(target_model)] = dec_val
            if new_post_min_on is not None or new_post_fill_short_off is not None or str(target_model) in model_pp_map:
                pp_entry = dict(model_pp_map.get(str(target_model)) or {})
                pp_entry["post_min_on"] = post_min_on
                pp_entry["post_fill_short_off"] = post_fill_off
                model_pp_map[str(target_model)] = pp_entry
        for m in tr_meta.get("models") or []:
            if reason == "explicit_sync":
                model_map[str(m)] = dec_val
                if new_post_min_on is not None or new_post_fill_short_off is not None:
                    model_pp_map[str(m)] = {
                        "post_min_on": post_min_on,
                        "post_fill_short_off": post_fill_off,
                    }
        tr_meta["model_decision_thr_w"] = model_map
        if model_pp_map:
            tr_meta["model_postprocess_params"] = model_pp_map
        tr_meta["decision_thr_w_sync"] = {
            "synced_at": ts_now,
            "reason": reason,
            "previous_decision_thr_w": old_train_dec,
            "synced_decision_thr_w": dec_val,
            "previous_post_min_on": old_train_min_on,
            "synced_post_min_on": post_min_on,
            "previous_post_fill_short_off": old_train_fill_off,
            "synced_post_fill_short_off": post_fill_off,
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
        old_infer_min_on = post_min_on_i
        old_infer_fill_off = post_fill_off_i
        if new_post_min_on is not None:
            post_min_on_i = int(new_post_min_on)
        if new_post_fill_short_off is not None:
            post_fill_off_i = int(new_post_fill_short_off)
        eff_min_on_out = post_min_on_i
        eff_fill_off_out = post_fill_off_i

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
            if "post_min_on_before_sync" not in inf_meta and old_infer_min_on is not None:
                inf_meta["post_min_on_before_sync"] = old_infer_min_on
            if "post_fill_short_off_before_sync" not in inf_meta and old_infer_fill_off is not None:
                inf_meta["post_fill_short_off_before_sync"] = old_infer_fill_off
            inf_meta["decision_thr_w"] = dec_val
            inf_meta["post_min_on"] = post_min_on_i
            inf_meta["post_fill_short_off"] = post_fill_off_i
            thr_blk = dict(inf_meta.get("thresholds") or {})
            thr_blk["decision_thr_w"] = dec_val
            thr_blk["decision_thr_w_source"] = f"synced({reason})"
            inf_meta["thresholds"] = thr_blk
            if "postprocess" in inf_meta or new_post_min_on is not None or new_post_fill_short_off is not None:
                pp_blk = dict(inf_meta.get("postprocess") or {})
                pp_blk["post_min_on"] = post_min_on_i
                pp_blk["post_fill_short_off"] = post_fill_off_i
                pp_blk["post_min_on_source"] = f"synced({reason})"
                pp_blk["post_fill_short_off_source"] = f"synced({reason})"
                inf_meta["postprocess"] = pp_blk
            inf_meta["offline_drift_advice"] = {
                "drift_detected": False,
                "synced_decision_thr_w": dec_val,
                "synced_post_min_on": post_min_on_i,
                "synced_post_fill_short_off": post_fill_off_i,
                "reason": reason,
            }
            inf_meta["decision_thr_w_sync"] = {
                "synced_at": ts_now,
                "reason": reason,
                "previous_decision_thr_w": old_infer_dec,
                "synced_decision_thr_w": dec_val,
                "previous_post_min_on": old_infer_min_on,
                "synced_post_min_on": post_min_on_i,
                "previous_post_fill_short_off": old_infer_fill_off,
                "synced_post_fill_short_off": post_fill_off_i,
                "details": sync_details,
            }
            inf_meta_path.write_text(json.dumps(inf_meta, ensure_ascii=False, indent=2), encoding="utf-8")
        updated["infer_dir"] = str(infer_dir)

    return {
        "user_key": user_key,
        "synced_decision_thr_w": dec_val,
        "synced_post_min_on": eff_min_on_out,
        "synced_post_fill_short_off": eff_fill_off_out,
        "previous_train_decision_thr_w": old_train_dec,
        "previous_infer_decision_thr_w": old_infer_dec,
        "previous_train_post_min_on": old_train_min_on,
        "previous_infer_post_min_on": old_infer_min_on,
        "previous_train_post_fill_short_off": old_train_fill_off,
        "previous_infer_post_fill_short_off": old_infer_fill_off,
        "updated": updated,
        "reason": reason,
    }


def calibrate_and_sync_joint_decision_thr(
    user_key: str,
    train_dir: Path,
    infer_dir: Path,
    *,
    force_auto_dec: bool = False,
    force_auto_min_on: bool = False,
    force_auto_fill_off: bool = False,
) -> dict:
    """在 Step2+Step5 完成后执行双链联合收口校准并原子回刷 train+infer 产物，输出黄/红高亮日志。"""
    new_thr, info = resolve_joint_val_infer_decision_thr_w(
        train_dir,
        infer_dir,
        force_auto_dec=force_auto_dec,
        force_auto_min_on=force_auto_min_on,
        force_auto_fill_off=force_auto_fill_off,
    )
    if new_thr is None:
        return info

    prev_thr = float(info["previous_decision_thr_w"])
    prev_m = int(info.get("previous_post_min_on", info["post_min_on"]))
    prev_k = int(info.get("previous_post_fill_short_off", info["post_fill_short_off"]))
    new_m = int(info.get("resolved_post_min_on", info["post_min_on"]))
    new_k = int(info.get("resolved_post_fill_short_off", info["post_fill_short_off"]))

    inf_before = info["infer_before"]
    inf_after = info["infer_after"]
    val_after = info["val_after"]
    shift = abs(new_thr - prev_thr)

    # 即使数值相等，若 train/meta.json 与 infer 的阈值或游程参数不一致也做原子对齐
    tr_meta = json.loads((train_dir / "meta.json").read_text(encoding="utf-8"))
    tr_prev = float(tr_meta.get("decision_thr_w", prev_thr))
    tr_prev_m = int(tr_meta.get("post_min_on", prev_m))
    tr_prev_k = int(tr_meta.get("post_fill_short_off", prev_k))
    pp_changed = (new_m != prev_m) or (new_k != prev_k) or (tr_prev_m != new_m) or (tr_prev_k != new_k)
    need_write = (shift > 1e-6) or (abs(tr_prev - new_thr) > 1e-6) or pp_changed

    pp_note = (
        f", post=({prev_m},{prev_k})->({new_m},{new_k})"
        if (new_m != prev_m or new_k != prev_k)
        else f", post=({new_m},{new_k})"
    )

    if need_write:
        sync_res = sync_decision_thr_artifacts(
            user_key=user_key,
            train_dir=train_dir,
            infer_dir=infer_dir,
            new_dec_thr=new_thr,
            reason="joint_val_infer_auto_sync",
            sync_details=info,
            new_post_min_on=new_m,
            new_post_fill_short_off=new_k,
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
                f"decision_thr_w={new_thr:.4g}{pp_note}），遵循调参铁律按 infer 链定阈 "
                f"(model={info['model']}, infer_F1: {inf_before['f1']:.4f}->{inf_after['f1']:.4f}, "
                f"infer_FP: {inf_before['fp']}->{inf_after['fp']}, infer_recall={inf_after['recall']:.4f}, "
                f"历史val_recall={val_after['recall']:.4f})，已零重训同步刷新 train+infer 全套产物！"
            )
        elif severe_shift:
            msg = (
                f"[{user_key}] [AUTO-DEC-THR-SYNC] 双链联合收口生效：检测到显著跨期偏移 "
                f"train_val={tr_prev:.4g} -> 双链最优 decision_thr_w={new_thr:.4g}{pp_note} "
                f"(model={info['model']}, infer_F1: {inf_before['f1']:.4f}->{inf_after['f1']:.4f}, "
                f"infer_FP: {inf_before['fp']}->{inf_after['fp']}, infer_recall={inf_after['recall']:.4f}, "
                f"val_F1={val_after['f1']:.4f})，已零重训同步刷新 train+infer 全套产物！"
            )
        else:
            msg = (
                f"[{user_key}] [AUTO-DEC-THR-SYNC] 双链联合微调收口："
                f"train_val={tr_prev:.4g} -> 双链最优 decision_thr_w={new_thr:.4g}{pp_note} "
                f"(model={info['model']}, infer_F1: {inf_before['f1']:.4f}->{inf_after['f1']:.4f}, "
                f"infer_FP: {inf_before['fp']}->{inf_after['fp']}, infer_recall={inf_after['recall']:.4f}, "
                f"val_F1={val_after['f1']:.4f})，已零重训同步刷新 train+infer 全套产物"
            )
        log.warning("%s", red_text(msg) if severe_shift else yellow_text(msg))
    else:
        info["synced"] = False
        log.warning("%s", yellow_text(
            f"[{user_key}] [AUTO-DEC-THR-SYNC] 双链联合复核一致：train 与 infer 最优参数均为 "
            f"decision_thr_w={new_thr:.4g}{pp_note} (infer_F1={inf_after['f1']:.4f}, "
            f"infer_FP={inf_after['fp']}, infer_recall={inf_after['recall']:.4f})，双侧产物已对齐"
        ))
    return info
