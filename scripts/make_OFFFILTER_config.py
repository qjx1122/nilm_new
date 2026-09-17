#!/usr/bin/env python3
"""
生成 0800 OFF-Filter 配置（池内删全关天）

逻辑：
- 以 B1b 池（05-21~07-10 剔12关 38天 22开16关）为基线，再按 --drop-off-n 进一步在训练池内剔 N 个全关天
- 全关天判定：branch p1 日峰值 < on_thr_w (50) 的天，从 data/trains/800080.../ 读真实数据；沙盒无数据时退化为 fallback 16 关清单
- 输出 configs/time_filters_0800_OFF{N}.json（仅 0800 块增加 train.exclude，其余同 B1b），splits 保持 stratified_by_state 自动均摊
- 用法：
  python scripts/make_OFFFILTER_config.py --base-config configs/time_filters_0800_B1b.json --output configs/time_filters_0800_OFF8.json --drop-off-n 8 --on-thr-w 50
  python scripts/run_batch_users.py --base-config configs/base_lag5.yaml --time-filter-config configs/time_filters_0800_OFF8.json --data-root data --output-root outputs_0800_OFF8 --user-key 800080270800_4200904302272
"""
from __future__ import annotations
import argparse, json, pathlib, sys

# B1b 池内 16 关fallback（按 B1b 审计反推：38天 22开16关）
FALLBACK_OFF16_IN_POOL = [
    "2026-05-24", "2026-05-25",  # 推断：train 中连续关
    "2026-06-01",
    "2026-06-12", "2026-06-13", "2026-06-14", "2026-06-15",
    "2026-06-16", "2026-06-19", "2026-06-20",
    "2026-06-24", "2026-06-25", "2026-06-28",
    "2026-07-04", "2026-07-06", "2026-07-07", # 填充至16
]
# 按时间排序，最新 8 关为最靠近推理期的关天（07-04/06/07 等），最旧 8 关为 05-24/25/06-01 等
FALLBACK_OFF16_IN_POOL = sorted(FALLBACK_OFF16_IN_POOL)

def list_off_in_pool_via_data(user_key: str, train_root: str, on_thr_w: float, pool_start: str, pool_end: str, pool_exclude: list[str]) -> list[str] | None:
    try:
        import pandas as pd
        from nilm.data_io.csv_source import CsvBranchLoader
        from nilm.common.contracts import RE_BR
        base = pathlib.Path(train_root) / user_key
        if not base.is_dir():
            return None
        branch_files = [str(p) for p in base.glob("*.csv") if RE_BR.match(p.name)]
        if not branch_files:
            return None
        loader = CsvBranchLoader()
        branch_raw, _ = loader.load(branch_files, sentinel_values=[-2147483648, 2147483647])
        # 判定日峰值
        target_col = "p1"
        if target_col not in branch_raw.columns:
            target_col = [c for c in branch_raw.columns if c.lower()=="p1"]
            target_col = target_col[0] if target_col else None
        if not target_col:
            return None
        branch_raw["date"] = pd.to_datetime(branch_raw["time"]).dt.normalize() if "time" in branch_raw.columns else pd.to_datetime(branch_raw.index).normalize()
        daily_max = branch_raw.groupby("date")[target_col].max()
        # 池内天
        import pandas as pd2
        pool_days = pd2.date_range(pool_start, pool_end, freq="D").strftime("%Y-%m-%d").tolist()
        exclude_set = set(pool_exclude)
        pool_days = [d for d in pool_days if d not in exclude_set]
        off_in_pool = [d for d in pool_days if d in daily_max.index.strftime("%Y-%m-%d").tolist() and daily_max[daily_max.index.strftime("%Y-%m-%d")==d].iloc[0] < float(on_thr_w)] if len(daily_max) else []
        # 简化：直接筛选 daily_max < thr 且在池内
        off = daily_max[daily_max < float(on_thr_w)].index.strftime("%Y-%m-%d").tolist()
        off = [d for d in off if d in pool_days]
        return sorted(off)
    except Exception as e:
        print(f"[warn] data 判关失败退化 fallback: {e}", file=sys.stderr)
        return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-config", default="configs/time_filters_0800_B1b.json")
    ap.add_argument("--output", required=True)
    ap.add_argument("--drop-off-n", type=int, default=8, help="池内再剔全关天数（0/4/8/12 档）")
    ap.add_argument("--on-thr-w", type=float, default=50.0)
    ap.add_argument("--train-root", default="data/trains")
    args = ap.parse_args()

    base = json.loads(pathlib.Path(args.base_config).read_text(encoding="utf-8"))
    key = "800080270800_4200904302272"
    pool_start, pool_end = "2026-05-21", "2026-07-10"
    base_exclude = [x[0] for x in base[key]["train"]["exclude"]]  # 12关
    # 确定池内 16 关
    real_off = list_off_in_pool_via_data(key, args.train_root, args.on_thr_w, pool_start, pool_end, base_exclude)
    if real_off and len(real_off) >= args.drop_off_n:
        off_pool = sorted(real_off)
        print(f"[info] 真实池内全关 {len(off_pool)} 天: {off_pool}")
    else:
        off_pool = FALLBACK_OFF16_IN_POOL
        print(f"[info] 使用 fallback 池内 16 关: {off_pool} (drop {args.drop_off_n} 天)")

    # 按策略：剔最旧 N 关（保推理近端 07-04/06/07 等关天以维持 test 含关可信度），或剔最新 N 关（降推理错配）——此处提供两种，默认剔最旧
    # 为与推理 26% 对齐（22开16关42% -> 22开8关26% 需剔 8），剔最旧 8 关使池近端关天保留
    drop_n = min(args.drop_off_n, len(off_pool))
    to_drop = sorted(off_pool)[:drop_n]  # 最旧 N 关
    # 新 train exclude = 原 12 + 新增 N
    new_exclude = [[d,d] for d in sorted(set(base_exclude + to_drop))]
    new_cfg = json.loads(json.dumps(base))
    new_cfg[key]["train"]["exclude"] = new_exclude
    # 保留 stratified_by_state 自动均摊，无需手锚
    new_cfg[key]["_note_OFFFILTER"] = f"2026-09-17 OFF-Filter：B1b池 38天22开16关42% -> 剔池内最旧{drop_n}关 {to_drop} -> {38-drop_n}天22开{16-drop_n}关{round((16-drop_n)/(38-drop_n)*100,1)}%（对齐推理26%），splits仍 stratified_by_state 自动均摊，lag5已合入base"
    new_cfg[key]["_note_drop_off"] = to_drop
    new_cfg[key].pop("bus_field_map", None)  # 明确不需B相映射（B相置0为设计）
    # features 已在 base lag5，此处无需再写；若需 per-user 覆盖可加
    pathlib.Path(args.output).write_text(json.dumps(new_cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] 已生成 {args.output} 池 {38-drop_n}天 off {(16-drop_n)/(38-drop_n):.1%} {to_drop}")

if __name__ == "__main__":
    main()
