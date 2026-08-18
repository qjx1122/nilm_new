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
from nilm.common.logging import get_logger
from nilm.common.schema import bus_total
from nilm.common.timefilter import filter_dataframe
from nilm.data_io.csv_source import CsvBranchLoader, CsvBusLoader
from nilm.data_io.validator import (QualityError, assert_quality,
                                    daily_quality_table, invalid_data_days,
                                    qualified_days_detail, qualified_days_summary,
                                    quality_advice, quality_report,
                                    split_coverage_advice, write_quality_html,
                                    write_schema_report)
from nilm.evaluation import (build_comparison_table, evaluate_all,
                             evaluate_daily, summarize)
from nilm.models import MODEL_REGISTRY
from nilm.models.base import BaseModel
from nilm.models.constraints import apply_constraints
from nilm.postprocess.state import postprocess_state, state_probability
from nilm.preprocess.align import align_frames, estimate_time_offset, resample_bus
from nilm.preprocess.clean import Cleaner
from nilm.preprocess.dataset import DEFAULT_WINDOW, build_windows, drop_invalid_rows
from nilm.preprocess.features import build_features
from nilm.preprocess.scaling import TrainFitScaler
from nilm.preprocess.splits import build_split_masks
from nilm.preprocess.target import build_target, resolve_target_cols
from nilm.reporting import write_markdown_report

log = get_logger("pipeline.user_task")

