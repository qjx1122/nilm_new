#!/usr/bin/env python3
"""
生成 0800 B1 重切配置（34-38天 29%关 + 推理 07-11~08-02）

逻辑：
- 读取 0800 分路原始 CSV（data/trains/800080270800_4200904302272/ + data/infers 同用户），
  按 on_thr_w=50 判日开/关，统计 05-21~06-29 的 23 个全关天与 07-01~07-10 的 10 个全开天
- 若 data/ 不可用（沙盒无数据），退化为按文档记载的 23 关清单（05-26/06-11/06-12/06-25 等）近似
- 目标：训练池 05-21~07-10 中剔 12 个全关天（最旧 12 天），使训练池 27开11关=38天 28.9%关，对齐推理 6关22.2%（差 6.7pct，原差 35.3pct）
- 输出 configs/time_filters_0800_B1.json（仅含 0800 键，其余键保持原样）并回显

用法：
  python scripts/make_B1_config.py --time-filter-config configs/time_filters.json --output configs/time_filters_0800_B1.json
  # 生成后用 --time-filter-config configs/time_filters_0800_B1.json 跑 run_batch_users
"""
from __future__ import annotations
import argparse
import json
import pathlib
import sys

# 文档记载的 05-21~06-29 40天中 23 关天清单（按 F1=0 与手锚推断，近似）
# 若有真实 data，则以真实为准；否则用此兜底
FALLBACK_OFF_DAYS_40 = [
    "2026-05-26", "2026-05-27", "2026-05-28", "2026-05-29", "2026-05-30",
    "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05", "2026-06-06",
    "2026-06-11", "2026-06-12", "2026-06-13", "2026-06-14", "2026-06-15",
    "2026-06-16", "2026-06-20", "2026-06-25", "2026-06-26", "2026-06-27",
    "2026-06-28", "2026-06-29", "2026-05-31",  # 23
]
# 按时间排序，最旧 12 天为剔除候选
FALLBACK_DROP_12 = sorted(FALLBACK_OFF_DAYS_40)[:12]


def list_off_days_via_data(user_key: str, train_root: str, on_thr_w: float) -> list[str] | None:
    """尝试从 data/trains/<user_key>/ 的分路 CSV 直接判日开/关"""
    try:
        import pandas as pd
        from nilm.data_io.csv_source import CsvBranchLoader
        from nilm.analysis.branch_sessions import analyze_branch_sessions
        from nilm.common.timefilter import parse_intervals, include_mask
        import glob
        # 扫描该用户 trains 目录
        base = pathlib.Path(train_root) / user_key
        if not base.is_dir():
            return None
        branch_files = list(base.glob("*.csv"))
        # 过滤出分路文件（RE_BR）
        from nilm.common.contracts import RE_BR
        branch_files = [str(p) for p in branch_files if RE_BR.match(p.name)]
        if not branch_files:
            return None
        loader = CsvBranchLoader()
        branch_raw, _ = loader.load(branch_files, sentinel_values=[-2147483648, 2147483647])
        # 清洗前直接判关：按原始 15min 点日峰值 < on_thr_w
        # 简易：按 time 列日峰值
        branch_raw["date"] = pd.to_datetime(branch_raw["time"]).dt.normalize()
        # 目标列 p1（0800 固定 p1）
        target_col = "p1"
        if target_col not in branch_raw.columns:
            # 尝试小写
            target_col = [c for c in branch_raw.columns if c.lower() == "p1"]
            target_col = target_col[0] if target_col else None
        if not target_col:
            return None
        daily_max = branch_raw.groupby("date")[target_col].max()
        off_days = daily_max[daily_max < float(on_thr_w)].index.strftime("%Y-%m-%d").tolist()
        # 仅取 05-21~06-29 段
        off_days = [d for d in off_days if "2026-05-21" <= d <= "2026-06-29"]
        return sorted(off_days)
    except Exception as e:
        print(f"[warn] 无法从 data 判关，退化到 fallback：{e}", file=sys.stderr)
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--time-filter-config", default="configs/time_filters.json")
    ap.add_argument("--output", default="configs/time_filters_0800_B1.json")
    ap.add_argument("--on-thr-w", type=float, default=50.0)
    ap.add_argument("--drop-n", type=int, default=12, help="剔除全关天数（默认 12，使 23→11，关占比 57%→29%）")
    ap.add_argument("--train-root", default="data/trains")
    args = ap.parse_args()

    cfg_path = pathlib.Path(args.time_filter_config)
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    user_key = "800080270800_4200904302272"

    # 1) 确定要剔的 12 个全关天
    real_off = list_off_days_via_data(user_key, args.train_root, args.on_thr_w)
    if real_off is not None and len(real_off) >= args.drop_n:
        drop_days = sorted(real_off)[: args.drop_n]
        print(f"[info] 从真实 data 判得 05-21~06-29 全关天 {len(real_off)} 天，剔最旧 {args.drop_n} 天：{drop_days}")
    else:
        drop_days = FALLBACK_DROP_12[: args.drop_n]
        print(f"[info] 使用 fallback 全关天清单 {len(FALLBACK_OFF_DAYS_40)} 天，剔最旧 {args.drop_n} 天：{drop_days}（如有 data 请重跑以校准）")

    # 2) 构造 B1 配置（在原 cfg 基础上覆盖 0800 键）
    # 复制原 0800 块作为基底
    base_0800 = cfg.get(user_key, {})
    b1_0800 = json.loads(json.dumps(base_0800))  # deep copy
    # 训练池 05-21~07-10，排除 12 关天
    b1_0800["train"] = {
        "include": [["2026-05-21", "2026-07-10"]],
        "exclude": [[d, d] for d in drop_days],
    }
    # 推理 07-11~08-02，排除 07-23~26（保持原排除）
    b1_0800["infer"] = {
        "include": [["2026-07-11", "2026-08-02"]],
        "exclude": [["2026-07-23", "2026-07-26"]],
    }
    # splits 清空，走自动 stratified_day（使关占比 29% 均匀分布到 train/val/test）
    # 若要保持手锚，可保留原 splits；此处清空以体现“池级”治理，层内由自动分层保证
    b1_0800["splits"] = {"train": {"include": []}, "val": {"include": []}, "test": {"include": []}}
    b1_0800["_note_B1"] = f"2026-09-17 B1：训练 05-21~07-10 剔 {args.drop_n} 关（23→{23-args.drop_n}，关占比 57%→{round((23-args.drop_n)/(40+10-args.drop_n)*100,1)}%），推理 07-11~08-02 6关22.2%（差 {(23-args.drop_n)/(40+10-args.drop_n)*100-22.2:.1f}pct，原差35.3pct），零泄漏 07-10<07-11；splits走自动分层（池级治理），drop_days={drop_days}"
    b1_0800["_note_drop_days"] = drop_days

    # 输出新文件：仅替换 0800 键，其余原样
    out_cfg = dict(cfg)
    out_cfg[user_key] = b1_0800
    out_path = pathlib.Path(args.output)
    out_path.write_text(json.dumps(out_cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] 已生成 {out_path}（训练 05-21~07-10 剔 {args.drop_n} 关，推理 07-11~08-02）")
    # 回显关键段
    print(json.dumps({user_key: b1_0800}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
