"""跨用户通用（pooled）任务编排 —— 新增链路，不影响单用户链路。

与单用户链路（user_task.py）的关系：
- 复用同一套 data_io / preprocess / models / evaluation 函数；
- 差异仅在「池化拼接 + cap 归一化 + 全局 Scaler + LOUO 切分 + 通用模型落盘」；
- 产物目录：outputs/universal/<target_col>/train/<ts>/（与 outputs/<user>/ 隔离）；
- 推理：新用户（可只有总线）用通用模型零样本推理，cap 仅由其总线估计。

对外暴露：run_pooled_train / run_pooled_infer / latest_universal_dir。
"""

from __future__ import annotations

import json
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from nilm.analysis import identifiability_report
from nilm.common.contracts import (INFER_RESULT_COLUMNS, INFERENCE_RESULT_REL,
                                   Status, split_user_key)
from nilm.common.logging import get_logger
from nilm.common.schema import bus_total
from nilm.common.timefilter import filter_dataframe
from nilm.data_io.csv_source import CsvBranchLoader, CsvBusLoader
from nilm.data_io.validator import (daily_quality_table, invalid_data_days,
                                    quality_report)
from nilm.evaluation import evaluate_all, evaluate_daily
from nilm.models import MODEL_REGISTRY
from nilm.models.base import BaseModel
from nilm.models.constraints import apply_constraints
from nilm.pipeline.auto_thresholds import (
    resolve_infer_thresholds,
    resolve_train_on_thr_w,
)
from nilm.postprocess.state import postprocess_state, state_probability
from nilm.preprocess.align import align_frames, resample_bus
from nilm.preprocess.clean import Cleaner
from nilm.preprocess.dataset import DEFAULT_WINDOW, drop_invalid_rows
from nilm.preprocess.features import build_features
from nilm.preprocess.normalize import (apply_branch_scale, apply_pt_ct,
                                       cap_from_bus, denormalize_target,
                                       normalize_features, normalize_target)
from nilm.preprocess.pooled_splits import (assign_pooled_splits, louo_folds,
                                           split_mask_for_index)
from nilm.preprocess.scaling import TrainFitScaler
from nilm.preprocess.target import build_target, resolve_target_cols

log = get_logger("pipeline.pooled_task")

DONE_MARKER = "_DONE"
NON_SCALED_COLS = {"slot", "hour", "minute", "day_of_week", "is_weekend",
                   "month", "day_of_year", "pbus"}


class PooledTaskError(Exception):
    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class PooledResult:
    status: str
    message: str = ""
    output_dir: str | None = None
    metrics: dict | None = None


