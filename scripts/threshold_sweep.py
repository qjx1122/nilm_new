#!/usr/bin/env python3
"""inference_result.csv / train_predictions.csv 判决链阈值扫描（离线分析，零重跑）。

原理：pred 列是回归原始输出，与 decision_thr_w 无关——故可在同一份推理/
训练预测产物上离线重构任意判决阈值下的完整生产判决链（decision_thr_w +
post_min_on + post_fill_short_off，nilm.postprocess.state.postprocess_state）
的混淆矩阵，无需逐阈值重跑训练/推理。用于：

  1. 判决阈值选型：thr → tp/fp/fn/P/R/F1（全关日·开机日 fp 分解）曲线；
  2. 虚报幅值结构：fp（ts=0 且 pred≥基准阈值，raw 判态）的功率带分布——
     判定「判决阈值是否为虚报治理的有效杠杆」（⑯ thr30 教训：全关日虚报
     主体幅值 ≥30W 时低阈值不是杠杆；先看幅值带再谈调参）；
  3. 跨月/跨段稳健性（⑯ thr400 确认 run 教训）：train_predictions.csv 经
     --pred-col/--state-col/--split 扫 val/test 段曲线——交付月与留出月的
     功率带结构差异直接显形（静态阈值是否可跨月复用）。

用法（在仓库根目录）：
    # 推理产物（交付口径）
    python scripts/threshold_sweep.py --csv <inference_result.csv>
    # 训练预测产物的 test 段（跨月护栏曲线）
    python scripts/threshold_sweep.py --csv <train_predictions.csv> \\
        --pred-col pred_transformer --state-col pred_state_transformer \\
        --split test --thresholds 10,30,50,100,150,200,250,300,400,500

输出：文件自描述（decision_thr_w/on_thr_w）+ 判决链复现校验（用文件自描述
阈值重构 pred_state 逐位比对，校验 --min-on/--fill-off 与文件配置一致性）+
阈值扫描表 + fp 幅值分布表（均打印到控制台，供实录粘贴）。
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from nilm.postprocess.state import postprocess_state  # noqa: E402

# fp 幅值分布固定功率带（W）；基准阈值取扫描列表最小值（默认 10，同 on_thr_w）
AMPLITUDE_BANDS = [(10, 20), (20, 30), (30, 50), (50, 100), (100, 200), (200, None)]


def _resolve_csv_arg(csv_arg: str | Path) -> Path:
    """解析 --csv 参数，兼容 shell 通配（* ? []）跨平台（Windows PowerShell 无自动展开）。

    - 含通配：glob 展开，排序后取最新（时间戳目录 YYYYMMDD_HHMMSS 末位即最新）；
    - 无通配：原样返回（pandas 再校验存在性）。
    """
    s = str(csv_arg)
    if any(ch in s for ch in ("*", "?", "[")):
        matches = sorted(glob.glob(s))
        if not matches:
            raise SystemExit(f"[threshold_sweep] 未找到匹配 --csv 通配 {s}（检查 outputs/<user>/train|infer/<timestamp>/predictions/*.csv 是否存在）")
        chosen = matches[-1]
        if len(matches) > 1:
            preview = ", ".join(matches[:3]) + (" ..." if len(matches) > 3 else "")
            print(f"通配展开 {len(matches)} 个，取最新: {chosen}（{preview}）")
        return Path(chosen)
    return Path(s)


def load_result(csv_path: str | Path, pred_col: str = "pred") -> pd.DataFrame:
    """读入预测 CSV 并校验必需列（timestamp/target_state/<pred_col>）。

    默认按 inference_result.csv 契约；train_predictions.csv（列名
    pred_<model>）经 --pred-col 适配。
    """
    csv_path = _resolve_csv_arg(csv_path)
    df = pd.read_csv(csv_path)
    missing = [c for c in ("timestamp", "target_state", pred_col) if c not in df.columns]
    if missing:
        raise SystemExit(f"[threshold_sweep] 缺少必需列 {missing}"
                         "（--pred-col/--state-col 是否与文件列名匹配？）")
    return df


def _day_of(df: pd.DataFrame) -> pd.Series:
    return df["timestamp"].astype(str).str.slice(0, 10)


def sweep(df: pd.DataFrame, thresholds, min_on: int, fill_off: int,
          pred_col: str = "pred") -> pd.DataFrame:
    """逐阈值重构完整判决链（thr + min_on + fill_off）混淆矩阵。

    空真约定与 evaluation.metrics / user_task.state_strategy 一致：
    无开态预测时 precision = 1.0（无漏报）/ 0.0（有漏报）。
    off_day_fp = 全关天（当日 target_state 全 0）上的 fp。
    """
    ts = df["target_state"].astype(int).to_numpy()
    pred = df[pred_col].to_numpy(dtype=float)
    day = _day_of(df).to_numpy()
    day_on = pd.Series(ts).groupby(day).transform("max").to_numpy().astype(bool)
    rows = []
    for thr in thresholds:
        st = postprocess_state(pred, float(thr), min_on, fill_off)
        tp = int((st & (ts == 1)).sum())
        fp = int((st & (ts == 0)).sum())
        fn = int((~st & (ts == 1)).sum())
        tn = int((~st & (ts == 0)).sum())
        prec = tp / (tp + fp) if (tp + fp) > 0 else (1.0 if fn == 0 else 0.0)
        rec = tp / (tp + fn) if tp + fn else 1.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        fp_off = int((st & (ts == 0) & ~day_on).sum())
        rows.append({"thr": thr, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
                     "precision": round(prec, 4), "recall": round(rec, 4),
                     "f1": round(f1, 4),
                     "off_day_fp": fp_off, "on_day_fp": fp - fp_off})
    return pd.DataFrame(rows)


def fp_amplitude(df: pd.DataFrame, base_thr: float,
                 pred_col: str = "pred") -> pd.DataFrame:
    """fp 幅值分布：ts=0 且 pred≥base_thr（raw 判态，未过判决链）的功率带。

    幅值带固定（10 起）；若 base_thr<10，[base_thr,10) 的点不计入带内。
    """
    mask = (df["target_state"].astype(int) == 0) & (df[pred_col] >= float(base_thr))
    vals = df.loc[mask, pred_col]
    rows = []
    for lo, hi in AMPLITUDE_BANDS:
        n = int(((vals >= lo) & (vals < hi)).sum()) if hi is not None \
            else int((vals >= lo).sum())
        rows.append({"band": f"[{lo}, {'∞' if hi is None else hi})",
                     "count": n,
                     "pct": round(100.0 * n / len(vals), 1) if len(vals) else 0.0})
    return pd.DataFrame(rows)


def chain_replay_check(df: pd.DataFrame, min_on: int, fill_off: int,
                       pred_col: str = "pred",
                       state_col: str = "pred_state") -> tuple[float, bool]:
    """用文件自描述 decision_thr_w 重构 pred_state 并逐位比对。

    返回 (dec_thr, 是否一致)；不一致 ⇒ --min-on/--fill-off 与文件生成配置
    不符（或状态列被改动），扫描结果不可信。
    """
    dec = float(df["decision_thr_w"].iloc[0])
    if df["decision_thr_w"].nunique() > 1:
        return dec, False
    replay = postprocess_state(df[pred_col].to_numpy(dtype=float), dec,
                               min_on, fill_off)
    ok = bool((replay.astype(int) == df[state_col].astype(int)).all())
    return dec, ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="inference_result.csv 判决链阈值扫描（离线，零重跑）")
    ap.add_argument("--csv", required=True,
                    help="inference_result.csv 或 train_predictions.csv 路径（支持 * 通配，如 train/*/predictions/train_predictions.csv，Windows PowerShell 亦可）")
    ap.add_argument("--thresholds", default="10,20,30,40,50,60,80,100,150",
                    help="逗号分隔的判决阈值列表（W）")
    ap.add_argument("--min-on", type=int, default=1,
                    help="判决链 post_min_on（须与文件配置一致，复现校验把关）")
    ap.add_argument("--fill-off", type=int, default=3,
                    help="判决链 post_fill_short_off（须与文件配置一致）")
    ap.add_argument("--pred-col", default="pred",
                    help="预测功率列名（inference_result=pred；"
                         "train_predictions=pred_<model>）")
    ap.add_argument("--state-col", default="pred_state",
                    help="预测状态列名（复现校验用；文件缺该列则跳过校验）")
    ap.add_argument("--split", default=None,
                    help="按 split 列过滤段（train_predictions.csv: train/val/test）")
    args = ap.parse_args(argv)

    resolved = _resolve_csv_arg(args.csv)
    df = load_result(resolved, args.pred_col)
    if str(resolved) != str(args.csv):
        print(f"已解析通配: {args.csv} -> {resolved}")
    if args.split:
        if "split" not in df.columns:
            raise SystemExit("[threshold_sweep] 指定了 --split 但文件无 split 列")
        n0 = len(df)
        df = df[df["split"] == args.split].reset_index(drop=True)
        print(f"split 过滤: {args.split}（{n0} → {len(df)} 行）")
    n_raw = len(df)
    df = df.dropna(subset=["target_state", args.pred_col]).reset_index(drop=True)
    thrs = [float(x) for x in str(args.thresholds).split(",") if x.strip()]

    print(f"文件: {args.csv}")
    if "decision_thr_w" in df.columns:
        on_note = (f" | on_thr_w: {sorted(df['on_thr_w'].unique().tolist())}"
                   if "on_thr_w" in df.columns else "")
        print(f"自描述 decision_thr_w: {sorted(df['decision_thr_w'].unique().tolist())}"
              f"{on_note}")
    day = _day_of(df)
    ts = df["target_state"].astype(int)
    day_max = ts.groupby(day.to_numpy()).max()
    off_days = sorted(day_max[day_max == 0].index.tolist())
    print(f"数据: n={len(df)} 点（剔除 NaN {n_raw - len(df)} 行），"
          f"{day.nunique()} 天（全关天 {len(off_days)}：{', '.join(off_days)}）")
    if args.state_col in df.columns and "decision_thr_w" in df.columns:
        dec, ok = chain_replay_check(df, args.min_on, args.fill_off,
                                     pred_col=args.pred_col,
                                     state_col=args.state_col)
        verdict = "一致 ✓" if ok else "不一致 ✗（--min-on/--fill-off 与文件配置不符？）"
        print(f"判决链复现校验（thr={dec:g}, min_on={args.min_on}, "
              f"fill_off={args.fill_off}）: pred_state 逐位{verdict}")
    print(f"\n== 阈值扫描（判决链 = decision_thr_w + min_on={args.min_on} "
          f"+ fill_off={args.fill_off}）==")
    print(sweep(df, thrs, args.min_on, args.fill_off,
                pred_col=args.pred_col).to_string(index=False))
    print(f"\n== fp 幅值分布（ts=0 且 pred≥{min(thrs):g}W，raw 判态）==")
    print(fp_amplitude(df, min(thrs), pred_col=args.pred_col).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
