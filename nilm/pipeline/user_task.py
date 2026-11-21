"""单用户任务编排（指南 §1–§11 全流程）：一个 user_key = 一个独立任务。

只有 pipeline 层允许组合各功能模块；对外仅暴露 run_user_train / run_user_infer。
产物目录：outputs/<user_key>/<mode>/<timestamp>/（完成写 _DONE 标记，供断点续跑）。
"""

from __future__ import annotations

import json
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from nilm.analysis import analyze_branch_sessions, identifiability_report
from nilm.common.contracts import (INFER_RESULT_COLUMNS, INFERENCE_RESULT_REL,
                                   Status, split_user_key)
from nilm.common.logging import get_logger, red_text
from nilm.common.schema import bus_total
from nilm.common.timefilter import filter_dataframe
from nilm.data_io.csv_source import CsvBranchLoader, CsvBusLoader
from nilm.data_io.validator import (QualityError, assert_quality,
                                    daily_quality_table, invalid_data_days,
                                    qualified_days_counts, qualified_days_detail,
                                    qualified_days_summary,
                                    quality_advice, quality_report,
                                    split_coverage_advice, write_quality_html,
                                    write_schema_report)
from nilm.evaluation import (METRIC_REGISTRY, build_comparison_table,
                             evaluate_all, evaluate_daily,
                             evaluate_daily_chain, summarize)
from nilm.models import MODEL_REGISTRY
from nilm.models.base import BaseModel
from nilm.models.constraints import apply_constraints
from nilm.pipeline.auto_thresholds import (
    UPPER_ON_P10_RATIO,
    check_offline_decision_drift,
    log_auto_decision_thr_train,
    log_auto_postprocess_train,
    resolve_auto_decision_thr_w,
    resolve_auto_postprocess_and_decision_thr,
    resolve_infer_postprocess_params,
    resolve_infer_thresholds,
    resolve_postprocess_physical_bounds,
    resolve_train_on_thr_w,
    select_calibration_model_zero_leakage,
)
from nilm.postprocess.state import postprocess_state, state_probability
from nilm.preprocess.align import align_frames, estimate_time_offset, resample_bus
from nilm.preprocess.clean import Cleaner
from nilm.preprocess.dataset import DEFAULT_WINDOW, build_windows, drop_invalid_rows
from nilm.preprocess.features import build_features
from nilm.preprocess.normalize import apply_branch_scale, apply_pt_ct
from nilm.preprocess.scaling import TrainFitScaler
from nilm.preprocess.splits import build_split_masks
from nilm.preprocess.target import build_target, resolve_target_cols
from nilm.reporting import write_markdown_report

log = get_logger("pipeline.user_task")

DONE_MARKER = "_DONE"
NON_SCALED_COLS = {"slot", "hour", "minute", "day_of_week", "is_weekend",
                   "month", "day_of_year",
                   # pbus_mode="raw" 时保持 pbus 不缩放：基线 proportional 走物理分摊
                   # （2026-08-18 修复 2842 pred=share×z(pbus)→F1=0 缺陷，856 跨期稳健）。
                   # pbus_mode="scaled" 时由 _non_scaled_cols 移出 pbus 做 z-score，并联动
                   # proportional 走线性改学（y≈a·pbus_z+b，733 大厂/多分路求和目标适用）。
                   "pbus"}
NON_SCALED_COLS_SCALED = NON_SCALED_COLS - {"pbus"}
SEQ_MODEL_NAMES = ("transformer", "lstm", "cnn1d")
VAL_MAE_MIN_REL_GAIN = 0.05   # 候选 scaled 相对 raw 的验证集 MAE 最小改善率（5%，过滤随机微扰）
VAL_F1_MAX_DROP = 0.015       # 候选 scaled 相对 raw 允许的验证集 F1 最大退化容忍度（防 778 类关机虚报激增）


def _non_scaled_cols(pbus_mode: str) -> set[str]:
    """按实际生效的物理 pbus_mode ('raw'|'scaled') 返回不参与 z-score 的特征列名集合。"""
    if pbus_mode == "scaled":
        return NON_SCALED_COLS_SCALED
    if pbus_mode == "raw":
        return NON_SCALED_COLS
    raise ValueError(f"未知物理 pbus_mode={pbus_mode!r}（仅支持 'raw' / 'scaled'）")


def resolve_auto_pbus_mode(pbus_train: np.ndarray,
                           y_train: np.ndarray,
                           on_thr_w: float = 10.0) -> tuple[str, dict]:
    """统计 train 集无泄漏物理特征并返回稳健默认基态 'raw'（三步闭环第1步，v4 根治版）。

    严格仅读取 splits['train']（严禁接触 val/test，遵守 §11 零泄漏契约）。
    物理定论（733 vs 778 对比实证）：778 的母线规模（P95=5442kW, std=2252kW）与开机贡献率
    （on_contrib=0.127）在所有静态维度上均比 733（P95=4005kW, std=1311kW, on_contrib=0.207）
    更极端，但 778 因跨月背景基荷剧烈漂移必须走 raw（scaled 下 infer F1 崩至 0.7216 vs raw 0.9776）。
    因此单靠训练前静态阈值无法分离“真欠拟合需缩放”与“高背景漂移规律负荷需抗漂移”，第1步统一先跑
    抗漂移的默认基态 raw，仅在检测到真实欠拟合（第2步）且候选 scaled 通过验证集 MAE+F1 双向验收
    （第3步）时才切换为 scaled。
    """
    pb_arr = np.asarray(pbus_train, dtype=np.float64).reshape(-1)
    yt_arr = np.asarray(y_train, dtype=np.float64).reshape(-1)
    valid = np.isfinite(pb_arr) & np.isfinite(yt_arr)
    pb, yt = pb_arr[valid], yt_arr[valid]
    if len(pb) == 0:
        return "raw", {
            "configured_mode": "auto", "resolved_mode": "raw",
            "stage": "default_raw", "train_pbus_p95": 0.0, "train_pbus_std": 0.0,
            "train_pbus_mean": 0.0, "train_target_mean": 0.0,
            "train_contrib_ratio": 1.0, "train_on_contrib_ratio": 1.0,
            "train_on_duty_ratio": 0.0, "on_thr_w": float(on_thr_w),
            "reasons": ["EMPTY_TRAIN_FALLBACK_RAW"],
        }

    p95 = float(np.quantile(pb, 0.95))
    pstd = float(np.std(pb))
    mean_pb = float(np.mean(pb))
    mean_y = float(np.mean(yt))
    contrib = (mean_y / mean_pb) if mean_pb > 1e-9 else 1.0

    on_mask = yt >= float(on_thr_w)
    on_duty = float(np.mean(on_mask))
    if np.any(on_mask):
        mean_pb_on = float(np.mean(pb[on_mask]))
        mean_y_on = float(np.mean(yt[on_mask]))
        on_contrib = (mean_y_on / mean_pb_on) if mean_pb_on > 1e-9 else 1.0
    else:
        on_contrib = contrib

    return "raw", {
        "configured_mode": "auto",
        "resolved_mode": "raw",
        "stage": "default_raw",
        "train_pbus_p95": round(p95, 2),
        "train_pbus_std": round(pstd, 2),
        "train_pbus_mean": round(mean_pb, 2),
        "train_target_mean": round(mean_y, 2),
        "train_contrib_ratio": round(contrib, 4),
        "train_on_contrib_ratio": round(on_contrib, 4),
        "train_on_duty_ratio": round(on_duty, 4),
        "on_thr_w": float(on_thr_w),
        "reasons": ["DEFAULT_RAW_BEFORE_VAL_CHECK"],
    }


def _should_fallback_raw_to_scaled(results_by_split: dict[str, dict],
                                   split_bands: dict[str, dict[str, float]],
                                   on_thr: float) -> tuple[bool, list[str]]:
    """第2步候选重训触发检测（仅检视 train/val 严禁看 test，遵守 §11 零泄漏契约）：

    首轮默认跑完 raw 后，检查深度序列模型是否因未缩放 pbus 条件数病态触发真实欠拟合/坍缩：
    1) 预测带宽坍缩（train 或 val 带宽 < on_thr_w）或 train R² < 0；
    2) 序列模型在 train 与 val 上 R² 双双显著劣于非退化基线（history_profile / ridge），
       且 val MAE 亦劣于最优参考基线（防 ridge 因最小化 MSE 使 R² 虚高但 MAE 远差于
       SmoothL1 序列模型如 856 时误触发）；
    3) 序列模型在 val 上 R² 与 MAE 均劣于纯静态槽位画像 history_profile 且 proportional 严重失配（R² < -1）。
    """
    reasons: list[str] = []
    ref_models = [b for b in ("history_profile", "ridge") if b in results_by_split]
    prop_tr_r2 = (results_by_split.get("proportional", {})
                  .get("train", {}).get("r2", {}).get("macro", 0.0))

    for seq_name in SEQ_MODEL_NAMES:
        if seq_name not in results_by_split:
            continue
        by_sp = results_by_split[seq_name]
        tr_r2 = by_sp.get("train", {}).get("r2", {}).get("macro")
        val_r2 = by_sp.get("val", {}).get("r2", {}).get("macro")
        tr_mae = by_sp.get("train", {}).get("mae", {}).get("macro")
        val_mae = by_sp.get("val", {}).get("mae", {}).get("macro")
        bands = split_bands.get(seq_name, {})
        tr_band = bands.get("train")
        val_band = bands.get("val")

        if tr_band is not None and tr_band < on_thr:
            reasons.append(f"{seq_name}_TRAIN_COLLAPSED(band={tr_band:.2f}<{on_thr:.2f})")
            continue
        if val_band is not None and val_band < on_thr:
            reasons.append(f"{seq_name}_VAL_COLLAPSED(band={val_band:.2f}<{on_thr:.2f})")
            continue
        if tr_r2 is not None and tr_r2 < 0.0:
            reasons.append(f"{seq_name}_TRAIN_NEG_R2(r2={tr_r2:.4f}<0)")
            continue

        if ref_models and tr_r2 is not None:
            ref_tr = [results_by_split[b]["train"]["r2"]["macro"]
                      for b in ref_models
                      if "train" in results_by_split[b] and "r2" in results_by_split[b]["train"]]
            best_ref_tr = max(ref_tr) if ref_tr else None
            if val_r2 is not None:
                ref_val = [results_by_split[b]["val"]["r2"]["macro"]
                           for b in ref_models
                           if "val" in results_by_split[b] and "r2" in results_by_split[b]["val"]]
                best_ref_val = max(ref_val) if ref_val else None
                ref_val_maes = [results_by_split[b]["val"]["mae"]["macro"]
                                for b in ref_models
                                if "val" in results_by_split[b]
                                and "mae" in results_by_split[b]["val"]]
                best_ref_val_mae = min(ref_val_maes) if ref_val_maes else None
                mae_worse = (val_mae is None or best_ref_val_mae is None
                             or val_mae > best_ref_val_mae)
                if (best_ref_tr is not None and best_ref_val is not None
                        and tr_r2 < best_ref_tr - 0.03 and val_r2 < best_ref_val - 0.02
                        and mae_worse):
                    mae_tag = (f", val_mae={val_mae:.2f}>{best_ref_val_mae:.2f}"
                               if (val_mae is not None and best_ref_val_mae is not None) else "")
                    reasons.append(
                        f"{seq_name}_UNDERFIT_VS_BASELINE("
                        f"train_r2={tr_r2:.4f}<{best_ref_tr:.4f}-0.03, "
                        f"val_r2={val_r2:.4f}<{best_ref_val:.4f}-0.02{mae_tag})")
                    continue
                hp_val = (results_by_split.get("history_profile", {})
                          .get("val", {}).get("r2", {}).get("macro"))
                hp_val_mae = (results_by_split.get("history_profile", {})
                              .get("val", {}).get("mae", {}).get("macro"))
                hp_mae_worse = (val_mae is None or hp_val_mae is None or val_mae > hp_val_mae)
                if (hp_val is not None and val_r2 < hp_val - 0.02
                        and hp_mae_worse and prop_tr_r2 < -1.0):
                    reasons.append(
                        f"{seq_name}_VAL_WORSE_THAN_PROFILE("
                        f"val_r2={val_r2:.4f}<{hp_val:.4f}-0.02, prop_train_r2={prop_tr_r2:.2f})")
                    continue
            elif best_ref_tr is not None:
                ref_tr_maes = [results_by_split[b]["train"]["mae"]["macro"]
                               for b in ref_models
                               if "train" in results_by_split[b]
                               and "mae" in results_by_split[b]["train"]]
                best_ref_tr_mae = min(ref_tr_maes) if ref_tr_maes else None
                tr_mae_worse = (tr_mae is None or best_ref_tr_mae is None
                                or tr_mae > best_ref_tr_mae)
                if tr_r2 < best_ref_tr - 0.05 and tr_mae_worse:
                    reasons.append(
                        f"{seq_name}_TRAIN_UNDERFIT_VS_BASELINE("
                        f"train_r2={tr_r2:.4f}<{best_ref_tr:.4f}-0.05)")

    return bool(reasons), reasons


