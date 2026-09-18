#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键执行 TUNING_GUIDE Step 2~5（训练 → 窗口自检 → 推理 → 阈值扫描）

来源：TUNING_GUIDE.md v1.1 §3 Step 2-5（已对齐 base_optimal lag5[5,1,2,3,4] + 4模型择优）
新增：适配新用户 800080270733_4206673297219（及任意 --user-key），兼容 Windows PowerShell（* 通配由脚本内部展开，已修复 threshold_sweep May-2026-09-19）

用法（仓库根目录）：
  # 新用户 733 一键（默认自动补 time_filters.json 缺省配置 target p1/on10/decision30/day_gate）
  python scripts/auto_run_steps2to5.py --user-key 800080270733_4206673297219
  # 覆盖目标/阈值
  python scripts/auto_run_steps2to5.py --user-key 800080270733_4206673297219 --target-col p2 --on-thr 10 --decision-thr 30
  # 仅推理/仅训练
  python scripts/auto_run_steps2to5.py --user-key 800080270733_4206673297219 --stage infer
  # 强制重跑
  python scripts/auto_run_steps2to5.py --user-key 800080270733_4206673297219 --force

Windows PowerShell (test_gpu)：
  conda activate test_gpu
  python scripts/auto_run_steps2to5.py --user-key 800080270733_4206673297219

产物：outputs/<user>/train/<ts>/{metrics_by_split,chain,train_predictions,branch_sessions,train_window_index,_DONE}
      outputs/<user>/infer/<ts>/{inference_result,offline_metrics,metrics_daily_chain}