def _new_outdir(output_root: Path, target_col: str, mode: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = Path(output_root) / "universal" / target_col / mode
    out = base / stamp
    n = 1
    while out.exists():
        out = base / f"{stamp}_{n}"
        n += 1
    out.mkdir(parents=True)
    return out


def latest_universal_dir(output_root: Path, target_col: str, mode: str = "train") -> Path | None:
    root = Path(output_root) / "universal" / target_col / mode
    if not root.is_dir():
        return None
    done = [p for p in sorted(root.iterdir()) if p.is_dir() and (p / DONE_MARKER).exists()]
    return done[-1] if done else None


def _dump(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8")


def _load_user_preprocessed(user_key: str, scan, user_cfg: dict, base_cfg: dict,
                            target_col_override: str | None = None,
                            need_branch: bool = True) -> dict:
    """单用户预处理（与 user_task.train 前半段同口径），返回归一化后样本。

    返回 dict: feat_norm(DataFrame), y_phys(Series), y_norm(Series),
               index, cap, on_thr_w, feature_names, bus_al(15min总线),
               target_cols, ident(dict)
    """
    pp = base_cfg.get("preprocess", {})
    qcfg = base_cfg.get("quality", {})
    field_map = user_cfg.get("bus_field_map") or base_cfg.get("bus_field_map")
    if not field_map:
        raise PooledTaskError(Status.SCHEMA_UNCONFIRMED,
                              f"[{user_key}] 未配置 bus_field_map")
    dcfg = base_cfg.get("data", {})
    sentinels = dcfg.get("sentinel_values")
    bus_raw, bus_schema = CsvBusLoader().load(
        scan.bus_files, field_map, sentinels=sentinels,
        derive_phase_from_ptotal=bool(dcfg.get("derive_phase_from_ptotal", False)))
    fatal = [i for i in bus_schema["issues"] if "SCHEMA_UNCONFIRMED" in i or "不存在" in i or "缺少列" in i]
    if fatal:
        raise PooledTaskError(Status.SCHEMA_UNCONFIRMED, f"[{user_key}] " + "; ".join(fatal))

    allow_negative = bool(pp.get("allow_negative_power", False))
    cleaner = Cleaner(clip_negative=not allow_negative,
                      max_gap_interp=int(pp.get("max_gap_interp", 2)))
    bus_c = cleaner.transform(bus_raw)
    # PT/CT 变比（与 on_thr_w 同级，默认 1.0=不校正）：总线二次侧->一次侧。
    # 质量打分（BOUNDS 1000V 二次侧 sanity 上限）必须在校正前（二次侧）执行，
    # 校正推迟至 day_gate 之后、ident/特征/cap 之前（一次侧 kW）。
    pt_ratio = float(user_cfg.get("pt_ratio", 1.0))
    ct_ratio = float(user_cfg.get("ct_ratio", 1.0))
    # 分路一次侧单位换算（到 kW 填 1000.0，默认 1.0=W 历史不变）
    branch_scale = float(user_cfg.get("branch_scale", 1.0))

    branch_c = None
    target = None
    target_cols: list[str] = []
    if need_branch:
        if not scan.branch_files:
            raise PooledTaskError(Status.DATA_MISSING_BRANCH_LABEL,
                                  f"[{user_key}] 缺少分路标签 CSV")
        branch_raw, branch_schema = CsvBranchLoader().load(scan.branch_files, sentinels=sentinels)
        if branch_schema["issues"]:
            raise PooledTaskError(Status.DATA_MISSING_BRANCH_LABEL,
                                  f"[{user_key}] " + "; ".join(branch_schema["issues"]))
        branch_c = cleaner.transform(branch_raw)
        if branch_scale != 1.0:
            branch_c = apply_branch_scale(branch_c, branch_scale)
            log.info("[%s] 分路单位换算 branch_scale=%.4g（到 kW）", user_key, branch_scale)
        tc = target_col_override or user_cfg.get("target_col")
        target_cols = resolve_target_cols(tc, branch_c)
        invalid_ch = [c for c in branch_c.columns if c not in target_cols]
        if invalid_ch:
            log.info("[%s] 丢弃无效分路通道 %s（仅保留 %s）", user_key, invalid_ch, target_cols)
            branch_c = branch_c[target_cols]
        target = build_target(branch_c, target_cols)
    else:
        # 推理：有分路则加载供离线评估，无则仅总线
        if scan.branch_files:
            branch_raw, _ = CsvBranchLoader().load(scan.branch_files, sentinels=sentinels)
            branch_c = cleaner.transform(branch_raw)
            if branch_scale != 1.0:
                branch_c = apply_branch_scale(branch_c, branch_scale)
                log.info("[%s] 分路单位换算 branch_scale=%.4g（到 kW）", user_key, branch_scale)
            tc = target_col_override or user_cfg.get("target_col")
            try:
                target_cols = resolve_target_cols(tc, branch_c)
                branch_c = branch_c[target_cols]
                target = build_target(branch_c, target_cols)
            except Exception:
                branch_c, target, target_cols = None, None, []

    bus15, _ = resample_bus(bus_c, strategy=pp.get("agg_strategy"))
    if need_branch:
        bus_al, branch_al = align_frames(bus15, branch_c,
                                         min_overlap=float(pp.get("min_overlap", 0.3)))
        target = target.loc[branch_al.index]
    else:
        bus_al, branch_al = bus15, branch_c

    # train 时间过滤（与单用户一致）
    tspec = user_cfg.get("train") or {}
    if tspec.get("include") or tspec.get("exclude"):
        bus_al = filter_dataframe(bus_al, tspec.get("include"), tspec.get("exclude"))
        if branch_al is not None:
            branch_al = filter_dataframe(branch_al, tspec.get("include"), tspec.get("exclude"))
        if target is not None:
            target = target.loc[target.index.intersection(bus_al.index)]

    # 无效天剔除
    daily_thr = float(qcfg.get("max_daily_missing_rate", 1.0))
    bad_days = set(invalid_data_days(bus_al, 96, daily_thr))
    if branch_al is not None:
        bad_days |= set(invalid_data_days(branch_al, 96, daily_thr))
    if bad_days:
        bus_al = bus_al[~bus_al.index.normalize().isin(bad_days)]
        if branch_al is not None:
            branch_al = branch_al[~branch_al.index.normalize().isin(bad_days)]
        if target is not None:
            target = target[~target.index.normalize().isin(bad_days)]
        log.warning("[%s] 剔除无效天 %d 天", user_key, len(bad_days))

    # day_gate：只保留双达标天（与单用户同口径，简化版：无 branch 时跳过）
    day_gate = bool((user_cfg.get("quality") or {}).get("day_gate") or qcfg.get("day_gate", False))
    if day_gate and branch_al is not None and target is not None:
        min_score = float(qcfg.get("min_score", 70))
        daily_q = daily_quality_table(bus_al, branch_al, 96, min_score, allow_negative)
        qual = set(daily_q.loc[daily_q["qualified"] == 1, "date"].astype(str))
        kept = sorted({d.strftime("%Y-%m-%d") for d in bus_al.index.normalize()}
                      | {d.strftime("%Y-%m-%d") for d in branch_al.index.normalize()})
        drop_days = sorted(set(kept) - qual)
        if not qual.intersection(kept):
            raise PooledTaskError(Status.DATA_QUALITY_FAILED,
                                  f"[{user_key}] 无双达标天")
        if drop_days:
            drop_set = {pd.Timestamp(d) for d in drop_days}
            bus_al = bus_al[~bus_al.index.normalize().isin(drop_set)]
            branch_al = branch_al[~branch_al.index.normalize().isin(drop_set)]
            target = target[~target.index.normalize().isin(drop_set)]
            log.warning("[%s] day_gate 剔除非双达标天 %d 天", user_key, len(drop_days))

    if len(bus_al) < 96 * float(qcfg.get("min_days", 3)):
        raise PooledTaskError(Status.INSUFFICIENT_TIME_RANGE,
                              f"[{user_key}] 有效数据不足（{len(bus_al)/96:.1f} 天）")

    # PT/CT 校正（二次侧->一次侧 kW）：质量已在二次侧打分，此后才转一次侧 kW
    # 供 ident/特征/cap 使用（pt/ct=1.0 时仅功率 W->kW /1000）
    bus_al = apply_pt_ct(bus_al, pt_ratio, ct_ratio)
    log.info("[%s] PT/CT 校正 pt=%.4g ct=%.4g（综合 %.4g，功率转 kW）",
             user_key, pt_ratio, ct_ratio, pt_ratio * ct_ratio)

    if isinstance(user_cfg.get("on_thr_w"), str) and user_cfg["on_thr_w"].strip().lower() == "auto":
        if target is not None and need_branch:
            eff_on_thr, _ = resolve_train_on_thr_w(user_key, user_cfg, target)
        else:
            eff_on_thr = 10.0
    else:
        eff_on_thr = float(user_cfg["on_thr_w"])

    ident = {}
    if target is not None:
        try:
            ident = identifiability_report(bus_al, target, on_thr_w=eff_on_thr)
        except Exception as e:
            log.warning("[%s] 可辨识性分析跳过: %s", user_key, e)

    fc = {**base_cfg.get("features", {}), **(user_cfg.get("features") or {})}
    feat = build_features(bus_al, lags=tuple(fc.get("lags", [5, 1, 2, 3, 4])),
                          rolling_windows=tuple(fc.get("rolling_windows", ["1h", "6h", "24h"])))
    cap_q = float((base_cfg.get("pooled") or {}).get("cap_quantile", 0.95))
    cap = cap_from_bus(bus_al, quantile=cap_q)
    feat_norm = normalize_features(feat, cap)

    if target is not None:
        f, y_phys = drop_invalid_rows(feat_norm, target)
        # drop_invalid_rows 已按 feat_norm 索引对齐；y_phys 为物理瓦数
        y_norm = normalize_target(y_phys, cap)
    else:
        f = feat_norm.dropna()
        y_phys, y_norm = None, None
    if len(f) < 2 * int(base_cfg.get("dataset", {}).get("window", DEFAULT_WINDOW)):
        raise PooledTaskError(Status.INSUFFICIENT_TIME_RANGE,
                              f"[{user_key}] 有效样本 {len(f)} 不足")
    return {
        "user_key": user_key, "feat": f, "y_phys": y_phys, "y_norm": y_norm,
        "index": f.index, "cap": cap, "on_thr_w": eff_on_thr,
        "pt_ratio": pt_ratio, "ct_ratio": ct_ratio, "branch_scale": branch_scale,
        "feature_names": [str(c) for c in f.columns],
        "bus_al": bus_al, "branch_al": branch_al, "target_cols": target_cols,
        "ident": ident,
    }


def _concat_pooled(pre: dict[str, dict], day_assign: dict[str, dict[str, str]]):
    """拼接池化矩阵：返回 dict 含 X_norm/y_norm/y_phys/pbus/index/user/split。"""
    Xs, yns, yps, pbus_list, idx_list, user_list, split_list = [], [], [], [], [], [], []
    for uk, d in pre.items():
        assign = day_assign[uk]
        for split in ("train", "val", "test"):
            m = split_mask_for_index(d["index"], assign, split)
            if not m.any():
                continue
            Xs.append(d["feat"].to_numpy(np.float64)[m.to_numpy()])
            yns.append(d["y_norm"].to_numpy(np.float64)[m.to_numpy()][:, None])
            yps.append(d["y_phys"].to_numpy(np.float64)[m.to_numpy()][:, None])
            # pbus 物理值：从 feat 的 pbus 列（归一化前需还原）—— feat 已归一化，
            # 用 bus_al 的 pbus 按 index 取
            pbus_phys = bus_total(d["bus_al"]).reindex(d["index"]).to_numpy(np.float64)[m.to_numpy()]
            pbus_list.append(pbus_phys)
            idx_list.append(d["index"][m.to_numpy()])
            user_list.extend([uk] * int(m.sum()))
            split_list.extend([split] * int(m.sum()))
    if not Xs:
        raise PooledTaskError(Status.INSUFFICIENT_TIME_RANGE, "池化拼接后无样本")
    return {
        "X": np.vstack(Xs), "y_norm": np.vstack(yns), "y_phys": np.vstack(yps),
        "pbus": np.concatenate(pbus_list),
        "index": pd.DatetimeIndex(np.concatenate(idx_list)),
        "user": np.array(user_list), "split": np.array(split_list),
    }


def _eval_per_user(y_phys: np.ndarray, y_hat_phys: np.ndarray,
                   user_arr: np.ndarray, on_thr_map: dict[str, float],
                   metric_names: list[str]) -> dict:
    """按用户 on_thr 逐用户评估再宏平均（池化 F1 必须按用户阈值）。"""
    per_user: dict[str, dict] = {}
    for uk in sorted(set(user_arr.tolist())):
        m = user_arr == uk
        if m.sum() == 0:
            continue
        per_user[uk] = evaluate_all(y_phys[m], y_hat_phys[m], metric_names,
                                    on_thr_w=on_thr_map[uk])
    # 宏平均：各指标 macro 在用户间取均值
    agg: dict[str, dict] = {}
    for name in metric_names:
        vals = [per_user[uk][name]["macro"] for uk in per_user]
        agg[name] = {"macro": float(np.mean(vals)) if vals else float("nan"),
                     "per_user": {uk: per_user[uk][name]["macro"] for uk in per_user}}
    return {"per_user": per_user, "pooled_macro": agg}


def _train_one_fold(pre: dict[str, dict], train_users: list[str], test_users: list[str],
                    base_cfg: dict, out: Path, fold_tag: str) -> dict:
    """单折训练：train_users 内切 train/val，test_users 整体 test。"""
    pooled_cfg = base_cfg.get("pooled") or {}
    val_ratio = float(pooled_cfg.get("val_ratio", 0.2))
    strategy = str(pooled_cfg.get("split_strategy", "stratified_by_state"))
    user_frames = {uk: (d["index"], d["y_phys"], d["on_thr_w"]) for uk, d in pre.items()}
    day_assign = assign_pooled_splits(user_frames, train_users, test_users,
                                      val_ratio=val_ratio, strategy=strategy)
    data = _concat_pooled(pre, day_assign)
    names = pre[train_users[0]]["feature_names"]
    # 特征列一致性校验
    for uk in train_users + test_users:
        if pre[uk]["feature_names"] != names:
            raise PooledTaskError(Status.FAILED,
                                  f"[{uk}] 特征列与池不一致: {pre[uk]['feature_names']} vs {names}")
    X, y_norm, y_phys = data["X"], data["y_norm"], data["y_phys"]
    split_arr, user_arr, idx_all = data["split"], data["user"], data["index"]
    on_thr_map = {uk: pre[uk]["on_thr_w"] for uk in pre}

    scale_cols = [i for i, c in enumerate(names) if c not in NON_SCALED_COLS]
    m_train = split_arr == "train"
    m_val = split_arr == "val"
    m_test = split_arr == "test"
    if m_train.sum() == 0:
        raise PooledTaskError(Status.INSUFFICIENT_TIME_RANGE, f"[{fold_tag}] train 为空")
    scaler = TrainFitScaler().fit(X[m_train], cols=scale_cols)
    Xs = scaler.transform(X)

    metric_names = base_cfg.get("metrics", ["mae", "rmse", "r2", "sae", "f1"])
    allow_negative = bool(base_cfg.get("preprocess", {}).get("allow_negative_power", False))
    results_by_split: dict[str, dict] = {}
    per_user_rows: list[dict] = []
    daily_rows: list[pd.DataFrame] = []
    fold_out = out / fold_tag if fold_tag else out
    fold_out.mkdir(parents=True, exist_ok=True)

    for spec in base_cfg.get("models", []):
        mname, params = spec["name"], dict(spec.get("params", {}))
        model = MODEL_REGISTRY.create(mname, **params)
        model.fit(Xs[m_train], y_norm[m_train], feature_names=names,
                  X_val=(Xs[m_val] if m_val.any() else None),
                  y_val=(y_norm[m_val] if m_val.any() else None),
                  index=idx_all[m_train],
                  val_index=(idx_all[m_val] if m_val.any() else None))
        model.save(fold_out / "models" / f"{mname}.pkl")
        results_by_split[mname] = {}
        for split, m in (("train", m_train), ("val", m_val), ("test", m_test)):
            if m.sum() == 0:
                continue
            y_hat_norm = model.predict(Xs[m], index=idx_all[m])
            # 还原物理域：逐样本 cap
            caps = np.array([pre[uk]["cap"] for uk in user_arr[m]])
            y_hat_phys = denormalize_target(y_hat_norm[:, 0], caps)[:, None] \
                if y_hat_norm.ndim == 2 else denormalize_target(y_hat_norm, caps)[:, None]
            y_hat_phys = apply_constraints(y_hat_phys, data["pbus"][m],
                                           nonnegative=not allow_negative,
                                           sum_consistency=False)
            ev = _eval_per_user(y_phys[m], y_hat_phys, user_arr[m], on_thr_map, metric_names)
            results_by_split[mname][split] = ev["pooled_macro"]
            for uk in sorted(set(user_arr[m].tolist())):
                row = {"fold": fold_tag, "model": mname, "split": split, "user_key": uk}
                for mn in metric_names:
                    row[mn] = ev["per_user"][uk][mn]["macro"]
                per_user_rows.append(row)
            # 日级（物理域，按用户 on_thr 的能力口径近似：用各用户 on_thr 的中位仅作日级诊断，
            # 精确 F1 以 per_user_rows 为准）
            try:
                daily = evaluate_daily(y_phys[m], y_hat_phys, idx_all[m], metric_names,
                                       on_thr_w=float(np.median(list(on_thr_map.values()))))
                daily.insert(0, "split", split)
                daily.insert(0, "model", mname)
                daily.insert(0, "fold", fold_tag)
                daily_rows.append(daily)
            except Exception as e:
                log.warning("日级评估跳过: %s", e)
            log.info("[%s] %s %s pooled_macro F1=%.4f R2=%.4f",
                     fold_tag, mname, split,
                     ev["pooled_macro"].get("f1", {}).get("macro", float("nan")),
                     ev["pooled_macro"].get("r2", {}).get("macro", float("nan")))

    # 落盘
    split_rows = []
    for mname, by in results_by_split.items():
        for split, mm in by.items():
            row = {"fold": fold_tag, "model": mname, "split": split}
            for mn in metric_names:
                row[mn] = mm[mn]["macro"]
            split_rows.append(row)
    pd.DataFrame(split_rows).to_csv(fold_out / "metrics_by_split.csv", index=False, encoding="utf-8")
    pd.DataFrame(per_user_rows).to_csv(fold_out / "metrics_by_user.csv", index=False, encoding="utf-8")
    if daily_rows:
        pd.concat(daily_rows, ignore_index=True).to_csv(
            fold_out / "metrics_daily.csv", index=False, encoding="utf-8")
    _dump(fold_out / "meta.json", {
        "fold": fold_tag, "train_users": train_users, "test_users": test_users,
        "feature_names": names, "scale_cols": scale_cols, "scaler": scaler.state(),
        "models": [s["name"] for s in base_cfg.get("models", [])],
        "target_col": (base_cfg.get("pooled") or {}).get("target_col"),
        "cap_quantile": float((base_cfg.get("pooled") or {}).get("cap_quantile", 0.95)),
        "caps": {uk: pre[uk]["cap"] for uk in pre},
        "on_thr_map": on_thr_map,
        "pt_ct_map": {uk: {"pt_ratio": pre[uk].get("pt_ratio", 1.0),
                           "ct_ratio": pre[uk].get("ct_ratio", 1.0),
                           "branch_scale": pre[uk].get("branch_scale", 1.0)} for uk in pre},
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    })
    (fold_out / DONE_MARKER).write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    return {"results_by_split": results_by_split, "per_user_rows": per_user_rows,
            "scaler": scaler, "feature_names": names}


def run_pooled_train(user_keys: list[str], scans: dict, user_cfgs: dict,
                     base_cfg: dict, output_root: str | Path,
                     target_col: str | None = None, loo: bool = False) -> PooledResult:
    """池化训练：多用户拼接 -> 全局 Scaler -> 通用模型。loo=True 时做 LOUO。"""
    out_root = Path(output_root)
    tc = target_col or (base_cfg.get("pooled") or {}).get("target_col") or "p1"
    out = _new_outdir(out_root, tc, "train")
    try:
        # 按 target_col 分组校验：只保留与 tc 一致的用户（其余跳过并告警）
        keep, skip = [], []
        for uk in user_keys:
            u_tc = user_cfgs[uk].get("target_col")
            if u_tc is None or str(u_tc).replace(" ", "").lower() == str(tc).replace(" ", "").lower():
                keep.append(uk)
            else:
                skip.append(uk)
        if skip:
            log.warning("target_col=%s 分组：跳过 %s（其 target_col 与池不一致）", tc, skip)
        if len(keep) < 2 and not loo:
            # 非 LOO 允许单用户池（退化为单用户训练的池化封装），但告警
            log.warning("池内仅 %d 个用户，通用模型泛化存疑", len(keep))
        if not keep:
            raise PooledTaskError(Status.FAILED, f"无与 target_col={tc} 匹配的用户")

        pre: dict[str, dict] = {}
        for uk in keep:
            pre[uk] = _load_user_preprocessed(uk, scans[uk], user_cfgs[uk], base_cfg,
                                              target_col_override=tc, need_branch=True)
            log.info("[%s] cap=%.1f on_thr=%.1f n=%d", uk, pre[uk]["cap"],
                     pre[uk]["on_thr_w"], len(pre[uk]["feat"]))

        if loo:
            folds = louo_folds(keep)
            louo_rows: list[dict] = []
            for train_users, held in folds:
                tag = f"loo_hold_{held}"
                res = _train_one_fold(pre, train_users, [held], base_cfg, out, tag)
                for row in res["per_user_rows"]:
                    if row["split"] == "test":
                        louo_rows.append(row)
            louo_df = pd.DataFrame(louo_rows)
            louo_df.to_csv(out / "louo_summary.csv", index=False, encoding="utf-8")
            # 汇总：每模型 LOUO 均值/最差
            summary = {}
            if not louo_df.empty:
                for mname, g in louo_df.groupby("model"):
                    summary[mname] = {
                        "f1_mean": float(g["f1"].mean()) if "f1" in g else None,
                        "f1_min": float(g["f1"].min()) if "f1" in g else None,
                        "r2_mean": float(g["r2"].mean()) if "r2" in g else None,
                        "n_users": int(g["user_key"].nunique()),
                    }
            _dump(out / "louo_summary.json", summary)
            log.info("LOUO 汇总: %s", summary)
            return PooledResult(Status.OK, f"loo folds={len(folds)}", str(out),
                                metrics=summary)
        else:
            res = _train_one_fold(pre, keep, [], base_cfg, out, "")
            # 非 LOO 时 test 为空（train/val 切分），best 模型按 val F1 选
            return PooledResult(Status.OK, f"users={len(keep)}", str(out),
                                metrics=res["results_by_split"])
    except PooledTaskError as e:
        log.error("pooled train %s: %s", e.status, e)
        return PooledResult(e.status, str(e), str(out))
    except Exception as e:  # noqa: BLE001
        log.error("pooled train 未分类错误: %s\n%s", e, traceback.format_exc())
        return PooledResult(Status.FAILED, str(e), str(out))


def run_pooled_infer(user_key: str, scan, user_cfg: dict, base_cfg: dict,
                     output_root: str | Path, universal_dir: str | Path | None = None,
                     target_col: str | None = None) -> PooledResult:
    """新用户零样本推理：通用模型 + 本用户 cap/on_thr 校准。"""
    out_root = Path(output_root)
    tc = target_col or (base_cfg.get("pooled") or {}).get("target_col") or "p1"
    out = _new_outdir(out_root, tc, "infer")
    try:
        train_dir = Path(universal_dir) if universal_dir else latest_universal_dir(out_root, tc, "train")
        if train_dir is None or not (train_dir / "meta.json").exists():
            raise PooledTaskError(Status.MODEL_NOT_FOUND,
                                  f"找不到通用模型产物（target_col={tc}）")
        meta = json.loads((train_dir / "meta.json").read_text(encoding="utf-8"))
        names = meta["feature_names"]

        pre = _load_user_preprocessed(user_key, scan, user_cfg, base_cfg,
                                      target_col_override=tc, need_branch=False)
        feat = pre["feat"]
        missing = [c for c in names if c not in feat.columns]
        if missing:
            raise PooledTaskError(Status.FAILED, f"推理特征缺列: {missing}")
        feat = feat[names]
        valid = feat.dropna()
        if len(valid) == 0:
            raise PooledTaskError(Status.INSUFFICIENT_TIME_RANGE, "推理区间无有效样本")

        scaler = TrainFitScaler.from_state(meta["scaler"])
        X = scaler.transform(valid.to_numpy(np.float64))
        model_name = user_cfg.get("infer_model") or base_cfg.get("infer_model") or meta["models"][0]
        model_path = train_dir / "models" / f"{model_name}.pkl"
        if not model_path.exists():
            raise PooledTaskError(Status.MODEL_NOT_FOUND, f"模型文件缺失: {model_path}")
        model = BaseModel.load(model_path)
        pred_norm = model.predict(X, index=valid.index).reshape(-1)
        pred = denormalize_target(pred_norm, pre["cap"])
        allow_negative = bool(base_cfg.get("preprocess", {}).get("allow_negative_power", False))
        pred = apply_constraints(pred[:, None], valid["pbus"].to_numpy() * pre["cap"],
                                 nonnegative=not allow_negative, sum_consistency=False)[:, 0]

        user_meta_proxy = {
            "on_thr_w": (meta.get("on_thr_map") or {}).get(user_key, pre["on_thr_w"]),
            "decision_thr_w": (meta.get("decision_thr_map") or {}).get(user_key),
        }
        on_thr, dec_thr, _ = resolve_infer_thresholds(
            user_key, user_cfg, user_meta_proxy, model_name=model_name
        )
        post_min_on = int(user_cfg.get("post_min_on", 1))
        post_fill_off = int(user_cfg.get("post_fill_short_off", 3))
        pred_state = postprocess_state(pred, dec_thr, post_min_on, post_fill_off)
        pred_prob = state_probability(pred, dec_thr)

        _, user_id = split_user_key(user_key) or ("", user_key)
        target_np = np.full(len(valid), np.nan)
        offline = None
        if pre["y_phys"] is not None:
            t = pre["y_phys"].reindex(valid.index)
            target_np = t.to_numpy(dtype=np.float64)
            have = t.dropna()
            if len(have) > 0:
                metric_names = base_cfg.get("metrics", ["mae", "rmse", "r2", "sae", "f1"])
                pred_on_have = pd.Series(pred, index=valid.index).loc[have.index]
                offline = evaluate_all(have.to_numpy()[:, None], pred_on_have.to_numpy()[:, None],
                                       metric_names, on_thr_w=on_thr)
                _dump(out / "offline_metrics.json", offline)
        target_state = np.where(np.isnan(target_np), np.nan, (target_np >= on_thr).astype(float))
        result_csv = out / INFERENCE_RESULT_REL
        result_csv.parent.mkdir(parents=True, exist_ok=True)
        df_result = pd.DataFrame({
            "timestamp": valid.index.strftime("%Y-%m-%d %H:%M:%S"),
            "user_id": user_id, "target": target_np,
            "target_state": pd.array([int(v) if not np.isnan(v) else None for v in target_state],
                                     dtype="Int64"),
            "on_thr_w": on_thr, "pred": pred, "pred_state": pred_state.astype(int),
            "decision_thr_w": dec_thr, "pred_prob": np.round(pred_prob, 6),
        })
        df_result[INFER_RESULT_COLUMNS].to_csv(result_csv, index=False)
        _dump(out / "meta.json", {
            "user_key": user_key, "mode": "infer", "model": model_name,
            "universal_dir": str(train_dir), "cap": pre["cap"], "n_points": len(valid),
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        })
        (out / DONE_MARKER).write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
        log.info("[%s] 通用推理完成：%d 点 -> %s", user_key, len(valid), result_csv)
        return PooledResult(Status.OK, f"model={model_name}, n={len(valid)}", str(out),
                            metrics=offline and {m: v["macro"] for m, v in offline.items()})
    except PooledTaskError as e:
        log.error("[%s] %s: %s", user_key, e.status, e)
        return PooledResult(e.status, str(e), str(out))
    except Exception as e:  # noqa: BLE001
        log.error("[%s] 未分类错误: %s\n%s", user_key, e, traceback.format_exc())
        return PooledResult(Status.FAILED, str(e), str(out))