def _extract_split_mae_f1(results_by_split: dict[str, dict],
                          pred_frames: dict[str, pd.DataFrame] | None,
                          model_name: str,
                          split: str,
                          on_thr: float) -> tuple[float | None, float | None]:
    """提取指定模型在给定划分（仅限 val/train，严禁传 test）上的 (MAE, F1@on_thr)。

    若 base_cfg.metrics 未显式包含 f1/mae（如精简单测配置），自动从同划分 pred_frames 补算，
    确保验证集双向验收门禁在任何指标配置下均具备一致的确定性口径。
    """
    by_sp = results_by_split.get(model_name, {}).get(split, {})
    mae_v = by_sp.get("mae", {}).get("macro")
    f1_v = by_sp.get("f1", {}).get("macro")
    if (mae_v is None or f1_v is None) and pred_frames and split in pred_frames:
        df_sp = pred_frames[split]
        p_col = f"pred_{model_name}"
        if not df_sp.empty and "target" in df_sp.columns and p_col in df_sp.columns:
            yt = df_sp["target"].to_numpy(dtype=np.float64)[:, None]
            yp = df_sp[p_col].to_numpy(dtype=np.float64)[:, None]
            if mae_v is None:
                mae_v = float(METRIC_REGISTRY.get("mae")(yt, yp)["macro"])
            if f1_v is None:
                f1_v = float(METRIC_REGISTRY.get("f1")(yt, yp, on_thr_w=on_thr)["macro"])
    return (float(mae_v) if mae_v is not None else None,
            float(f1_v) if f1_v is not None else None)


def _accept_scaled_candidate(raw_results_by_split: dict[str, dict],
                             raw_split_bands: dict[str, dict[str, float]],
                             scaled_results_by_split: dict[str, dict],
                             scaled_split_bands: dict[str, dict[str, float]],
                             on_thr: float,
                             raw_pred_frames: dict[str, pd.DataFrame] | None = None,
                             scaled_pred_frames: dict[str, pd.DataFrame] | None = None,
                             triggered_reasons: list[str] | None = None,
                             mae_min_rel_gain: float = VAL_MAE_MIN_REL_GAIN,
                             f1_max_drop: float = VAL_F1_MAX_DROP) -> tuple[bool, list[str]]:
    """第3步验证集双向验收门禁（仅用 val，无 val 时回退 train；严禁看 test 零泄漏）：

    候选 scaled 重训完成后，在验证集上与首轮 raw 正面对决，必须同时满足：
    1) 候选 scaled 自身未坍缩（train/val 带宽 >= on_thr）且 train/val R² 非负；
    2) 触发重训的序列模型在验证集上 MAE 实质改善：
       val_mae_scaled <= val_mae_raw * (1 - mae_min_rel_gain)（若首轮 raw 发生带宽坍缩致全 0 伪低 MAE，
       则凭非坍缩与 F1 唤醒放行）；
    3) 所有序列模型在验证集上 F1 不退化：val_f1_scaled >= val_f1_raw - f1_max_drop
       （如 778：scaled 虽降低波峰 MAE，但关机零点随 z(pbus) 漂移虚报激增，val F1 从 0.9743 跌至
        0.9366，跌幅 0.0377 > 0.015，本门禁自动拒绝切换并保留抗漂移的 raw）。
    """
    seq_models = [m for m in SEQ_MODEL_NAMES
                  if m in raw_results_by_split and m in scaled_results_by_split]
    if not seq_models:
        return False, ["NO_SEQ_MODEL_FOR_ACCEPTANCE"]

    triggered_set = {r.split("_", 1)[0] for r in (triggered_reasons or [])
                     if r.split("_", 1)[0] in SEQ_MODEL_NAMES}
    if not triggered_set:
        triggered_set = set(seq_models)

    accept_notes: list[str] = []
    reject_notes: list[str] = []
    for seq_name in seq_models:
        raw_by = raw_results_by_split[seq_name]
        sc_by = scaled_results_by_split[seq_name]
        eval_split = "val" if ("val" in raw_by and "val" in sc_by) else "train"

        sc_bands = scaled_split_bands.get(seq_name, {})
        sc_tr_band = sc_bands.get("train")
        sc_ev_band = sc_bands.get(eval_split)
        sc_tr_r2 = sc_by.get("train", {}).get("r2", {}).get("macro")
        sc_ev_r2 = sc_by.get(eval_split, {}).get("r2", {}).get("macro")

        if sc_tr_band is not None and sc_tr_band < on_thr:
            reject_notes.append(
                f"{seq_name}_SCALED_TRAIN_COLLAPSED(band={sc_tr_band:.2f}<{on_thr:.2f})")
            continue
        if sc_ev_band is not None and sc_ev_band < on_thr:
            reject_notes.append(
                f"{seq_name}_SCALED_{eval_split.upper()}_COLLAPSED("
                f"band={sc_ev_band:.2f}<{on_thr:.2f})")
            continue
        if sc_tr_r2 is not None and sc_tr_r2 < 0.0:
            reject_notes.append(
                f"{seq_name}_SCALED_TRAIN_NEG_R2(r2={sc_tr_r2:.4f}<0)")
            continue
        if sc_ev_r2 is not None and sc_ev_r2 < 0.0:
            reject_notes.append(
                f"{seq_name}_SCALED_{eval_split.upper()}_NEG_R2(r2={sc_ev_r2:.4f}<0)")
            continue

        raw_bands = raw_split_bands.get(seq_name, {})
        raw_tr_band = raw_bands.get("train")
        raw_ev_band = raw_bands.get(eval_split)
        raw_tr_r2 = raw_by.get("train", {}).get("r2", {}).get("macro")
        raw_ev_r2 = raw_by.get(eval_split, {}).get("r2", {}).get("macro")
        raw_collapsed = ((raw_tr_band is not None and raw_tr_band < on_thr)
                         or (raw_ev_band is not None and raw_ev_band < on_thr))
        raw_neg_r2 = ((raw_tr_r2 is not None and raw_tr_r2 < 0.0)
                      or (raw_ev_r2 is not None and raw_ev_r2 < 0.0))

        raw_mae, raw_f1 = _extract_split_mae_f1(
            raw_results_by_split, raw_pred_frames, seq_name, eval_split, on_thr)
        sc_mae, sc_f1 = _extract_split_mae_f1(
            scaled_results_by_split, scaled_pred_frames, seq_name, eval_split, on_thr)

        if raw_collapsed:
            if raw_f1 is not None and sc_f1 is not None and sc_f1 + 1e-9 < raw_f1:
                reject_notes.append(
                    f"{seq_name}_{eval_split.upper()}_F1_NOT_RECOVERED("
                    f"raw={raw_f1:.4f}->scaled={sc_f1:.4f})")
                continue
            mae_s = (f"{eval_split}_mae:{raw_mae:.2f}->{sc_mae:.2f}"
                     if (raw_mae is not None and sc_mae is not None) else f"{eval_split}_mae:n/a")
            f1_s = (f"{eval_split}_f1:{raw_f1:.4f}->{sc_f1:.4f}"
                    if (raw_f1 is not None and sc_f1 is not None) else f"{eval_split}_f1:n/a")
            accept_notes.append(f"{seq_name}_RECOVERED_FROM_COLLAPSE({mae_s}, {f1_s})")
            continue

        rel_gain = (((raw_mae - sc_mae) / raw_mae)
                    if (raw_mae is not None and sc_mae is not None and raw_mae > 1e-9) else 0.0)
        if seq_name in triggered_set and raw_mae is not None and sc_mae is not None:
            if sc_mae > raw_mae * (1.0 - mae_min_rel_gain) + 1e-9:
                reject_notes.append(
                    f"{seq_name}_{eval_split.upper()}_MAE_GAIN_INSUFFICIENT("
                    f"raw={raw_mae:.2f}->scaled={sc_mae:.2f}, "
                    f"gain={rel_gain:.1%}<{mae_min_rel_gain:.1%})")
                continue

        if not raw_neg_r2 and raw_f1 is not None and sc_f1 is not None:
            f1_drop = raw_f1 - sc_f1
            if f1_drop > f1_max_drop + 1e-9:
                reject_notes.append(
                    f"{seq_name}_{eval_split.upper()}_F1_DEGRADED("
                    f"raw={raw_f1:.4f}->scaled={sc_f1:.4f}, "
                    f"drop={f1_drop:.4f}>{f1_max_drop:.4f})")
                continue

        mae_str = (f"{eval_split}_mae:{raw_mae:.2f}->{sc_mae:.2f}({rel_gain:+.1%})"
                   if (raw_mae is not None and sc_mae is not None) else f"{eval_split}_mae:n/a")
        f1_str = (f"{eval_split}_f1:{raw_f1:.4f}->{sc_f1:.4f}"
                  if (raw_f1 is not None and sc_f1 is not None) else f"{eval_split}_f1:n/a")
        accept_notes.append(f"{seq_name}_VAL_DUAL_GATE_PASS({mae_str}, {f1_str})")

    if reject_notes:
        return False, reject_notes
    return bool(accept_notes), accept_notes


def _infer_trained_pbus_mode(meta: dict) -> str:
    """从训练 meta 识别该模型的 pbus_mode（兼容无 pbus_mode 字段的历史产物）。"""
    if meta.get("pbus_mode") in ("raw", "scaled"):
        return str(meta["pbus_mode"])
    fnames = meta.get("feature_names") or []
    if "pbus" in fnames:
        p_idx = fnames.index("pbus")
        return "scaled" if p_idx in (meta.get("scale_cols") or []) else "raw"
    return "raw"


class UserTaskError(Exception):
    """携带状态码的用户任务异常（映射到指南 §13 状态表）。"""

    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class UserTaskResult:
    user_key: str
    mode: str
    status: str
    message: str = ""
    output_dir: str | None = None
    metrics: dict | None = None