"""
from __future__ import annotations
import argparse, json, subprocess, sys, glob
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TF = ROOT / "configs/time_filters.json"
DEFAULT_BASE = ROOT / "configs/base_optimal.yaml"

def log(s, tag="INFO"):
    print(f"[{tag}] {s}")

def sh(cmd, **kw):
    log(" ".join(cmd) if isinstance(cmd, list) else cmd, "RUN")
    rc = subprocess.run(cmd, shell=False, **kw)
    if rc.returncode != 0:
        log(f"命令失败 exit={rc.returncode}: {' '.join(cmd) if isinstance(cmd,list) else cmd}", "FAIL")
        raise SystemExit(rc.returncode)
    return rc

def ensure_user_config(user_key: str, tf_path: Path, target_col: str, on_thr: float, decision_thr: float):
    """若 time_filters.json 缺该用户，自动补最小可用配置（Step1 模板），并备份原文件"""
    cfg = json.loads(tf_path.read_text(encoding="utf-8"))
    if user_key in cfg:
        log(f"time_filters.json 已含 {user_key} → 沿用（target={cfg[user_key].get('target_col')} on={cfg[user_key].get('on_thr_w')} dec={cfg[user_key].get('decision_thr_w')})")
        return False
    cfg[user_key] = {
        "target_col": target_col,
        "on_thr_w": on_thr,
        "decision_thr_w": decision_thr,
        "post_min_on": 1,
        "post_fill_short_off": 3,
        "quality": {"day_gate": True, "min_on_day_ratio": 0.2},
        "train": {"include": [["2025-07-10","2026-06-30"]]},
        "infer": {"include": [["2026-07-01","2026-07-31"]]}
    }
    bak = tf_path.with_suffix(".bak.auto")
    tf_path.rename(bak)
    tf_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"已为新用户 {user_key} 补 time_filters.json（target={target_col} on={on_thr} dec={decision_thr}），原文件备份 {bak.name}", "WARN")
    log("  → 请按 TUNING_GUIDE §1 核实 target_col 是否为真实 pN（错则似 OQ-13/800 PAUSED 全返工）", "WARN")
    return True

def step2_train(user_key, tf, base, data_root, out_root, force):
    cmd = [sys.executable, "scripts/run_batch_users.py",
           "--time-filter-config", str(tf),
           "--base-config", str(base),
           "--data-root", str(data_root),
           "--output-root", str(out_root),
           "--user-key", user_key, "--stage", "train"]
    if force: cmd.append("--force")
    sh(cmd)
    outs = sorted((out_root / user_key / "train").glob("*/_DONE")) if (out_root/user_key/"train").exists() else []
    if outs:
        log(f"Step2 训练 OK → {outs[-1].parent}  (共 {len(outs)} 个 _DONE)")
    else:
        log("Step2 警告：未找到 train/_DONE（可能 DATA_QUALITY_FAILED 门禁，查 data_quality_report.html）", "WARN")

def step3_window_check(user_key, out_root):
    import pandas
    pattern = str(out_root / user_key / "train" / "*" / "train_window_index.csv")
    files = sorted(glob.glob(pattern))
    if not files:
        log(f"Step3 跳过：未找到 {pattern}", "WARN")
        return
    f = files[-1]
    w = pandas.read_csv(f)
    s = pandas.to_datetime(w.win_end) - pandas.to_datetime(w.win_start)
    cross = int((s > pandas.Timedelta("23h45m")).sum())
    log(f"Step3 窗口自检 {f} → windows {len(w)} cross_gap {cross} max {s.max()} (期望 cross_gap 0, max 23.75h) {'✓' if cross==0 else '✗ W-1 污染'}")

def step5_infer(user_key, tf, base, data_root, out_root, force):
    cmd = [sys.executable, "scripts/run_batch_users.py",
           "--time-filter-config", str(tf),
           "--base-config", str(base),
           "--data-root", str(data_root),
           "--output-root", str(out_root),
           "--user-key", user_key, "--stage", "infer"]
    if force: cmd.append("--force")
    try:
        sh(cmd)
    except SystemExit as e:
        # 允许缺 infer 目录的空批次（batch.py v2026-09-18 已改为返回 DATA_MISSING 并写 batch_status.csv）
        batch_csvs = sorted((out_root / "batch").glob("*/batch_status.csv"), key=lambda p: p.stat().st_mtime)
        if batch_csvs:
            try:
                import pandas as _pd
                _df = _pd.read_csv(batch_csvs[-1])
                _has_missing = ((_df["user_key"] == user_key) & (_df["mode"] == "infer") & (_df["status"].str.contains("DATA_MISSING"))).any()
                if _has_missing:
                    log(f"Step5 跳过：data/infers/{user_key} 不存在（无推理数据，阈值 infer 链将跳过）— batch_status {batch_csvs[-1]}", "WARN")
                    return
            except Exception:
                pass
        raise
    outs = sorted((out_root / user_key / "infer").glob("*/_DONE")) if (out_root/user_key/"infer").exists() else []
    if outs:
        log(f"Step5 推理 OK → {outs[-1].parent}")
    else:
        log("Step5 未产出 infer/_DONE（可能无 infer 数据或被跳过，查 batch_status.csv）", "WARN")

def step4_threshold(user_key, out_root, pred_col_train="pred_transformer", state_col_train="pred_state_transformer"):
    pat_train = str(out_root / user_key / "train" / "*" / "predictions" / "train_predictions.csv")
    files = sorted(glob.glob(pat_train))
    if files:
        csv_train = files[-1]
        log(f"Step4 test链阈值扫描: {csv_train}")
        sh([sys.executable, "scripts/threshold_sweep.py",
            "--csv", csv_train,
            "--pred-col", pred_col_train, "--state-col", state_col_train,
            "--split", "test",
            "--thresholds", "10,30,50,100,150,200,300,400,500",
            "--min-on", "1", "--fill-off", "3"])
    else:
        log(f"Step4 test链跳过：未找到 {pat_train}", "WARN")
    pat_infer = str(out_root / user_key / "infer" / "*" / "predictions" / "inference_result.csv")
    files2 = sorted(glob.glob(pat_infer))
    if files2:
        csv_infer = files2[-1]
        log(f"Step4 infer链阈值扫描: {csv_infer}")
        sh([sys.executable, "scripts/threshold_sweep.py",
            "--csv", csv_infer,
            "--pred-col", "pred", "--state-col", "pred_state",
            "--thresholds", "10,30,50,100,150,200,300,400,500"])
    else:
        log(f"Step4 infer链跳过：未找到 {pat_infer}（先完成 Step5 推理）", "WARN")

def main(argv=None):
    ap = argparse.ArgumentParser(description="一键执行 TUNING_GUIDE Step2-5（ train → 窗口 → infer → 阈值）")
    ap.add_argument("--user-key", default="800080270733_4206673297219", help="用户键 <device>_<user>（默认新户733）")
    ap.add_argument("--time-filter-config", default=str(DEFAULT_TF))
    ap.add_argument("--base-config", default=str(DEFAULT_BASE))
    ap.add_argument("--data-root", default="data")
    ap.add_argument("--output-root", default="outputs")
    ap.add_argument("--target-col", default="p1", help="新用户自动补配置时的 target_col（默认 p1，需按 §1 核实）")
    ap.add_argument("--on-thr", type=float, default=10.0)
    ap.add_argument("--decision-thr", type=float, default=30.0)
    ap.add_argument("--stage", choices=["all","train","infer","threshold"], default="all", help="all=2+3+5+4 完整，threshold=仅 Step4")
    ap.add_argument("--force", action="store_true", help="--force 忽略 _DONE 重跑")
    ap.add_argument("--skip-train", action="store_true", help="跳过 Step2（已有训练）")
    ap.add_argument("--skip-infer", action="store_true", help="跳过 Step5")
    args = ap.parse_args(argv)

    user_key = args.user_key
    tf = Path(args.time_filter_config)
    base = Path(args.base_config)
    data_root = Path(args.data_root)
    out_root = Path(args.output_root)

    log(f"用户 {user_key}  Stage={args.stage}  TF={tf}  Base={base}  Data={data_root}  Out={out_root}")

    if args.stage in ("all","train"):
        ensure_user_config(user_key, tf, args.target_col, args.on_thr, args.decision_thr)

    if args.stage in ("all","train") and not args.skip_train:
        step2_train(user_key, tf, base, data_root, out_root, args.force)
    if args.stage in ("all","train"):
        try:
            step3_window_check(user_key, out_root)
        except Exception as e:
            log(f"Step3 异常: {e}", "WARN")
    if args.stage in ("all","infer") and not args.skip_infer:
        step5_infer(user_key, tf, base, data_root, out_root, args.force)
    if args.stage in ("all","threshold"):
        step4_threshold(user_key, out_root)

    log(f"一键完成 {user_key}  Step2-5 → 产出 outputs/{user_key}/{{train,infer}}/<ts>/  后续 Step6 审计见 TUNING_GUIDE §3")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
