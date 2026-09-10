#!/usr/bin/env python3
"""日级评估指标达标分析：扫描各用户最新 train/infer 的 metrics_daily.csv，
按达标口径（默认 SAE<0.20 且 F1>0.90）逐模型逐天判定，产出：

- outputs/analysis/daily_metrics_compliance.csv  逐行判定明细（用户×模型×阶段×天）
- outputs/analysis/daily_metrics_summary.csv     汇总（用户×模型×阶段 达标率）
- 控制台报告：不达标 TOP 原因归类（供数据/模型分析）

用法：
    python scripts/analyze_daily_metrics.py [--output-root outputs]
        [--sae-max 0.20] [--f1-min 0.90] [--split test]（默认只看 test；all=三阶段）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nilm.common.logging import get_logger, setup_logging  # noqa: E402
from nilm.pipeline.user_task import latest_done_dir  # noqa: E402

log = get_logger("cli.daily_analysis")


def load_daily(output_root: Path) -> pd.DataFrame:
    """收集所有用户最新完成运行的 metrics_daily.csv（train+infer）。"""
    rows = []
    for user_dir in sorted(output_root.iterdir()):
        if not user_dir.is_dir() or "_" not in user_dir.name:
            continue
        for mode in ("train", "infer"):
            d = latest_done_dir(user_dir / mode)
            if d is None or not (d / "metrics_daily.csv").exists():
                continue
            df = pd.read_csv(d / "metrics_daily.csv")
            df.insert(0, "mode", mode)
            df.insert(0, "user_key", user_dir.name)
            if "split" not in df.columns:  # infer 无 split 列
                df["split"] = "infer"
            rows.append(df)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def judge(df: pd.DataFrame, sae_max: float, f1_min: float) -> pd.DataFrame:
    """逐行达标判定 + 不达标原因归类。"""
    df = df.copy()
    df["sae_ok"] = df["sae"] < sae_max
    df["f1_ok"] = df["f1"] > f1_min
    df["compliant"] = df["sae_ok"] & df["f1_ok"]

    def reason(r) -> str:
        if r["compliant"]:
            return ""
        why = []
        if not r["sae_ok"]:
            why.append(f"SAE {r['sae']:.3f}≥{sae_max}")
        if not r["f1_ok"]:
            why.append(f"F1 {r['f1']:.3f}≤{f1_min}")
        # 归因辅助标记
        if r.get("tp", 1) == 0 and r.get("fn", 0) > 0:
            why.append("全漏报(模型未预测出开机)")
        elif r.get("tp", 1) == 0 and r.get("fp", 0) > 0:
            why.append("全误报(真值全关但预测开机)")
        elif r.get("fn", 0) == 0 and r.get("fp", 0) == 0 and r.get("tp", 0) == 0:
            why.append("全关日(无开机事件)")
        return "; ".join(why)

    df["reason"] = df.apply(reason, axis=1)
    return df


def main() -> int:
    ap = argparse.ArgumentParser(description="日级指标达标分析（SAE/F1 口径）")
    ap.add_argument("--output-root", default="outputs")
    ap.add_argument("--sae-max", type=float, default=0.20)
    ap.add_argument("--f1-min", type=float, default=0.90)
    ap.add_argument("--split", default="test",
                    help="train 侧统计的切分（train/val/test/all，默认 test；infer 恒含）")
    args = ap.parse_args()

    setup_logging()
    root = Path(args.output_root)
    daily = load_daily(root)
    if daily.empty:
        log.error("未找到任何 metrics_daily.csv，请先执行批量训练/推理")
        return 1
    if args.split != "all":
        daily = daily[daily["split"].isin([args.split, "infer"])]

    judged = judge(daily, args.sae_max, args.f1_min)
    out_dir = root / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    detail_csv = out_dir / "daily_metrics_compliance.csv"
    judged.to_csv(detail_csv, index=False, encoding="utf-8")

    grp = judged.groupby(["user_key", "mode", "split", "model"]).agg(
        days=("date", "nunique"), compliant_days=("compliant", "sum"),
        sae_median=("sae", "median"), f1_median=("f1", "median")).reset_index()
    grp["compliance_rate"] = (grp["compliant_days"] / grp["days"]).round(3)
    summary_csv = out_dir / "daily_metrics_summary.csv"
    grp.to_csv(summary_csv, index=False, encoding="utf-8")

    log.info("达标口径: SAE<%s 且 F1>%s；明细 -> %s；汇总 -> %s",
             args.sae_max, args.f1_min, detail_csv, summary_csv)
    bad = judged[~judged["compliant"]]
    log.info("总计 %d 行（模型×天），不达标 %d 行（%.1f%%）",
             len(judged), len(bad), 100 * len(bad) / max(len(judged), 1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