def _new_outdir(output_root: Path, user_key: str, mode: str) -> Path:
    """新建带时间戳的运行目录；同秒重复运行（如强制重跑）时追加序号保证唯一。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = Path(output_root) / user_key / mode
    out = base / stamp
    n = 1
    while out.exists():  # 不覆盖既有产物（含同秒内的 force 重跑）
        out = base / f"{stamp}_{n}"
        n += 1
    out.mkdir(parents=True)
    return out


def latest_done_dir(root: Path) -> Path | None:
    """找最近一次带 _DONE 标记的运行目录（断点续跑依据）。"""
    if not root.is_dir():
        return None
    done = [p for p in sorted(root.iterdir()) if p.is_dir() and (p / DONE_MARKER).exists()]
    return done[-1] if done else None


def _save_cleaned_csv(out: Path, name: str, df: pd.DataFrame, enabled: bool) -> None:
    """清洗后数据落盘：cleaned/<name>_cleaned.csv（时间索引列名 timestamp）。

    enabled 由配置 ``preprocess.save_cleaned_csv`` 控制（默认开启）；
    只写产物目录 outputs/，不触碰原始数据（§13 只读约束）。
    """
    if not enabled or df is None or df.empty:
        return
    path = out / "cleaned" / f"{name}_cleaned.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index_label="timestamp", encoding="utf-8")
    log.info("清洗后数据已保存: %s（%d 行 × %d 列）", path, len(df), df.shape[1])


def _dump(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")


# ---------------------------------------------------------------- 训练
def run_user_train(user_key: str, scan, user_cfg: dict, base_cfg: dict,
                   output_root: str | Path) -> UserTaskResult:
    """单用户训练全流程（§3–§11），返回带状态码的结果。"""
    mode = "train"
    out = _new_outdir(Path(output_root), user_key, mode)
    try:
        pp = base_cfg.get("preprocess", {})
        qcfg = base_cfg.get("quality", {})

        # —— §3/§4 数据接入与字段映射（物理含义必须由配置确认）
        field_map = user_cfg.get("bus_field_map") or base_cfg.get("bus_field_map")
        if not field_map:
            raise UserTaskError(Status.SCHEMA_UNCONFIRMED,
                                "未配置 bus_field_map，Ch 字段物理含义未确认（§3.2/§4）")
        dcfg = base_cfg.get("data", {})
        sentinels = dcfg.get("sentinel_values")
        bus_raw, bus_schema = CsvBusLoader().load(
            scan.bus_files, field_map, sentinels=sentinels,
            derive_phase_from_ptotal=bool(dcfg.get("derive_phase_from_ptotal", False)))
        branch_raw, branch_schema = CsvBranchLoader().load(scan.branch_files, sentinels=sentinels)
        fatal = [i for i in bus_schema["issues"] if "SCHEMA_UNCONFIRMED" in i or "不存在" in i or "缺少列" in i]
        if fatal:
            raise UserTaskError(Status.SCHEMA_UNCONFIRMED, "; ".join(fatal))
        if branch_schema["issues"]:
            raise UserTaskError(Status.DATA_MISSING_BRANCH_LABEL, "; ".join(branch_schema["issues"]))
        if bus_schema["issues"]:
            log.warning("schema 警告（%s）: %s", Status.DATA_UNIT_UNKNOWN, bus_schema["issues"])
        write_schema_report(out / "data_schema_report.json", bus_schema, branch_schema,
                            {"user_key": user_key, "field_map": field_map})

        # —— §6 清洗与质量门禁（原始数据只读，产物写 outputs/）
        allow_negative = bool(pp.get("allow_negative_power", False))
        cleaner = Cleaner(clip_negative=not allow_negative,
                          max_gap_interp=int(pp.get("max_gap_interp", 2)))
        bus_c, branch_c = cleaner.transform(bus_raw), cleaner.transform(branch_raw)
        # PT/CT 变比（与 on_thr_w 同级，默认 1.0=不校正）：总线二次侧->一次侧。
        # 质量打分（BOUNDS 1000V 二次侧 sanity 上限）必须在校正前（二次侧）执行，
        # 校正推迟至 day_gate 之后、ident/特征之前（一次侧 kW）。
        pt_ratio = float(user_cfg.get("pt_ratio", 1.0))
        ct_ratio = float(user_cfg.get("ct_ratio", 1.0))
        # 分路一次侧单位换算（到 kW 填 1000.0，默认 1.0=W 历史不变）
        branch_scale = float(user_cfg.get("branch_scale", 1.0))
        if branch_scale != 1.0:
            branch_c = apply_branch_scale(branch_c, branch_scale)
            log.info("[%s] 分路单位换算 branch_scale=%.4g（到 kW）", user_key, branch_scale)
        save_cleaned = bool(pp.get("save_cleaned_csv", True))
        _save_cleaned_csv(out, "bus", bus_c, save_cleaned)

        # —— §3.3/§12.3 目标列契约（提前解析）：配置的目标通道为唯一有效分路，
        #    其余通道不在当前总线回路中（无效通道数据），清洗后立即丢弃——
        #    后续所有环节（清洗产物/开机分析/质量报告/门禁/无效天/评估）只见有效通道
        target_cols = resolve_target_cols(user_cfg.get("target_col"), branch_c)
        invalid_ch = [c for c in branch_c.columns if c not in target_cols]
        if invalid_ch:
            log.info("[%s] 丢弃无效分路通道 %s（不在当前总线回路，仅保留目标通道 %s）",
                     user_key, invalid_ch, target_cols)
            branch_c = branch_c[target_cols]
        _save_cleaned_csv(out, "branch", branch_c, save_cleaned)
        target = build_target(branch_c, target_cols)

        # —— 解析 on_thr_w（显式数值优先；配置为 "auto" 时基于训练时间窗分路分布对数双峰寻谷，黄/红日志提示）
        cfg_on_thr_raw = user_cfg.get("on_thr_w", 10.0)
        tspec_pre = user_cfg.get("train") or {}
        target_for_on_thr = target
        if tspec_pre.get("include") or tspec_pre.get("exclude"):
            _br_tr = filter_dataframe(branch_c, tspec_pre.get("include"), tspec_pre.get("exclude"))
            if len(_br_tr) > 0:
                target_for_on_thr = build_target(_br_tr, target_cols)
        on_thr, on_thr_decision = resolve_train_on_thr_w(user_key, user_cfg, target_for_on_thr)

        # —— 训练前分路开机情况分析（有效通道=目标通道，逐天开机段/时长/功率/电量）
        sessions = analyze_branch_sessions(branch_c, on_thr,
                                           columns=target_cols)
        sessions.to_csv(out / "branch_sessions.csv", index=False, encoding="utf-8")

        # —— §5 统一 15min（聚合策略可配置且记录）
        bus15, agg_record = resample_bus(bus_c, strategy=pp.get("agg_strategy"))
        _dump(out / "agg_strategy.json", agg_record)

        # —— 时间对齐（重叠率门禁；偏移只报告不改戳）
        bus_al, branch_al = align_frames(bus15, branch_c,
                                         min_overlap=float(pp.get("min_overlap", 0.5)))
        target = target.loc[branch_al.index]
        _dump(out / "time_offset.json",
              estimate_time_offset(bus_total(bus_al), target))

        q_bus = quality_report(bus_al, "bus", 96, allow_negative,
                               on_thr_w=on_thr)
        # branch 报告=有效通道（目标通道）口径：无效通道已在清洗后丢弃
        q_br = quality_report(branch_al, "branch", 96, allow_negative,
                              on_thr_w=on_thr)
        q_br["target_cols"] = target_cols
        # 逐天质量表（总线/分路得分、阈值、当天合格）+ 双达标统计 + 训练建议
        min_score = float(qcfg.get("min_score", 50))
        daily_q = daily_quality_table(bus_al, branch_al, 96, min_score, allow_negative)
        daily_q.to_csv(out / "daily_quality.csv", index=False, encoding="utf-8")
        advice = quality_advice(daily_q, float(qcfg.get("min_days", 14)))
        # 各自达标天数（任务⑫）：总线/分路单独达标+仅单侧达标，随质量报告进 result JSON
        q_side = qualified_days_counts(daily_q)
        q_br["both_qualified_days"] = q_side["both_qualified_days"]
        q_br["daily_total_days"] = q_side["total_days"]
        q_bus["qualified_days_by_side"] = q_side
        q_br["qualified_days_by_side"] = q_side
        _dump(out / "quality_advice.json", {"advice": advice})
        write_quality_html(out / "data_quality_report.html", [q_bus, q_br],
                           daily_quality=daily_q, advice=advice)
        # 质量门禁范围（任务⑬ 2844 放行 B）：full=全范围（现状默认）；train_range=门禁后移——
        # 改判「时间过滤后的训练范围」（全范围报告仍产出仅供诊断；配合 time_filters 缩窗）
        gate_scope = str((user_cfg.get("quality") or {}).get("gate_scope")
                         or qcfg.get("gate_scope", "full"))
        # 日级放行机制（任务⑭策略，用户级 quality.day_gate 启用）：以「总线与分路
        # 同时达标天」为放行/训练单位，优先级高于 gate_scope（day 级比 range 级更细）；
        # min_on_day_ratio 为可选开机日占比门禁（0~1，缺省不启用）
        day_gate = bool((user_cfg.get("quality") or {}).get("day_gate")
                        or qcfg.get("day_gate", False))
        _ratio_cfg = (user_cfg.get("quality") or {}).get("min_on_day_ratio")
        if _ratio_cfg is None:
            _ratio_cfg = qcfg.get("min_on_day_ratio")
        min_on_ratio = float(_ratio_cfg) if _ratio_cfg is not None else None
        if day_gate:
            log.info("[%s] 日级放行机制启用（quality.day_gate）：训练天=总线与分路同时达标天"
                     "（逐天得分≥%.0f）%s", user_key, float(qcfg.get("min_score", 50)),
                     f"；开机日占比门禁 {min_on_ratio:.0%}" if min_on_ratio is not None
                     else "（未配置开机日占比门禁，双达标天默认全部参与训练）")
        elif gate_scope != "train_range":
            assert_quality(q_bus, qcfg.get("max_missing_rate", 0.3),
                           qcfg.get("min_coverage", 0.5), qcfg.get("min_score", 50))
            assert_quality(q_br, qcfg.get("max_missing_rate", 0.3),
                           qcfg.get("min_coverage", 0.5), qcfg.get("min_score", 50))

        # —— §12.4 train 时间过滤
        tspec = user_cfg.get("train") or {}
        if tspec.get("include") or tspec.get("exclude"):
            bus_al = filter_dataframe(bus_al, tspec.get("include"), tspec.get("exclude"))
            branch_al = filter_dataframe(branch_al, tspec.get("include"), tspec.get("exclude"))
            target = target.loc[target.index.intersection(bus_al.index)]

        # —— 训练范围质量门禁（gate_scope=train_range，任务⑬ 2844 放行 B）：
        #    判「缩窗后实际参与训练的范围」；总线离线段已被时间过滤剔除，全范围门禁不适用
        if gate_scope == "train_range" and not day_gate:
            q_bus_tr = quality_report(bus_al, "bus(训练范围)", 96, allow_negative,
                                      on_thr_w=on_thr)
            q_br_tr = quality_report(branch_al, "branch(训练范围)", 96, allow_negative,
                                     on_thr_w=on_thr)
            q_bus["train_range_quality"] = q_bus_tr      # 随 result JSON 留痕
            q_br["train_range_quality"] = q_br_tr
            log.info("[%s] 训练范围质量门禁（gate_scope=train_range）: bus %.2f / branch %.2f"
                     "（阈值 %.0f，范围约 %d 天）",
                     user_key, q_bus_tr["quality_score"], q_br_tr["quality_score"],
                     float(qcfg.get("min_score", 50)), q_bus_tr["n_days_approx"])
            assert_quality(q_bus_tr, qcfg.get("max_missing_rate", 0.3),
                           qcfg.get("min_coverage", 0.5), qcfg.get("min_score", 50))
            assert_quality(q_br_tr, qcfg.get("max_missing_rate", 0.3),
                           qcfg.get("min_coverage", 0.5), qcfg.get("min_score", 50))

        # —— 日级无效天剔除：总线或分路全天缺失/缺失率超阈值的天不参与训练与评估
        daily_thr = float(qcfg.get("max_daily_missing_rate", 1.0))
        bad_days = sorted(set(invalid_data_days(bus_al, 96, daily_thr)) |
                          set(invalid_data_days(branch_al, 96, daily_thr)))
        if bad_days:
            bad_set = set(bad_days)
            keep_bus = ~bus_al.index.normalize().isin(bad_set)
            keep_br = ~branch_al.index.normalize().isin(bad_set)
            bus_al, branch_al = bus_al[keep_bus], branch_al[keep_br]
            target = target[~target.index.normalize().isin(bad_set)]
            log.warning("[%s] 剔除无效天 %d 天（全天缺失或日缺失率>%.0f%%）: %s",
                        user_key, len(bad_days), daily_thr * 100,
                        [d.strftime("%Y-%m-%d") for d in bad_days])
        _dump(out / "excluded_days.json", {
            "max_daily_missing_rate": daily_thr,
            "excluded_days": [d.strftime("%Y-%m-%d") for d in bad_days],
        })

        # —— 日级放行筛选（day_gate，任务⑭策略步骤③）：只保留「总线与分路同时达标」
        #    的天参与训练/评估；可选 min_on_day_ratio 对双达标天中的开机日占比设门禁
        if day_gate:
            qual = set(daily_q.loc[daily_q["qualified"] == 1, "date"].astype(str))
            kept = sorted({d.strftime("%Y-%m-%d") for d in bus_al.index.normalize()}
                          | {d.strftime("%Y-%m-%d") for d in branch_al.index.normalize()})
            drop_days = sorted(set(kept) - qual)
            if not qual.intersection(kept):
                raise UserTaskError(Status.DATA_QUALITY_FAILED,
                                    f"训练范围内无双达标天（逐天质量得分均<"
                                    f"{float(qcfg.get('min_score', 50)):.0f}），无可训练数据")
            if drop_days:
                drop_set = {pd.Timestamp(d) for d in drop_days}
                bus_al = bus_al[~bus_al.index.normalize().isin(drop_set)]
                branch_al = branch_al[~branch_al.index.normalize().isin(drop_set)]
                target = target[~target.index.normalize().isin(drop_set)]
                log.warning("[%s] 日级放行剔除非双达标天 %d 天: %s", user_key, len(drop_days),
                            drop_days[:12] + (["…"] if len(drop_days) > 12 else []))
            t_d = target.dropna()
            day_max = (t_d.groupby(t_d.index.normalize()).max()
                       if len(t_d) else pd.Series(dtype=float))
            off_n = int((day_max < on_thr).sum())
            on_n = int((day_max >= on_thr).sum())
            n_both = on_n + off_n
            ratio = (on_n / n_both) if n_both else 0.0
            gate_line = f"双达标天 {n_both}（开机日 {on_n} / 全关日 {off_n}，开机日占比 {ratio:.1%}）"
            if min_on_ratio is not None and n_both and ratio < min_on_ratio:
                raise UserTaskError(Status.DATA_QUALITY_FAILED,
                                    f"开机日占比 {ratio:.1%} < 配置阈值 {min_on_ratio:.0%}"
                                    f"（{gate_line}）")
            log.info("[%s] 日级放行统计: %s%s", user_key, gate_line,
                     f"；门禁 {min_on_ratio:.0%} PASS" if min_on_ratio is not None else "")
            dg = {"enabled": True, "min_on_day_ratio": min_on_ratio,
                  "both_qualified_days": n_both, "on_days": on_n, "off_days": off_n,
                  "on_day_ratio": round(ratio, 4),
                  "excluded_unqualified_days": drop_days}
            q_bus["day_gate"] = dg       # 随 result JSON 留痕
            q_br["day_gate"] = dg
            _dump(out / "day_gate.json", dg)

        if len(bus_al) < 96 * float(qcfg.get("min_days", 14)):
            raise UserTaskError(Status.INSUFFICIENT_TIME_RANGE,
                                f"有效数据不足 {qcfg.get('min_days', 14)} 天（现有 {len(bus_al)/96:.1f} 天）")

        # PT/CT 校正（二次侧->一次侧 kW）：质量已在二次侧打分，此后才转一次侧 kW
        # 供 ident/特征使用（pt/ct=1.0 时仅功率 W->kW /1000）
        bus_al = apply_pt_ct(bus_al, pt_ratio, ct_ratio)
        log.info("[%s] PT/CT 校正 pt=%.4g ct=%.4g（综合 %.4g，功率转 kW）",
                 user_key, pt_ratio, ct_ratio, pt_ratio * ct_ratio)

        # —— §9 可辨识性分析（训练前强制执行）
        ident = identifiability_report(bus_al, target, on_thr_w=on_thr)
        _dump(out / "identifiability_report.json", ident)
        if not ident.get("identifiable", True):
            log.warning("[%s] %s: %s", user_key, Status.IDENTIFIABILITY_LOW, ident.get("risk"))

        # —— §8 特征 + §10/§11 样本与切分
        # 允许 time_filters per-user 覆盖 features（P0 lag5 等），user 优先于 base
        fc = {**base_cfg.get("features", {}), **(user_cfg.get("features") or {})}
        feat = build_features(bus_al, lags=tuple(fc.get("lags", [1, 2, 3, 4])),
                              rolling_windows=tuple(fc.get("rolling_windows", ["1h", "6h", "24h"])))
        f, y = drop_invalid_rows(feat, target)
        window = int(base_cfg.get("dataset", {}).get("window", DEFAULT_WINDOW))
        if len(f) < window * 2:
            raise UserTaskError(Status.INSUFFICIENT_TIME_RANGE,
                                f"有效样本 {len(f)} < 2×L({window})")

        masks = build_split_masks(f.index, user_cfg["split_ratios"],
                                  user_cfg["split_strategy"], user_cfg.get("splits"),
                                  target=target, on_thr_w=on_thr)
        names = [str(c) for c in f.columns]
        X_all = f.to_numpy(np.float64)
        y_all = y.to_numpy(np.float64)[:, None]  # 统一 (n, 1) 多输出矩阵接口
        splits = {k: (X_all[m.to_numpy()], y_all[m.to_numpy()], f.index[m.to_numpy()])
                  for k, m in masks.items()}
        split_sizes = {k: len(v[0]) for k, v in splits.items()}
        log.info("[%s] 切分（%s）: %s", user_key, user_cfg["split_strategy"], split_sizes)
        if split_sizes["test"] == 0 or split_sizes["train"] < window:
            raise UserTaskError(Status.INSUFFICIENT_TIME_RANGE, f"切分后样本不足: {split_sizes}")

        # —— 双达标天清洗后统计：总/全关/训练/验证/测试天数 + 每天明细（全关/阈值/所属集）
        q_detail = qualified_days_detail(
            daily_q, target, on_thr,
            split_index={k: splits[k][2] for k in ("train", "val", "test")
                         if split_sizes.get(k, 0) > 0})
        q_detail.to_csv(out / "qualified_days_detail.csv", index=False, encoding="utf-8")
        q_br["qualified_days_stats"] = qualified_days_summary(q_detail)
        advice = advice + split_coverage_advice(q_detail)
        _dump(out / "quality_advice.json", {"advice": advice})
        write_quality_html(out / "data_quality_report.html", [q_bus, q_br],
                           daily_quality=daily_q, advice=advice,
                           qualified_detail=q_detail)  # 重写含双达标统计

        # Scaler 只由 Train 拟合（§11）；按用户级 pbus_mode 选择（默认 "auto" 自动判定+欠拟合熔断）
        wmode = base_cfg.get("dataset", {}).get("mode", "seq2seq")
        metric_names = base_cfg.get("metrics", ["mae", "rmse", "r2", "sae"])
        pbus_col = names.index("pbus")
        cfg_pbus_mode = str(user_cfg.get("pbus_mode") or "auto")
        if cfg_pbus_mode == "auto":
            pbus_mode, pbus_decision = resolve_auto_pbus_mode(
                splits["train"][0][:, pbus_col], splits["train"][1][:, 0],
                on_thr_w=on_thr)
            log.info("[%s] [auto] 默认先试 pbus_mode=%s（train_p95=%.1f, std=%.1f, "
                     "on_contrib=%.4f, duty=%.1f%%, all_contrib=%.4f, reasons=%s）",
                     user_key, pbus_mode, pbus_decision["train_pbus_p95"],
                     pbus_decision["train_pbus_std"],
                     pbus_decision["train_on_contrib_ratio"],
                     pbus_decision["train_on_duty_ratio"] * 100.0,
                     pbus_decision["train_contrib_ratio"],
                     pbus_decision["reasons"])
        else:
            pbus_mode = cfg_pbus_mode
            pbus_decision = {
                "configured_mode": cfg_pbus_mode,
                "resolved_mode": cfg_pbus_mode,
                "stage": "explicit",
                "reasons": ["EXPLICIT_USER_CONFIG"],
            }

        cfg_dec_thr_raw = user_cfg.get("decision_thr_w", "auto")
        cfg_post_min_on_raw = user_cfg.get("post_min_on", 1)
        cfg_post_fill_off_raw = user_cfg.get("post_fill_short_off", 3)
        postproc_is_auto = (
            (isinstance(cfg_post_min_on_raw, str) and cfg_post_min_on_raw.strip().lower() == "auto")
            or (isinstance(cfg_post_fill_off_raw, str) and cfg_post_fill_off_raw.strip().lower() == "auto")
        )
        non_test_mask = ~masks["test"].to_numpy()
        postproc_bounds = (
            resolve_postprocess_physical_bounds(
                y.to_numpy(np.float64)[non_test_mask],
                f.index[non_test_mask],
                on_thr_w=on_thr,
            )
            if postproc_is_auto else None
        )
        mp_override = user_cfg.get("model_params") or {}

        def _run_pbus_mode_pass(eff_mode: str):
            non_scaled_local = _non_scaled_cols(eff_mode)
            log.info("[%s] pbus_mode=%s（配置=%s；%s）", user_key, eff_mode, cfg_pbus_mode,
                     "pbus z-score + proportional 线性改学"
                     if eff_mode == "scaled" else "pbus 未缩放 + proportional 物理分摊")
            sc_cols = [i for i, c in enumerate(names) if c not in non_scaled_local]
            sc = TrainFitScaler().fit(splits["train"][0], cols=sc_cols)
            sc_splits = {k: (sc.transform(v[0]), v[1], v[2]) for k, v in splits.items()}

            fitted_models_local: dict[str, BaseModel] = {}
            res_local: dict[str, dict] = {}
            res_by_sp_local: dict[str, dict] = {}
            daily_local: list[pd.DataFrame] = []
            daily_chain_local: list[pd.DataFrame] = []
            test_preds_local: dict[str, np.ndarray] = {}
            collapsed_local: list[dict] = []
            split_bands_local: dict[str, dict[str, float]] = {}
            raw_preds_by_sp_local: dict[str, dict[str, np.ndarray]] = {}

            for spec in base_cfg.get("models", []):
                name, params = spec["name"], dict(spec.get("params", {}))
                # pbus_mode 双路径联动：proportional 默认跟随 eff_mode（raw->分摊, scaled->改学）
                if name == "proportional" and "mode" not in params:
                    params["mode"] = eff_mode
                extra = mp_override.get(name) or {}
                if extra:
                    log.info("[train] 用户级模型参数覆盖 %s: %s", name, extra)
                    params.update(extra)
                if name == "proportional":
                    eff_prop = {"share": "raw", "linear": "scaled"}.get(
                        params.get("mode"), params.get("mode"))
                    if eff_prop != eff_mode:
                        log.warning("[%s] proportional.mode=%r 与 pbus_mode=%r 不一致"
                                    "（可能触发量级失配或 F1=0 退化）",
                                    user_key, params.get("mode"), eff_mode)
                model = MODEL_REGISTRY.create(name, **params)
                model.fit(sc_splits["train"][0], sc_splits["train"][1], feature_names=names,
                          X_val=sc_splits["val"][0], y_val=sc_splits["val"][1],
                          index=splits["train"][2],
                          val_index=(splits["val"][2] if split_sizes.get("val", 0) > 0 else None))
                fitted_models_local[name] = model

                res_by_sp_local[name] = {}
                split_bands_local[name] = {}
                raw_preds_by_sp_local[name] = {}
                for split in ("train", "val", "test"):
                    if len(sc_splits[split][0]) == 0:
                        continue
                    y_hat = model.predict(sc_splits[split][0], index=splits[split][2])
                    y_hat = apply_constraints(y_hat, splits[split][0][:, pbus_col],
                                              nonnegative=not allow_negative,
                                              sum_consistency=False)
                    raw_preds_by_sp_local[name][split] = y_hat
                    metrics = evaluate_all(sc_splits[split][1], y_hat, metric_names,
                                           on_thr_w=on_thr)
                    res_by_sp_local[name][split] = metrics
                    band_sp = float(y_hat[:, 0].max() - y_hat[:, 0].min())
                    split_bands_local[name][split] = band_sp
                    daily = evaluate_daily(sc_splits[split][1], y_hat, splits[split][2],
                                           metric_names, on_thr_w=on_thr)
                    daily.insert(0, "split", split)
                    daily.insert(0, "model", name)
                    daily["state_thr_w"] = on_thr
                    daily_local.append(daily)
                    log.info("[%s] 模型 %s %s 指标: %s", user_key, name, split,
                             {m: round(v["macro"], 4) for m, v in metrics.items()})
                    if split == "test":
                        test_preds_local[name] = y_hat[:, 0]
                        if band_sp < on_thr:
                            collapsed_local.append(
                                {"model": name, "pred_band_w": round(band_sp, 2),
                                 "on_thr_w": on_thr})
                            log.warning(
                                "[%s] PRED_COLLAPSED：模型 %s test 预测带宽 %.1fW < "
                                "on_thr_w %.0fW——预测坍缩（疑似欠训练），该模型无法识别"
                                "任何开机点，勿用于推理", user_key, name, band_sp, on_thr)
                res_local[name] = res_by_sp_local[name]["test"]

            # 零泄漏解析本趟 (decision_thr_w, post_min_on, post_fill_short_off)：
            # 显式数值 100% 优先；auto 时基于阶段一物理上界 (M_max, K_max) 在 val（空则 train）上校准
            prov_map = user_cfg.get("_provenance") or {}
            prov_dec = prov_map.get("decision_thr_w", "default(硬编码)")
            prov_min_on = prov_map.get("post_min_on", "default(硬编码)")
            prov_fill_off = prov_map.get("post_fill_short_off", "default(硬编码)")

            if postproc_is_auto:
                cal_model = select_calibration_model_zero_leakage(
                    user_cfg, base_cfg, res_by_sp_local, split_bands_local, on_thr
                )
                model_dec_map_local = {}
                model_postproc_map_local: dict[str, dict[str, int]] = {}
                per_model_dec_info: dict[str, dict] = {}
                per_model_postproc_info: dict[str, dict] = {}
                y_tr_1d = sc_splits["train"][1][:, 0]
                y_va_1d = sc_splits["val"][1][:, 0] if len(sc_splits["val"][0]) > 0 else np.array([], dtype=np.float64)
                idx_va = splits["val"][2] if len(sc_splits["val"][0]) > 0 else None
                idx_tr = splits["train"][2]
                for mname in fitted_models_local:
                    p_va_1d = (raw_preds_by_sp_local[mname]["val"][:, 0]
                               if "val" in raw_preds_by_sp_local[mname]
                               else np.array([], dtype=np.float64))
                    p_tr_1d = (raw_preds_by_sp_local[mname]["train"][:, 0]
                               if "train" in raw_preds_by_sp_local[mname]
                               else None)
                    m_thr, m_mo, m_fo, m_dec_info, m_pp_info = resolve_auto_postprocess_and_decision_thr(
                        y_train=y_tr_1d,
                        y_val=y_va_1d,
                        pred_val=p_va_1d,
                        val_index=idx_va,
                        on_thr_w=on_thr,
                        cfg_decision_thr_w=cfg_dec_thr_raw,
                        cfg_post_min_on=cfg_post_min_on_raw,
                        cfg_post_fill_off=cfg_post_fill_off_raw,
                        bounds=postproc_bounds,
                        pred_train=p_tr_1d,
                        train_index=idx_tr,
                        calibrated_model=mname,
                        pbus_mode=eff_mode,
                    )
                    model_dec_map_local[mname] = m_thr
                    model_postproc_map_local[mname] = {
                        "post_min_on": int(m_mo),
                        "post_fill_short_off": int(m_fo),
                    }
                    per_model_dec_info[mname] = m_dec_info
                    per_model_postproc_info[mname] = m_pp_info

                default_mo = 1 if cfg_post_min_on_raw == "auto" else int(cfg_post_min_on_raw)
                default_fo = (
                    int(min(3, postproc_bounds["k_max"]))
                    if (cfg_post_fill_off_raw == "auto" and postproc_bounds is not None)
                    else (3 if cfg_post_fill_off_raw == "auto" else int(cfg_post_fill_off_raw))
                )
                dec_thr_local = float(model_dec_map_local.get(cal_model, on_thr))
                cal_pp = model_postproc_map_local.get(cal_model) or {
                    "post_min_on": default_mo,
                    "post_fill_short_off": default_fo,
                }
                post_min_on_local = int(cal_pp["post_min_on"])
                post_fill_off_local = int(cal_pp["post_fill_short_off"])

                dec_decision_local = dict(per_model_dec_info.get(cal_model, {
                    "configured": cfg_dec_thr_raw,
                    "resolved_decision_thr_w": dec_thr_local,
                    "stage": "auto_fallback_on_thr",
                    "confidence": "low",
                    "calibrated_model": cal_model,
                    "reasons": ["NO_MODEL_AVAILABLE"],
                }))
                dec_decision_local["provenance"] = prov_dec
                dec_decision_local["model_decision_thr_w"] = model_dec_map_local

                postproc_decision_local = dict(per_model_postproc_info.get(cal_model, {
                    "configured_post_min_on": cfg_post_min_on_raw,
                    "configured_post_fill_short_off": cfg_post_fill_off_raw,
                    "resolved_post_min_on": post_min_on_local,
                    "resolved_post_fill_short_off": post_fill_off_local,
                    "stage": "auto_fallback_anchor",
                    "confidence": "low",
                    "calibrated_model": cal_model,
                    "reasons": ["NO_MODEL_AVAILABLE"],
                }))
                postproc_decision_local["provenance"] = {
                    "post_min_on": prov_min_on,
                    "post_fill_short_off": prov_fill_off,
                }
                postproc_decision_local["model_postprocess_params"] = model_postproc_map_local
            else:
                post_min_on_local = int(cfg_post_min_on_raw)
                post_fill_off_local = int(cfg_post_fill_off_raw)
                model_postproc_map_local = {
                    m: {"post_min_on": post_min_on_local, "post_fill_short_off": post_fill_off_local}
                    for m in fitted_models_local
                }
                postproc_decision_local = {
                    "configured_post_min_on": post_min_on_local,
                    "configured_post_fill_short_off": post_fill_off_local,
                    "resolved_post_min_on": post_min_on_local,
                    "resolved_post_fill_short_off": post_fill_off_local,
                    "stage": "explicit",
                    "confidence": "explicit",
                    "provenance": {"post_min_on": prov_min_on, "post_fill_short_off": prov_fill_off},
                    "reasons": ["EXPLICIT_OR_DEFAULT_INT"],
                }
                if isinstance(cfg_dec_thr_raw, (int, float)) and not isinstance(cfg_dec_thr_raw, bool):
                    dec_thr_local = float(cfg_dec_thr_raw)
                    model_dec_map_local = {m: dec_thr_local for m in fitted_models_local}
                    dec_decision_local = {
                        "configured": dec_thr_local,
                        "resolved_decision_thr_w": dec_thr_local,
                        "stage": "explicit",
                        "confidence": "explicit",
                        "provenance": prov_dec,
                        "reasons": ["EXPLICIT_USER_CONFIG"],
                    }
                elif isinstance(cfg_dec_thr_raw, str) and cfg_dec_thr_raw.strip().lower() == "on_thr":
                    dec_thr_local = float(on_thr)
                    model_dec_map_local = {m: dec_thr_local for m in fitted_models_local}
                    dec_decision_local = {
                        "configured": "on_thr",
                        "resolved_decision_thr_w": dec_thr_local,
                        "stage": "explicit_on_thr",
                        "confidence": "explicit",
                        "provenance": prov_dec,
                        "reasons": ["EXPLICIT_LOCK_ON_THR"],
                    }
                else:
                    cal_model = select_calibration_model_zero_leakage(
                        user_cfg, base_cfg, res_by_sp_local, split_bands_local, on_thr
                    )
                    model_dec_map_local = {}
                    per_model_dec_info = {}
                    y_tr_1d = sc_splits["train"][1][:, 0]
                    y_va_1d = sc_splits["val"][1][:, 0] if len(sc_splits["val"][0]) > 0 else np.array([], dtype=np.float64)
                    idx_va = splits["val"][2] if len(sc_splits["val"][0]) > 0 else None
                    idx_tr = splits["train"][2]
                    for mname in fitted_models_local:
                        p_va_1d = (raw_preds_by_sp_local[mname]["val"][:, 0]
                                   if "val" in raw_preds_by_sp_local[mname]
                                   else np.array([], dtype=np.float64))
                        p_tr_1d = (raw_preds_by_sp_local[mname]["train"][:, 0]
                                   if "train" in raw_preds_by_sp_local[mname]
                                   else None)
                        m_thr, m_info = resolve_auto_decision_thr_w(
                            y_train=y_tr_1d,
                            y_val=y_va_1d,
                            pred_val=p_va_1d,
                            val_index=idx_va,
                            on_thr_w=on_thr,
                            post_min_on=post_min_on_local,
                            post_fill_off=post_fill_off_local,
                            pred_train=p_tr_1d,
                            train_index=idx_tr,
                            calibrated_model=mname,
                            pbus_mode=eff_mode,
                        )
                        model_dec_map_local[mname] = m_thr
                        per_model_dec_info[mname] = m_info
                    dec_thr_local = float(model_dec_map_local.get(cal_model, on_thr))
                    dec_decision_local = dict(per_model_dec_info.get(cal_model, {
                        "configured": "auto",
                        "resolved_decision_thr_w": dec_thr_local,
                        "stage": "auto_fallback_on_thr",
                        "confidence": "low",
                        "calibrated_model": cal_model,
                        "reasons": ["NO_MODEL_AVAILABLE"],
                    }))
                    dec_decision_local["provenance"] = prov_dec
                    dec_decision_local["model_decision_thr_w"] = model_dec_map_local

            # 统一使用本趟最终敲定的单值 (dec_thr_local, post_min_on_local, post_fill_off_local) 生成判决链（保证 T3/T4 跨产物逐位一致）
            pred_frames_local: dict[str, pd.DataFrame] = {
                s: pd.DataFrame({"timestamp": splits[s][2]
                                 .strftime("%Y-%m-%d %H:%M:%S"),
                                 "split": s, "target": splits[s][1][:, 0],
                                 "target_state": (splits[s][1][:, 0] >= on_thr).astype(int),
                                 "on_thr_w": on_thr, "decision_thr_w": dec_thr_local})
                for s in ("train", "val", "test") if split_sizes.get(s, 0) > 0}
            for name in fitted_models_local:
                for split in ("train", "val", "test"):
                    if split not in raw_preds_by_sp_local[name]:
                        continue
                    y_hat = raw_preds_by_sp_local[name][split]
                    pred_state_arr = postprocess_state(
                        y_hat[:, 0], dec_thr_local, post_min_on_local, post_fill_off_local).astype(int)
                    daily_chain = evaluate_daily_chain(
                        sc_splits[split][1], y_hat, pred_state_arr, splits[split][2],
                        metric_names, on_thr_w=on_thr)
                    daily_chain.insert(0, "split", split)
                    daily_chain.insert(0, "model", name)
                    daily_chain["state_thr_w"] = on_thr
                    daily_chain["decision_thr_w"] = dec_thr_local
                    daily_chain["post_min_on"] = post_min_on_local
                    daily_chain["post_fill_short_off"] = post_fill_off_local
                    daily_chain_local.append(daily_chain)
                    pred_frames_local[split][f"pred_{name}"] = y_hat[:, 0]
                    pred_frames_local[split][f"pred_state_{name}"] = pred_state_arr

            return (sc_cols, sc, sc_splits, fitted_models_local, res_local,
                    res_by_sp_local, daily_local, daily_chain_local,
                    test_preds_local, collapsed_local, pred_frames_local,
                    split_bands_local, dec_thr_local, dec_decision_local,
                    model_dec_map_local, post_min_on_local, post_fill_off_local,
                    postproc_decision_local, model_postproc_map_local)

        (scale_cols, scaler, scaled, fitted_models, results, results_by_split,
         daily_rows, daily_chain_rows, test_preds, collapsed_models,
         pred_frames, split_bands, dec_thr, dec_decision,
         model_dec_map, post_min_on, post_fill_off,
         postproc_decision, model_postproc_map) = _run_pbus_mode_pass(pbus_mode)

        # 第2–3步闭环（仅 auto 模式）：默认跑完 raw 后，若 train/val 检测到真实欠拟合，
        # 启动候选 scaled 重训并执行验证集双向验收（MAE 实质改善 + F1 不退化，零泄漏禁看 test）：
        # 通过则红色高亮切换为 scaled；未通过则拒绝切换、自动保留抗漂移的 raw。
        if cfg_pbus_mode == "auto" and pbus_mode == "raw":
            need_fb, fb_reasons = _should_fallback_raw_to_scaled(
                results_by_split, split_bands, on_thr)
            if need_fb:
                log.info(
                    "[%s] [auto] 首轮 pbus_mode=raw 触发候选重训条件（%s），"
                    "启动候选 pbus_mode=scaled 重训并执行验证集双向验收...",
                    user_key, "; ".join(fb_reasons))
                cand_bundle = _run_pbus_mode_pass("scaled")
                (c_sc_cols, c_scaler, c_scaled, c_models, c_results, c_res_by_sp,
                 c_daily, c_daily_chain, c_test_preds, c_collapsed,
                 c_pred_frames, c_split_bands, c_dec_thr, c_dec_decision,
                 c_model_dec_map, c_post_min_on, c_post_fill_off,
                 c_postproc_decision, c_model_postproc_map) = cand_bundle
                accepted, gate_notes = _accept_scaled_candidate(
                    results_by_split, split_bands,
                    c_res_by_sp, c_split_bands,
                    on_thr=on_thr,
                    raw_pred_frames=pred_frames,
                    scaled_pred_frames=c_pred_frames,
                    triggered_reasons=fb_reasons)
                pbus_decision["fallback_reasons"] = fb_reasons
                pbus_decision["val_acceptance"] = {
                    "accepted": accepted,
                    "details": gate_notes,
                }
                if accepted:
                    log.warning("%s", red_text(
                        f"[{user_key}] [AUTO-FALLBACK] 首轮 pbus_mode=raw 触发欠拟合"
                        f"（{'; '.join(fb_reasons)}），且候选 scaled 通过验证集双向验收"
                        f"（{'; '.join(gate_notes)}），自动重训切换为 pbus_mode=scaled！"
                    ))
                    pbus_mode = "scaled"
                    pbus_decision["resolved_mode"] = "scaled"
                    pbus_decision["stage"] = "post_fallback"
                    (scale_cols, scaler, scaled, fitted_models, results, results_by_split,
                     daily_rows, daily_chain_rows, test_preds, collapsed_models,
                     pred_frames, split_bands, dec_thr, dec_decision,
                     model_dec_map, post_min_on, post_fill_off,
                     postproc_decision, model_postproc_map) = cand_bundle
                else:
                    log.warning(
                        "[%s] [AUTO-REJECT-SCALED] 候选 pbus_mode=scaled 未通过验证集双向验收"
                        "（%s；触发原因: %s），拒绝切换，自动保留抗漂移的 pbus_mode=raw！",
                        user_key, "; ".join(gate_notes), "; ".join(fb_reasons))
                    pbus_decision["resolved_mode"] = "raw"
                    pbus_decision["stage"] = "post_fallback_rejected"

        # 最终胜出 pbus_mode 确定后，若 decision_thr_w 或游程参数配置为 auto，输出一次黄/红提示日志
        log_auto_decision_thr_train(user_key, dec_decision)
        log_auto_postprocess_train(user_key, postproc_decision)

        # 最终选定模式落盘：窗口样本索引（§10）与各模型权重
        if split_sizes["train"] >= window:
            _, _, wmeta = build_windows(scaled["train"][0], scaled["train"][1],
                                        scaled["train"][2], window=window, mode=wmode)
            wmeta.to_csv(out / "train_window_index.csv", index=False)
        for mname, mobj in fitted_models.items():
            mobj.save(out / "models" / f"{mname}.pkl")

        # 三阶段汇总 CSV：model × split 行 × 指标列（state_thr_w=分类指标判定阈值）
        split_rows = [{"model": mname, "split": s, "state_thr_w": on_thr,
                       **{m: v["macro"] for m, v in mm.items()}}
                      for mname, by in results_by_split.items()
                      for s, mm in by.items()]
        pd.DataFrame(split_rows).to_csv(out / "metrics_by_split.csv",
                                        index=False, encoding="utf-8")
        # 日级指标 CSV：model × split × date 行 × 指标列（能力口径 @on_thr，无游程）
        pd.concat(daily_rows, ignore_index=True).to_csv(
            out / "metrics_daily.csv", index=False, encoding="utf-8")
        # 日级链口径 CSV：同上，分类指标按 target_state(@on_thr) vs pred_state(@decision_thr+游程) 逐日
        pd.concat(daily_chain_rows, ignore_index=True).to_csv(
            out / "metrics_daily_chain.csv", index=False, encoding="utf-8")

        # 训练阶段预测结果 CSV：timestamp/split/target(真实值) + 各模型预测列
        # （§2.3 同域产物：predictions/train_predictions.csv，按时间排序便于画图比对）
        pred_out = pd.concat(pred_frames.values(), ignore_index=True)
        pred_out = pred_out.sort_values("timestamp").reset_index(drop=True)
        pred_csv = out / "predictions" / "train_predictions.csv"
        pred_csv.parent.mkdir(parents=True, exist_ok=True)
        pred_out.round(3).to_csv(pred_csv, index=False, encoding="utf-8")
        log.info("[%s] 训练预测结果已保存: %s（%d 行 × %d 模型）",
                 user_key, pred_csv, len(pred_out), len(test_preds))

        # —— 状态策略评估（test）：决策阈值 + 游程后处理下的 F1（全量 / 仅开机日两口径）
        strat_rows = []
        y_test = splits["test"][1][:, 0]
        idx_test = splits["test"][2]
        t_on_test = y_test >= on_thr
        day_on = pd.Series(t_on_test, index=idx_test).groupby(
            idx_test.normalize()).transform("max").to_numpy()
        # 三条策略：raw = 与 metrics_by_split 同口径的对照行（on_thr_w 判决、无游程），
        # 便于跨产物对账；decision+runs = 生产判决链（decision_thr_w + min_on/fill）
        strategies = [("raw_on_thr", on_thr, 0, 0),
                      ("decision+runs", dec_thr, post_min_on, post_fill_off)]
        for mname, p in test_preds.items():
            for strat, thr_, mo_, fo_ in strategies:
                st = postprocess_state(p, thr_, mo_, fo_)
                for scope, m in (("all_days", np.ones(len(st), bool)),
                                 ("on_days_only", day_on)):
                    tp = int((st[m] & t_on_test[m]).sum())
                    fp = int((st[m] & ~t_on_test[m]).sum())
                    fn = int((~st[m] & t_on_test[m]).sum())
                    # 空真约定与 evaluation.metrics 一致：无开态预测有漏报记 0
                    prec = tp / (tp + fp) if (tp + fp) > 0 else (1.0 if fn == 0 else 0.0)
                    rec = tp / (tp + fn) if tp + fn else 1.0
                    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
                    strat_rows.append({"model": mname, "strategy": strat,
                                       "scope": scope, "decision_thr_w": thr_,
                                       "post_min_on": mo_, "post_fill_short_off": fo_,
                                       "f1": round(f1, 4), "precision": round(prec, 4),
                                       "recall": round(rec, 4),
                                       "tp": tp, "fp": fp, "fn": fn})
        pd.DataFrame(strat_rows).to_csv(out / "state_strategy_metrics.csv",
                                        index=False, encoding="utf-8")

        table = build_comparison_table(results)
        table.to_csv(out / "comparison.csv", encoding="utf-8")
        summary = summarize(table)
        pbus_note = (f"pbus_mode={pbus_mode} (configured={cfg_pbus_mode}, "
                     f"stage={pbus_decision.get('stage')})"
                     if cfg_pbus_mode == "auto" else f"pbus_mode={pbus_mode}")
        on_thr_note = (f"on_thr_w={on_thr} (configured={cfg_on_thr_raw})"
                       if cfg_on_thr_raw == "auto" else f"on_thr_w={on_thr}")
        dec_thr_note = (f"decision_thr_w={dec_thr} (configured={cfg_dec_thr_raw}, "
                        f"model={dec_decision.get('calibrated_model')})"
                        if cfg_dec_thr_raw == "auto" else f"decision_thr_w={dec_thr}")
        post_note = (
            f"post_min_on={post_min_on}, post_fill_short_off={post_fill_off} "
            f"(configured={cfg_post_min_on_raw}/{cfg_post_fill_off_raw})"
            if postproc_is_auto
            else f"post_min_on={post_min_on}, post_fill_short_off={post_fill_off}"
        )
        write_markdown_report(out, f"{user_key}/train", table, summary,
                              notes=[f"target_col={'+'.join(target_cols)}",
                                     f"split_strategy={user_cfg['split_strategy']}",
                                     pbus_note, on_thr_note, dec_thr_note, post_note])
        best = summary.get("overall_best")
        y_tr_meta = splits["train"][1][:, 0]
        y_tr_on_meta = y_tr_meta[y_tr_meta >= on_thr]
        train_y_on_p10 = round(float(np.quantile(y_tr_on_meta, 0.10)), 3) if len(y_tr_on_meta) > 0 else None
        train_upper_guard_w = (
            round(max(float(on_thr), UPPER_ON_P10_RATIO * float(train_y_on_p10)), 3)
            if train_y_on_p10 is not None else float(on_thr)
        )

        _dump(out / "meta.json", {
            "user_key": user_key, "mode": mode, "target_cols": target_cols,
            "pbus_mode": pbus_mode,
            "pbus_mode_configured": cfg_pbus_mode,
            "pbus_mode_decision": pbus_decision,
            "feature_names": names, "scale_cols": scale_cols,
            "scaler": scaler.state(), "window": window, "window_mode": wmode,
            "split_sizes": split_sizes, "split_strategy": user_cfg["split_strategy"],
            "best_model": best, "models": [s["name"] for s in base_cfg.get("models", [])],
            "collapsed_models": [c["model"] for c in collapsed_models],
            "on_thr_w": on_thr,
            "on_thr_w_configured": cfg_on_thr_raw,
            "on_thr_w_decision": on_thr_decision,
            "decision_thr_w": dec_thr,
            "decision_thr_w_configured": cfg_dec_thr_raw,
            "decision_thr_w_decision": dec_decision,
            "model_decision_thr_w": model_dec_map,
            "train_y_on_p10": train_y_on_p10,
            "train_upper_guard_w": train_upper_guard_w,
            "post_min_on": post_min_on,
            "post_fill_short_off": post_fill_off,
            "post_min_on_configured": cfg_post_min_on_raw,
            "post_fill_short_off_configured": cfg_post_fill_off_raw,
            "postprocess_bounds": postproc_bounds,
            "postprocess_decision": postproc_decision,
            "model_postprocess_params": model_postproc_map,
            "quality": {"bus": q_bus, "branch": q_br},
            "identifiable": ident.get("identifiable"),
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        })
        _dump(out / "metrics.json", results)
        (out / DONE_MARKER).write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
        msg = f"best={best}" + ("" if ident.get("identifiable", True) else f"; {Status.IDENTIFIABILITY_LOW}")
        return UserTaskResult(user_key, mode, Status.OK, msg, str(out),
                              metrics={m: v["macro"] for m, v in (results.get(best) or {}).items()})
    except UserTaskError as e:
        log.error("[%s] %s: %s", user_key, e.status, e)
        return UserTaskResult(user_key, mode, e.status, str(e), str(out))
    except QualityError as e:
        log.error("[%s] %s: %s", user_key, Status.DATA_QUALITY_FAILED, e)
        return UserTaskResult(user_key, mode, Status.DATA_QUALITY_FAILED, str(e), str(out))
    except Exception as e:  # noqa: BLE001 —— 单用户失败不得阻塞其他用户（§13）
        log.error("[%s] 未分类错误: %s\n%s", user_key, e, traceback.format_exc())
        return UserTaskResult(user_key, mode, Status.FAILED, str(e), str(out))


# ---------------------------------------------------------------- 推理
def run_user_infer(user_key: str, scan, user_cfg: dict, base_cfg: dict,
                   output_root: str | Path) -> UserTaskResult:
    """单用户推理：只使用该用户自己的模型（§13：MODEL_NOT_FOUND 不得借用他人模型）。"""
    mode = "infer"
    out = _new_outdir(Path(output_root), user_key, mode)
    try:
        train_root = Path(output_root) / user_key / "train"
        train_dir = latest_done_dir(train_root)
        if train_dir is None:
            raise UserTaskError(Status.MODEL_NOT_FOUND, f"找不到 {user_key} 的已完成训练产物")
        meta = json.loads((train_dir / "meta.json").read_text(encoding="utf-8"))
        planned_model = (user_cfg.get("infer_model") or base_cfg.get("infer_model")
                         or meta.get("best_model") or (meta.get("models") or [None])[0])
        on_thr, dec_thr, infer_thr_info = resolve_infer_thresholds(
            user_key, user_cfg, meta, model_name=planned_model
        )

        field_map = base_cfg.get("bus_field_map") or user_cfg.get("bus_field_map") or {}
        dcfg = base_cfg.get("data", {})
        sentinels = dcfg.get("sentinel_values")
        bus_raw, bus_schema = CsvBusLoader().load(
            scan.bus_files, field_map, sentinels=sentinels,
            derive_phase_from_ptotal=bool(dcfg.get("derive_phase_from_ptotal", False)))
        fatal = [i for i in bus_schema["issues"] if "SCHEMA_UNCONFIRMED" in i or "不存在" in i or "缺少列" in i]
        if fatal:
            raise UserTaskError(Status.SCHEMA_UNCONFIRMED, "; ".join(fatal))
        write_schema_report(out / "data_schema_report.json", bus_schema,
                            {"kind": "infer", "files": [str(p.name) for p in scan.bus_files]})

        pp = base_cfg.get("preprocess", {})
        allow_negative = bool(pp.get("allow_negative_power", False))
        bus_c = Cleaner(clip_negative=not allow_negative,
                        max_gap_interp=int(pp.get("max_gap_interp", 2))).transform(bus_raw)
        # PT/CT 变比（与 on_thr_w 同级，默认 1.0=不校正）：总线二次侧->一次侧。
        # 质量打分（BOUNDS 1000V 二次侧 sanity 上限）必须在校正前（二次侧）执行，
        # 校正推迟至质量之后、特征之前（一次侧 kW）。
        pt_ratio = float(user_cfg.get("pt_ratio", 1.0))
        ct_ratio = float(user_cfg.get("ct_ratio", 1.0))
        # 分路一次侧单位换算（到 kW 填 1000.0，默认 1.0=W 历史不变）
        branch_scale = float(user_cfg.get("branch_scale", 1.0))
        save_cleaned = bool(pp.get("save_cleaned_csv", True))
        _save_cleaned_csv(out, "bus", bus_c, save_cleaned)
        bus15, agg_record = resample_bus(bus_c, strategy=pp.get("agg_strategy"))
        _dump(out / "agg_strategy.json", agg_record)

        # —— 推理前分路开机情况分析（有分路文件时；branch_c 供后续离线评估复用）
        branch_c = None
        infer_quality = None
        if scan.branch_files:
            branch_raw, _ = CsvBranchLoader().load(scan.branch_files, sentinels=sentinels)
            branch_c = Cleaner(clip_negative=not allow_negative).transform(branch_raw)
            if branch_scale != 1.0:
                branch_c = apply_branch_scale(branch_c, branch_scale)
                log.info("[%s] 分路单位换算 branch_scale=%.4g（到 kW）", user_key, branch_scale)
            # 目标通道为唯一有效分路，其余通道（不在当前总线回路）清洗后立即丢弃
            tcols_i = resolve_target_cols(user_cfg.get("target_col"), branch_c)
            invalid_ch = [c for c in branch_c.columns if c not in tcols_i]
            if invalid_ch:
                log.info("[%s] 丢弃无效分路通道 %s（不在当前总线回路，仅保留目标通道 %s）",
                         user_key, invalid_ch, tcols_i)
                branch_c = branch_c[tcols_i]
            _save_cleaned_csv(out, "branch", branch_c, save_cleaned)
            sessions = analyze_branch_sessions(branch_c, on_thr,
                                               columns=tcols_i)
            sessions.to_csv(out / "branch_sessions.csv", index=False, encoding="utf-8")
            # 数据质量报告（与训练阶段同构：bus+branch 有效通道口径；只报告不设门禁）
            q_bus_i = quality_report(bus15, "bus", 96, allow_negative,
                                     on_thr_w=on_thr)
            q_br_i = quality_report(branch_c, "branch", 96, allow_negative,
                                    on_thr_w=on_thr)
            q_br_i["target_cols"] = tcols_i
            # 逐天质量表 + 双达标统计 + 建议（推理侧建议主要看数据可用性）
            min_score_i = float(base_cfg.get("quality", {}).get("min_score", 50))
            daily_q_i = daily_quality_table(bus15, branch_c, 96, min_score_i,
                                            allow_negative)
            daily_q_i.to_csv(out / "daily_quality.csv", index=False, encoding="utf-8")
            advice_i = quality_advice(daily_q_i,
                                      float(base_cfg.get("quality", {})
                                            .get("min_days", 14)))
            q_side_i = qualified_days_counts(daily_q_i)
            q_br_i["both_qualified_days"] = q_side_i["both_qualified_days"]
            q_br_i["daily_total_days"] = q_side_i["total_days"]
            q_bus_i["qualified_days_by_side"] = q_side_i
            q_br_i["qualified_days_by_side"] = q_side_i
            _dump(out / "quality_advice.json", {"advice": advice_i})
            infer_quality = {"bus": q_bus_i, "branch": q_br_i}
            write_quality_html(out / "data_quality_report.html", [q_bus_i, q_br_i],
                               daily_quality=daily_q_i, advice=advice_i)

        # §12.4 infer 时间过滤
        ispec = user_cfg.get("infer") or {}
        if ispec.get("include") or ispec.get("exclude"):
            bus15 = filter_dataframe(bus15, ispec.get("include"), ispec.get("exclude"))
        if len(bus15) == 0:
            raise UserTaskError(Status.INSUFFICIENT_TIME_RANGE, "infer 时间过滤后无数据")

        # PT/CT 校正（二次侧->一次侧 kW）：质量已在二次侧打分，此后才转一次侧 kW
        # 供特征使用（pt/ct=1.0 时仅功率 W->kW /1000）
        bus15 = apply_pt_ct(bus15, pt_ratio, ct_ratio)
        log.info("[%s] PT/CT 校正 pt=%.4g ct=%.4g（综合 %.4g，功率转 kW）",
                 user_key, pt_ratio, ct_ratio, pt_ratio * ct_ratio)

        fc = {**base_cfg.get("features", {}), **(user_cfg.get("features") or {})}
        feat = build_features(bus15, lags=tuple(fc.get("lags", [1, 2, 3, 4])),
                              rolling_windows=tuple(fc.get("rolling_windows", ["1h", "6h", "24h"])))
        names = meta["feature_names"]
        missing_cols = [c for c in names if c not in feat.columns]
        if missing_cols:
            raise UserTaskError(Status.FAILED, f"推理特征缺列: {missing_cols}")
        feat = feat[names]
        valid = feat.dropna()
        if len(valid) == 0:
            raise UserTaskError(Status.INSUFFICIENT_TIME_RANGE, "推理区间无有效样本")

        trained_pbus_mode = _infer_trained_pbus_mode(meta)
        cfg_pbus_mode = str(user_cfg.get("pbus_mode") or "auto")
        if cfg_pbus_mode != "auto" and cfg_pbus_mode != trained_pbus_mode:
            log.warning("[%s] 配置 pbus_mode=%s 与训练产物 pbus_mode=%s（%s）不一致！"
                        "推理仍按训练产物 scaler 执行；切换 pbus_mode 后须加 --force 重新训练",
                        user_key, cfg_pbus_mode, trained_pbus_mode, train_dir.name)
        else:
            log.info("[%s] 推理沿用训练 pbus_mode=%s（配置=%s）",
                     user_key, trained_pbus_mode, cfg_pbus_mode)
        scaler = TrainFitScaler.from_state(meta["scaler"])
        X = scaler.transform(valid.to_numpy(np.float64))

        # 模型选择：配置指定 > 训练最优；只用本用户模型
        # 优先级：用户级 infer_model > _default.infer_model > JSON 顶级全局
        # infer_model（三者已在 resolve_user_config 合并进 user_cfg）
        # > base_cfg.infer_model（default.yaml）> 训练综合最优 best_model
        model_name = (user_cfg.get("infer_model") or base_cfg.get("infer_model")
                      or meta.get("best_model") or meta["models"][0])
        if model_name not in meta["models"]:
            raise UserTaskError(Status.MODEL_NOT_FOUND, f"模型 {model_name} 不在该用户训练清单")
        if model_name in (meta.get("collapsed_models") or []):
            log.warning("[%s] 推理模型 %s 在训练阶段被标记 PRED_COLLAPSED（test 预测带宽"
                        "<on_thr_w，疑似欠训练）——推理结果将无法识别任何开机点，"
                        "建议改用其他模型或重新训练", user_key, model_name)
        model_path = train_dir / "models" / f"{model_name}.pkl"
        if not model_path.exists():
            raise UserTaskError(Status.MODEL_NOT_FOUND, f"模型文件缺失: {model_path}")
        model = BaseModel.load(model_path)
        pred = model.predict(X, index=valid.index).reshape(-1)
        pred = apply_constraints(pred[:, None], valid["pbus"].to_numpy(),
                                 nonnegative=not allow_negative, sum_consistency=False)[:, 0]

        # 输出契约：predictions/inference_result.csv（§2.3）
        _, user_id = split_user_key(user_key)
        target_vals = pd.Series(np.nan, index=valid.index)
        offline_metrics = None
        if branch_c is not None:  # 分路仅用于离线评估，不参与生产推理（§3.1）；已在推理前加载
            tcols = resolve_target_cols(user_cfg.get("target_col"), branch_c)
            t = build_target(branch_c, tcols).reindex(valid.index)
            target_vals = t
            have = t.dropna()
            # 日级无效天剔除：总线或分路全天缺失/缺失率超阈值的天不参与评估指标
            daily_thr = float(base_cfg.get("quality", {})
                              .get("max_daily_missing_rate", 1.0))
            bad_days = sorted(set(invalid_data_days(bus15, 96, daily_thr)) |
                              set(invalid_data_days(branch_c, 96, daily_thr)))
            if bad_days and len(have):
                have = have[~have.index.normalize().isin(set(bad_days))]
                log.warning("[%s] 推理评估剔除无效天 %d 天: %s", user_key,
                            len(bad_days),
                            [d.strftime("%Y-%m-%d") for d in bad_days])
            _dump(out / "excluded_days.json", {
                "max_daily_missing_rate": daily_thr,
                "excluded_days": [d.strftime("%Y-%m-%d") for d in bad_days],
            })
            if len(have) > 0:
                metric_names = base_cfg.get("metrics", ["mae", "rmse", "r2", "sae"])
                pred_on_have = pd.Series(pred, index=valid.index).loc[have.index]
                offline_metrics = evaluate_all(
                    have.to_numpy()[:, None], pred_on_have.to_numpy()[:, None],
                    metric_names, on_thr_w=on_thr)
                _dump(out / "offline_metrics.json", offline_metrics)
                # 质量报告补充双达标天明细（推理评估段=推理集）
                if infer_quality is not None:
                    infer_days = set(have.index.normalize().strftime("%Y-%m-%d"))
                    q_detail_i = qualified_days_detail(
                        daily_q_i, t, on_thr,
                        infer_days=infer_days)
                    q_detail_i.to_csv(out / "qualified_days_detail.csv",
                                      index=False, encoding="utf-8")
                    infer_quality["branch"]["qualified_days_stats"] = \
                        qualified_days_summary(q_detail_i)
                    advice_i = advice_i + split_coverage_advice(q_detail_i)
                    _dump(out / "quality_advice.json", {"advice": advice_i})
                    write_quality_html(out / "data_quality_report.html",
                                       [infer_quality["bus"], infer_quality["branch"]],
                                       daily_quality=daily_q_i, advice=advice_i,
                                       qualified_detail=q_detail_i)
                # 日级离线指标 CSV（model × date 行 × 指标列）
                daily = evaluate_daily(have.to_numpy()[:, None],
                                       pred_on_have.to_numpy()[:, None],
                                       have.index, metric_names,
                                       on_thr_w=on_thr)
                daily.insert(0, "model", model_name)
                # 状态判定阈值自描述（同训练日级：on_thr_w 口径，非判决链口径）
                daily["state_thr_w"] = on_thr
                daily.to_csv(out / "metrics_daily.csv", index=False, encoding="utf-8")

        post_min_on, post_fill_off, infer_postproc_info = resolve_infer_postprocess_params(
            user_key, user_cfg, meta, model_name=model_name
        )
        pred_state = postprocess_state(pred, dec_thr, post_min_on, post_fill_off)
        pred_prob = state_probability(pred, dec_thr)
        # 状态真值：分路真值按同一 on_thr_w 二值化；无真值处为空（NaN）
        target_np = target_vals.to_numpy(dtype=np.float64)
        target_state = np.where(np.isnan(target_np), np.nan,
                                (target_np >= on_thr).astype(float))
        result_csv = out / INFERENCE_RESULT_REL
        result_csv.parent.mkdir(parents=True, exist_ok=True)
        df_result = pd.DataFrame({
            "timestamp": valid.index.strftime("%Y-%m-%d %H:%M:%S"),
            "user_id": user_id,
            "target": target_np,
            "target_state": pd.array(
                [int(v) if not np.isnan(v) else None for v in target_state],
                dtype="Int64"),
            "on_thr_w": on_thr,          # 真值判态阈值（分类指标同口径）
            "pred": pred,
            "pred_state": pred_state.astype(int),
            "decision_thr_w": dec_thr,   # 预测判态阈值（判决链口径）
            "pred_prob": np.round(pred_prob, 6),
        })
        df_result[INFER_RESULT_COLUMNS].to_csv(result_csv, index=False)

        # 链口径日级评估（推理，I.J 新增）：回归同 pred vs target，分类按 target_state(@on_thr) vs pred_state 逐日
        if branch_c is not None:
            try:
                # have 为剔除无效天后的有效评估点（与 offline_metrics/metrics_daily 同口径）
                if "have" in locals() and have is not None and len(have) > 0:
                    metric_names_chain = base_cfg.get("metrics",
                                                      ["mae", "rmse", "r2", "sae",
                                                       "f1", "accuracy", "precision",
                                                       "recall", "tp", "fp", "fn", "tn"])
                    pred_state_on_have = pd.Series(
                        pred_state, index=valid.index).loc[have.index].to_numpy(dtype=int)
                    pred_on_have_chain = pd.Series(
                        pred, index=valid.index).loc[have.index].to_numpy(dtype=float)
                    daily_chain = evaluate_daily_chain(
                        have.to_numpy(dtype=float)[:, None],
                        pred_on_have_chain[:, None],
                        pred_state_on_have,
                        have.index,
                        metric_names_chain,
                        on_thr_w=on_thr)
                    daily_chain.insert(0, "model", model_name)
                    daily_chain["state_thr_w"] = on_thr
                    daily_chain["decision_thr_w"] = dec_thr
                    daily_chain["post_min_on"] = post_min_on
                    daily_chain["post_fill_short_off"] = post_fill_off
                    daily_chain.to_csv(out / "metrics_daily_chain.csv",
                                       index=False, encoding="utf-8")
            except Exception as e:  # noqa: BLE001 —— 链表为新增诊断产物，失败不阻断推理
                log.warning("[%s] metrics_daily_chain 生成跳过: %s", user_key, e)

        # 只读离线跨期漂移诊断（不修改 inference_result.csv，仅在跨月基荷显著偏移时给出红字校准建议）
        drift_advice = None
        if branch_c is not None and "have" in locals() and have is not None and len(have) > 0:
            try:
                train_upper_guard = meta.get("train_upper_guard_w")
                if train_upper_guard is None:
                    train_upper_guard = (meta.get("decision_thr_w_decision") or {}).get("upper_guard_w")
                if train_upper_guard is None:
                    tp_csv = train_dir / "predictions" / "train_predictions.csv"
                    if tp_csv.exists():
                        tp_tr = pd.read_csv(tp_csv, usecols=["split", "target"])
                        y_tr_ref = tp_tr.loc[tp_tr["split"] == "train", "target"].to_numpy(dtype=float)
                        y_tr_on = y_tr_ref[y_tr_ref >= on_thr]
                        if len(y_tr_on) > 0:
                            train_upper_guard = max(float(on_thr), UPPER_ON_P10_RATIO * float(np.quantile(y_tr_on, 0.10)))
                drift_advice = check_offline_decision_drift(
                    user_key=user_key,
                    y_infer_true=have.to_numpy(dtype=float),
                    pred_infer=pred_on_have.to_numpy(dtype=float),
                    infer_index=have.index,
                    on_thr_w=on_thr,
                    current_dec_thr_w=dec_thr,
                    post_min_on=post_min_on,
                    post_fill_off=post_fill_off,
                    model_name=model_name,
                    upper_guard_w=float(train_upper_guard) if train_upper_guard is not None else None,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("[%s] 离线漂移诊断跳过: %s", user_key, e)

        _dump(out / "meta.json", {
            "user_key": user_key, "mode": mode, "model": model_name,
            "pbus_mode": trained_pbus_mode,
            "pbus_mode_configured": cfg_pbus_mode,
            "on_thr_w": on_thr,
            "decision_thr_w": dec_thr,
            "post_min_on": post_min_on,
            "post_fill_short_off": post_fill_off,
            "thresholds": infer_thr_info,
            "postprocess": infer_postproc_info,
            "offline_drift_advice": drift_advice,
            "train_dir": str(train_dir), "n_points": len(valid),
            "quality": infer_quality,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        })
        (out / DONE_MARKER).write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
        log.info("[%s] 推理完成：%d 点 -> %s", user_key, len(valid), result_csv)
        return UserTaskResult(user_key, mode, Status.OK, f"model={model_name}, n={len(valid)}",
                              str(out), metrics=offline_metrics and
                              {m: v["macro"] for m, v in offline_metrics.items()})
    except UserTaskError as e:
        log.error("[%s] %s: %s", user_key, e.status, e)
        return UserTaskResult(user_key, mode, e.status, str(e), str(out))
    except Exception as e:  # noqa: BLE001 —— §13 失败隔离
        log.error("[%s] 未分类错误: %s\n%s", user_key, e, traceback.format_exc())
        return UserTaskResult(user_key, mode, Status.FAILED, str(e), str(out))
