"""三阶段编排：train → evaluate → compare（技术方案 §7.2）。

阶段产物全部落在实验目录：config 快照 / 模型 / metrics.json / 对比表 / 报告。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from nilm.common.logging import get_logger
from nilm.evaluation import build_comparison_table, evaluate_all, summarize
from nilm.models import MODEL_REGISTRY
from nilm.models.constraints import apply_constraints
from nilm.pipeline.context import build_datasource, make_output_dir, set_global_seed
from nilm.preprocess import (Cleaner, align_frames, build_features,
                             build_supervised_matrix, resample_bus_to_branch_freq,
                             time_split)
from nilm.reporting import write_markdown_report

log = get_logger("pipeline.runner")


# ---------------------------------------------------------------- 数据准备
class Prepared:
    """一次实验内跨阶段共享的数据准备结果（只读）。"""

    def __init__(self) -> None:
        self.feature_names: list[str] = []
        self.splits: dict[str, pd.DatetimeIndex] = {}
        self.features: pd.DataFrame | None = None
        self.branch: pd.DataFrame | None = None
        self.branch_names: list[str] = []


def _prepare(cfg: dict) -> Prepared:
    """data_io → clean → align → features → split，产出共享上下文。"""
    source = build_datasource(cfg)
    pp = cfg.get("preprocess", {})

    bus_raw = source.load_bus()
    branch_raw = source.load_branch()

    cleaner = Cleaner(clip_negative=pp.get("clip_negative", True),
                      max_gap_interp=pp.get("max_gap_interp", 2))
    bus15 = resample_bus_to_branch_freq(cleaner.transform(bus_raw))
    branch = cleaner.transform(branch_raw)
    bus_al, branch_al = align_frames(bus15, branch)

    fc = cfg.get("features", {})
    feat = build_features(bus_al,
                          lags=tuple(fc.get("lags", [1, 2, 3, 4])),
                          rolling_windows=tuple(fc.get("rolling_windows", ["1h", "6h", "24h"])))

    prep = Prepared()
    prep.features = feat
    prep.branch = branch_al
    prep.branch_names = [c for c in branch_al.columns]
    prep.feature_names = [str(c) for c in feat.columns]
    prep.splits = time_split(feat.index,
                             train_frac=pp.get("train_frac", 0.7),
                             val_frac=pp.get("val_frac", 0.15))
    log.info("数据准备完成：%d 个公共时间点，分路 %s，划分 %s",
             len(feat), prep.branch_names,
             {k: len(v) for k, v in prep.splits.items()})
    return prep


def _matrices(cfg: dict, prep: Prepared, split: str):
    X, y, names = build_supervised_matrix(prep.features, prep.branch, prep.splits[split])
    return X, y, names


def _p_bus_of(X: np.ndarray, feature_names: list[str]) -> np.ndarray:
    return X[:, feature_names.index("p_total")]


# ---------------------------------------------------------------- 阶段：train
def run_train(cfg: dict, prep: Prepared | None = None,
              out_dir: str | Path | None = None) -> tuple[Prepared, Path, dict]:
    """训练配置中列出的全部模型并落盘。返回 (prep, out_dir, model_paths)。"""
    set_global_seed(cfg)
    out_dir = Path(out_dir) if out_dir else make_output_dir(cfg)
    models_dir = out_dir / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    if prep is None:
        prep = _prepare(cfg)

    # 配置快照 + 元信息（可复现）
    (out_dir / "config.snapshot.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    (out_dir / "meta.json").write_text(json.dumps({
        "feature_names": prep.feature_names,
        "branch_names": prep.branch_names,
        "split_sizes": {k: len(v) for k, v in prep.splits.items()},
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    X_tr, y_tr, names = _matrices(cfg, prep, "train")
    X_va, y_va, _ = _matrices(cfg, prep, "val")

    model_paths: dict[str, Path] = {}
    for spec in cfg["models"]:
        name, params = spec["name"], spec.get("params", {})
        model = MODEL_REGISTRY.create(name, **params)
        model.fit(X_tr, y_tr, feature_names=names, X_val=X_va, y_val=y_va)
        path = models_dir / f"{name}.pkl"
        model.save(path)
        model_paths[name] = path
        log.info("已训练并保存模型 %s -> %s", name, path)
    return prep, out_dir, model_paths


# ---------------------------------------------------------------- 阶段：evaluate
def run_evaluate(cfg: dict, prep: Prepared | None = None,
                 out_dir: str | Path | None = None,
                 model_paths: dict | None = None) -> tuple[dict, Path]:
    """在测试集上评估全部模型，落盘 metrics.json。返回 ({model: 指标}, out_dir)。"""
    from nilm.models.base import BaseModel

    if prep is None or model_paths is None:
        prep, out_dir, model_paths = run_train(cfg, prep, out_dir)
    out_dir = Path(out_dir)

    X_te, y_te, _ = _matrices(cfg, prep, "test")
    p_bus = _p_bus_of(X_te, prep.feature_names)
    cc = cfg.get("constraints", {})

    results: dict[str, dict] = {}
    for name, path in model_paths.items():
        model = BaseModel.load(path)
        y_hat = model.predict(X_te)
        y_hat = apply_constraints(y_hat, p_bus,
                                  nonnegative=cc.get("nonnegative", True),
                                  sum_consistency=cc.get("sum_consistency", True))
        metrics = evaluate_all(y_te, y_hat, cfg.get("metrics", ["mae", "rmse", "r2", "sae"]))
        metrics["_y_true"] = None  # 占位：大矩阵不落 JSON
        results[name] = {k: v for k, v in metrics.items() if not k.startswith("_")}
        log.info("模型 %s 测试集指标: %s", name,
                 {m: round(v["macro"], 4) for m, v in results[name].items()})

    (out_dir / "metrics.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return results, out_dir


# ---------------------------------------------------------------- 阶段：compare
def run_compare(cfg: dict, results: dict, out_dir: str | Path) -> dict:
    """汇总对比表 + Markdown 报告。返回 {'table','summary','report_path'}。"""
    out_dir = Path(out_dir)
    table = build_comparison_table(results)
    table.to_csv(out_dir / "comparison.csv", encoding="utf-8")
    summary = summarize(table)
    notes = [
        f"分路数：评估基于测试集宏平均；基线下界：history_profile / proportional",
        "约束后处理：nonnegative + sum_consistency（见 config 快照）",
    ]
    report_path = write_markdown_report(out_dir, cfg.get("experiment_name", "exp"),
                                        table, summary, notes=notes)
    return {"table": table, "summary": summary, "report_path": report_path}


# ---------------------------------------------------------------- 一键全流程
def run_all(cfg: dict, output_root: str | Path | None = None) -> dict:
    """train → evaluate → compare 一键全流程，返回汇总信息。"""
    out_dir = make_output_dir(cfg, output_root)
    prep, out_dir, model_paths = run_train(cfg, None, out_dir)
    results, out_dir = run_evaluate(cfg, prep, out_dir, model_paths)
    cmp = run_compare(cfg, results, out_dir)
    log.info("全流程完成，产物目录: %s | 综合最优: %s",
             out_dir, cmp["summary"].get("overall_best"))
    return {"output_dir": str(out_dir), "results": results, **cmp}