DONE_MARKER = "_DONE"
NON_SCALED_COLS = {"slot", "hour", "minute", "day_of_week", "is_weekend",
                   "month", "day_of_year"}


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
        field_map = base_cfg.get("bus_field_map") or user_cfg.get("bus_field_map")
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

        # —— 训练前分路开机情况分析（有效通道=目标通道，逐天开机段/时长/功率/电量）
        sessions = analyze_branch_sessions(branch_c, float(user_cfg["on_thr_w"]),
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
                               on_thr_w=float(user_cfg["on_thr_w"]))
        # branch 报告=有效通道（目标通道）口径：无效通道已在清洗后丢弃
        q_br = quality_report(branch_al, "branch", 96, allow_negative,
                              on_thr_w=float(user_cfg["on_thr_w"]))
        q_br["target_cols"] = target_cols
        # 逐天质量表（总线/分路得分、阈值、当天合格）+ 双达标统计 + 训练建议
        min_score = float(qcfg.get("min_score", 50))
        daily_q = daily_quality_table(bus_al, branch_al, 96, min_score, allow_negative)
        daily_q.to_csv(out / "daily_quality.csv", index=False, encoding="utf-8")
        advice = quality_advice(daily_q, float(qcfg.get("min_days", 14)))
        q_br["both_qualified_days"] = int((daily_q["qualified"] == 1).sum())
        q_br["daily_total_days"] = int(len(daily_q))
        _dump(out / "quality_advice.json", {"advice": advice})
        write_quality_html(out / "data_quality_report.html", [q_bus, q_br],
                           daily_quality=daily_q, advice=advice)
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

        if len(bus_al) < 96 * float(qcfg.get("min_days", 14)):
            raise UserTaskError(Status.INSUFFICIENT_TIME_RANGE,
                                f"有效数据不足 {qcfg.get('min_days', 14)} 天（现有 {len(bus_al)/96:.1f} 天）")

        # —— §9 可辨识性分析（训练前强制执行）
        ident = identifiability_report(bus_al, target, on_thr_w=float(user_cfg["on_thr_w"]))
        _dump(out / "identifiability_report.json", ident)
        if not ident.get("identifiable", True):
            log.warning("[%s] %s: %s", user_key, Status.IDENTIFIABILITY_LOW, ident.get("risk"))

        # —— §8 特征 + §10/§11 样本与切分
        fc = base_cfg.get("features", {})
        feat = build_features(bus_al, lags=tuple(fc.get("lags", [1, 2, 3, 4])),
                              rolling_windows=tuple(fc.get("rolling_windows", ["1h", "6h", "24h"])))
        f, y = drop_invalid_rows(feat, target)
        window = int(base_cfg.get("dataset", {}).get("window", DEFAULT_WINDOW))
        if len(f) < window * 2:
            raise UserTaskError(Status.INSUFFICIENT_TIME_RANGE,
                                f"有效样本 {len(f)} < 2×L({window})")

        masks = build_split_masks(f.index, user_cfg["split_ratios"],
                                  user_cfg["split_strategy"], user_cfg.get("splits"))
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
            daily_q, target, float(user_cfg["on_thr_w"]),
            split_index={k: splits[k][2] for k in ("train", "val", "test")
                         if split_sizes.get(k, 0) > 0})
        q_detail.to_csv(out / "qualified_days_detail.csv", index=False, encoding="utf-8")
        q_br["qualified_days_stats"] = qualified_days_summary(q_detail)
        advice = advice + split_coverage_advice(q_detail)
        _dump(out / "quality_advice.json", {"advice": advice})
        write_quality_html(out / "data_quality_report.html", [q_bus, q_br],
                           daily_quality=daily_q, advice=advice,
                           qualified_detail=q_detail)  # 重写含双达标统计

        # Scaler 只由 Train 拟合（§11）；日历列不缩放
        scale_cols = [i for i, c in enumerate(names) if c not in NON_SCALED_COLS]
        scaler = TrainFitScaler().fit(splits["train"][0], cols=scale_cols)
        scaled = {k: (scaler.transform(v[0]), v[1], v[2]) for k, v in splits.items()}

        # §10 窗口样本索引落盘（默认 Seq2Seq）
        wmode = base_cfg.get("dataset", {}).get("mode", "seq2seq")
        if split_sizes["train"] >= window:
            _, _, wmeta = build_windows(scaled["train"][0], scaled["train"][1],
                                        scaled["train"][2], window=window, mode=wmode)
            wmeta.to_csv(out / "train_window_index.csv", index=False)

        # —— 多模型训练与三阶段（train/val/test）评估（模块解耦：只经注册表接口）
        metric_names = base_cfg.get("metrics", ["mae", "rmse", "r2", "sae"])
        on_thr = float(user_cfg["on_thr_w"])
        pbus_col = names.index("pbus")
        results: dict[str, dict] = {}          # {model: test 指标}（选型口径不变）
        results_by_split: dict[str, dict] = {}  # {model: {split: 指标}} 三阶段全量
        daily_rows: list[pd.DataFrame] = []     # 每模型×每阶段×每天 指标
        test_preds: dict[str, np.ndarray] = {}  # {model: test 段预测}（状态策略评估用）
        best = None
        for spec in base_cfg.get("models", []):
            name, params = spec["name"], spec.get("params", {})
            model = MODEL_REGISTRY.create(name, **params)
            model.fit(scaled["train"][0], scaled["train"][1], feature_names=names,
                      X_val=scaled["val"][0], y_val=scaled["val"][1])
            model.save(out / "models" / f"{name}.pkl")

            results_by_split[name] = {}
            for split in ("train", "val", "test"):
                if len(scaled[split][0]) == 0:
                    continue
                y_hat = model.predict(scaled[split][0])
                y_hat = apply_constraints(y_hat, splits[split][0][:, pbus_col],
                                          nonnegative=not allow_negative,
                                          sum_consistency=False)
                metrics = evaluate_all(scaled[split][1], y_hat, metric_names,
                                       on_thr_w=on_thr)
                results_by_split[name][split] = metrics
                daily = evaluate_daily(scaled[split][1], y_hat, splits[split][2],
                                       metric_names, on_thr_w=on_thr)
                daily.insert(0, "split", split)
                daily.insert(0, "model", name)
                daily_rows.append(daily)
                log.info("[%s] 模型 %s %s 指标: %s", user_key, name, split,
                         {m: round(v["macro"], 4) for m, v in metrics.items()})
                if split == "test":
                    test_preds[name] = y_hat[:, 0]
            results[name] = results_by_split[name]["test"]  # 选型口径：test（不变）

        # 三阶段汇总 CSV：model × split 行 × 指标列
        split_rows = [{"model": mname, "split": s,
                       **{m: v["macro"] for m, v in mm.items()}}
                      for mname, by in results_by_split.items()
                      for s, mm in by.items()]
        pd.DataFrame(split_rows).to_csv(out / "metrics_by_split.csv",
                                        index=False, encoding="utf-8")
        # 日级指标 CSV：model × split × date 行 × 指标列
        pd.concat(daily_rows, ignore_index=True).to_csv(
            out / "metrics_daily.csv", index=False, encoding="utf-8")

        # —— 状态策略评估（test）：决策阈值 + 游程后处理下的 F1（全量 / 仅开机日两口径）
        dec_thr = float(user_cfg.get("decision_thr_w") or on_thr)
        strat_rows = []
        y_test = splits["test"][1][:, 0]
        idx_test = splits["test"][2]
        t_on_test = y_test >= on_thr
        day_on = pd.Series(t_on_test, index=idx_test).groupby(
            idx_test.normalize()).transform("max").to_numpy()
        # 三条策略：raw = 与 metrics_by_split 同口径的对照行（on_thr_w 判决、无游程），
        # 便于跨产物对账；decision+runs = 生产判决链（decision_thr_w + min_on/fill）
        strategies = [("raw_on_thr", on_thr, 0, 0),
                      ("decision+runs", dec_thr, int(user_cfg["post_min_on"]),
                       int(user_cfg["post_fill_short_off"]))]
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
        write_markdown_report(out, f"{user_key}/train", table, summary,
                              notes=[f"target_col={'+'.join(target_cols)}",
                                     f"split_strategy={user_cfg['split_strategy']}"])
        best = summary.get("overall_best")

        _dump(out / "meta.json", {
            "user_key": user_key, "mode": mode, "target_cols": target_cols,
            "feature_names": names, "scale_cols": scale_cols,
            "scaler": scaler.state(), "window": window, "window_mode": wmode,
            "split_sizes": split_sizes, "split_strategy": user_cfg["split_strategy"],
            "best_model": best, "models": [s["name"] for s in base_cfg.get("models", [])],
            "on_thr_w": user_cfg["on_thr_w"],
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
            # 目标通道为唯一有效分路，其余通道（不在当前总线回路）清洗后立即丢弃
            tcols_i = resolve_target_cols(user_cfg.get("target_col"), branch_c)
            invalid_ch = [c for c in branch_c.columns if c not in tcols_i]
            if invalid_ch:
                log.info("[%s] 丢弃无效分路通道 %s（不在当前总线回路，仅保留目标通道 %s）",
                         user_key, invalid_ch, tcols_i)
                branch_c = branch_c[tcols_i]
            _save_cleaned_csv(out, "branch", branch_c, save_cleaned)
            sessions = analyze_branch_sessions(branch_c, float(user_cfg["on_thr_w"]),
                                               columns=tcols_i)
            sessions.to_csv(out / "branch_sessions.csv", index=False, encoding="utf-8")
            # 数据质量报告（与训练阶段同构：bus+branch 有效通道口径；只报告不设门禁）
            q_bus_i = quality_report(bus15, "bus", 96, allow_negative,
                                     on_thr_w=float(user_cfg["on_thr_w"]))
            q_br_i = quality_report(branch_c, "branch", 96, allow_negative,
                                    on_thr_w=float(user_cfg["on_thr_w"]))
            q_br_i["target_cols"] = tcols_i
            # 逐天质量表 + 双达标统计 + 建议（推理侧建议主要看数据可用性）
            min_score_i = float(base_cfg.get("quality", {}).get("min_score", 50))
            daily_q_i = daily_quality_table(bus15, branch_c, 96, min_score_i,
                                            allow_negative)
            daily_q_i.to_csv(out / "daily_quality.csv", index=False, encoding="utf-8")
            advice_i = quality_advice(daily_q_i,
                                      float(base_cfg.get("quality", {})
                                            .get("min_days", 14)))
            q_br_i["both_qualified_days"] = int((daily_q_i["qualified"] == 1).sum())
            q_br_i["daily_total_days"] = int(len(daily_q_i))
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

        fc = base_cfg.get("features", {})
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

        scaler = TrainFitScaler.from_state(meta["scaler"])
        X = scaler.transform(valid.to_numpy(np.float64))

        # 模型选择：配置指定 > 训练最优；只用本用户模型
        model_name = base_cfg.get("infer_model") or meta.get("best_model") or meta["models"][0]
        if model_name not in meta["models"]:
            raise UserTaskError(Status.MODEL_NOT_FOUND, f"模型 {model_name} 不在该用户训练清单")
        model_path = train_dir / "models" / f"{model_name}.pkl"
        if not model_path.exists():
            raise UserTaskError(Status.MODEL_NOT_FOUND, f"模型文件缺失: {model_path}")
        model = BaseModel.load(model_path)
        pred = model.predict(X).reshape(-1)
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
                    metric_names, on_thr_w=float(user_cfg["on_thr_w"]))
                _dump(out / "offline_metrics.json", offline_metrics)
                # 质量报告补充双达标天明细（推理评估段=推理集）
                if infer_quality is not None:
                    infer_days = set(have.index.normalize().strftime("%Y-%m-%d"))
                    q_detail_i = qualified_days_detail(
                        daily_q_i, t, float(user_cfg["on_thr_w"]),
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
                                       on_thr_w=float(user_cfg["on_thr_w"]))
                daily.insert(0, "model", model_name)
                daily.to_csv(out / "metrics_daily.csv", index=False, encoding="utf-8")

        on_thr = float(user_cfg["on_thr_w"])
        # 决策阈值（§12.3 扩展）：仅作用于预测→状态判决；缺省沿用 on_thr_w
        dec_thr = float(user_cfg.get("decision_thr_w") or on_thr)
        pred_state = postprocess_state(pred, dec_thr,
                                       int(user_cfg["post_min_on"]),
                                       int(user_cfg["post_fill_short_off"]))
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
            "pred": pred,
            "pred_state": pred_state.astype(int),
            "pred_prob": np.round(pred_prob, 6),
        })
        df_result[INFER_RESULT_COLUMNS].to_csv(result_csv, index=False)

        _dump(out / "meta.json", {
            "user_key": user_key, "mode": mode, "model": model_name,
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
