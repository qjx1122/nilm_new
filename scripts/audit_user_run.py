#!/usr/bin/env python3
"""单用户 run 产物一键审计（train + infer 全链路，模式 B 交付前检查）。

对一个 output_root 下的用户产物树逐项审计并输出 ✓/✗ 清单（任何 ✗ 退出码 1）：

TRAIN（<run>/<user>/train/<ts>/）
  T1 metrics_by_split.csv   三段行；tp/fp/fn/tn 之和 == train_predictions 段行数（跨产物）
  T2 state_strategy_metrics.csv  同 scope 内 tp+fn 恒等（真值不变式）；
      raw_on_thr/all_days 行 == metrics_by_split 的 test 行（跨产物对账）
  T3 train_predictions.csv  行数=三段之和；pred_state_<model> 按文件自描述
      decision_thr_w 重放逐位一致（判决链复现校验）
  T4 metrics_daily_chain.csv 链口径日级（I.J 新增）：回归同能力口径、分类按
      target_state vs pred_state 逐日；Σ==train_predictions 总数；阈值列自描述

INFER（<run>/<user>/infer/<ts>/）
  I1 行数 == meta.json n_points          I2 列 == 契约 INFER_RESULT_COLUMNS（逐列）
  I3 pred 无 NaN；状态取值合法；阈值列/user_id 单值（口径自描述）
  I4 时间戳严格递增、15min 间隔统计、逐日行数、全关天清单
  I5 pred_state 判决链重放（decision_thr_w+min_on+fill_off）逐位一致
  I6 pred_prob ≈ state_probability(pred, decision_thr_w)（sigmoid 契约）
  I7 总混淆 + 全关日 fp + 逐日混淆（ts=1 恒等不变式校验）
  I8 期望值断言：--expect-n / --expect-confusion tp,fp,fn,tn / --expect-off-day-fp
  I9 offline_metrics.json 打印宏指标；--baseline-run 给另一 run 时逐键对照
      （模型能力口径应逐位一致 = 跨运行模型复现校验）
  I10 metrics_daily_chain.csv 链口径日级（I.J 审计 J1）：Σ==I7 总数；逐日==细明细行

用法（仓库根目录）：
    # 形式 A：--run-root 已是用户目录（含 train/infer），文档 Step 6 常用
    python scripts/audit_user_run.py --run-root outputs/800080252844_4206894986488 \
        --expect-n 2629 --expect-confusion 1036,77,21,1495 \
        --expect-off-day-fp 18 --baseline-run outputs/800080252844_4206894986488
    # 形式 B：--run-root 为父目录，需 --user-key
    python scripts/audit_user_run.py --run-root outputs --user-key 800080252844_4206894986488 \
        --expect-n 2629 --expect-confusion 1036,77,21,1495 \
        --expect-off-day-fp 18 --baseline-run outputs
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from nilm.common.contracts import INFER_RESULT_COLUMNS  # noqa: E402
from nilm.postprocess.state import postprocess_state, state_probability  # noqa: E402

FAILS: list[str] = []


def _ok(cond: bool, msg: str) -> None:
    print(f"  {'✓' if cond else '✗'} {msg}")
    if not cond:
        FAILS.append(msg)


def _latest(root: Path, stage: str) -> Path | None:
    dirs = sorted((root / stage).glob("*/")) if (root / stage).is_dir() else []
    return dirs[-1] if dirs else None


def _find_user_dir(run_root: Path, user_key: str | None) -> Path:
    # 兼容两种调用：① --run-root=outputs/<user>（用户目录本身，含 train/infer） ② --run-root=outputs（父目录）+ --user-key
    if (run_root / "train").is_dir() or (run_root / "infer").is_dir():
        if user_key and run_root.name != user_key:
            raise SystemExit(f"[audit] --run-root {run_root} 与 --user-key {user_key} 不一致（前者已是用户目录 {run_root.name}）")
        return run_root
    cands = [d for d in run_root.iterdir() if d.is_dir() and d.name != "batch"]
    if user_key:
        cands = [d for d in cands if d.name == user_key]
    if len(cands) != 1:
        raise SystemExit(f"[audit] 期望唯一用户目录（--user-key 可指定），实际: {[d.name for d in cands]}；若 --run-root 已是用户目录（如 outputs/<user>）请直接指向该目录或改用 --run-root outputs --user-key <user>")
    return cands[0]


def audit_train(tdir: Path | None, min_on: int, fill_off: int) -> None:
    print("== TRAIN ==")
    if tdir is None:
        _ok(False, "train 目录不存在");  return
    mbs = tdir / "metrics_by_split.csv"
    strat = tdir / "state_strategy_metrics.csv"
    tp_csv = tdir / "predictions" / "train_predictions.csv"
    _ok(mbs.exists() and strat.exists() and tp_csv.exists(), "三产物在位（metrics_by_split/state_strategy/train_predictions）")
    if not (mbs.exists() and strat.exists() and tp_csv.exists()):
        return
    preds = pd.read_csv(tp_csv)
    split_n = preds["split"].value_counts().to_dict()
    print(f"  · train_predictions: {len(preds)} 行，段计数 {split_n}")

    mdf = pd.read_csv(mbs)
    has_counts = all(c in mdf.columns for c in ("tp", "fp", "fn", "tn"))
    for _, r in mdf.iterrows():
        if has_counts:
            s = int(r["tp"] + r["fp"] + r["fn"] + r["tn"])
            _ok(s == split_n.get(r["split"]),
                f"metrics_by_split[{r['split']}] 计数和 {s} == 段行数 {split_n.get(r['split'])}")
        print(f"  · [{r['split']}] mae={r.get('mae')} f1={r.get('f1')}")

    sdf = pd.read_csv(strat)
    for scope, g in sdf.groupby("scope"):
        tfn = g["tp"] + g["fn"]
        _ok(tfn.nunique() == 1, f"state_strategy[{scope}] tp+fn 恒等 = {int(tfn.iloc[0])}（真值不变式）")
    raw = sdf[(sdf["strategy"] == "raw_on_thr") & (sdf["scope"] == "all_days")]
    test_row = mdf[mdf["split"] == "test"]
    if has_counts and len(raw) == 1 and len(test_row) == 1:
        match = all(int(raw.iloc[0][c]) == int(test_row.iloc[0][c]) for c in ("tp", "fp", "fn"))
        _ok(match, "state_strategy raw 行 == metrics_by_split test 行（跨产物对账）")
    for _, r in sdf.iterrows():
        print(f"  · strategy {r['strategy']}@{r['decision_thr_w']:g} [{r['scope']}]: "
              f"tp={r['tp']} fp={r['fp']} fn={r['fn']} f1={r['f1']}")

    dec = preds["decision_thr_w"].dropna().unique()
    _ok(len(dec) == 1, f"train_predictions decision_thr_w 单值 = {dec.tolist()}")
    if len(dec) == 1:
        d = float(dec[0])
        for col in [c for c in preds.columns if c.startswith("pred_state_")]:
            model = col[len("pred_state_"):]
            # 流水线按段分别计算 pred_state（user_task pred_frames[split]）——
            # 审计同口径：按 split 分块重放，段边界不跨块回填
            ok_all = True
            for _, g in preds.groupby("split", sort=False):
                replay = postprocess_state(g[f"pred_{model}"].to_numpy(float),
                                           d, min_on, fill_off)
                ok_all &= bool((replay.astype(int) == g[col].astype(int)).all())
            _ok(ok_all, f"train_predictions pred_state_{model} 判决链重放"
                        f"（按段，thr={d:g}）逐位一致")

    # T4 metrics_daily_chain.csv（链口径日级，I.J 新增）
    chain = tdir / "metrics_daily_chain.csv"
    _ok(chain.exists(), "metrics_daily_chain.csv 在位（链口径日级）")
    if chain.exists() and len(dec) == 1:
        try:
            cdf = pd.read_csv(chain)
            has_cols = all(c in cdf.columns for c in ("model", "split", "date", "n_points", "tp", "fp", "fn", "tn", "state_thr_w", "decision_thr_w", "post_min_on", "post_fill_short_off"))
            _ok(has_cols, "metrics_daily_chain 列完备（model/split/date/n_points/tp⋯/state_thr/decision_thr/post_*）")
            on_thr_pred = float(preds["on_thr_w"].iloc[0]) if "on_thr_w" in preds.columns else None
            _ok((cdf["decision_thr_w"] == float(dec[0])).all() and (on_thr_pred is None or (cdf["state_thr_w"] == on_thr_pred).all()) and (cdf["post_min_on"] == min_on).all() and (cdf["post_fill_short_off"] == fill_off).all(),
                f"metrics_daily_chain 阈值列自描述（state/dec/post）与文件一致（dec={float(dec[0]):g} min_on={min_on} fill={fill_off}）")
            for col in [c for c in preds.columns if c.startswith("pred_state_")]:
                model = col[len("pred_state_"):]
                g = cdf[cdf["model"] == model]
                if g.empty:
                    _ok(False, f"metrics_daily_chain 缺模型 {model}")
                    continue
                s_tp, s_fp, s_fn, s_tn = int(g["tp"].sum()), int(g["fp"].sum()), int(g["fn"].sum()), int(g["tn"].sum())
                t_on = preds["target_state"].to_numpy(dtype=int)
                p_on = preds[col].to_numpy(dtype=int)
                tp2 = int(((t_on == 1) & (p_on == 1)).sum())
                fp2 = int(((t_on == 0) & (p_on == 1)).sum())
                fn2 = int(((t_on == 1) & (p_on == 0)).sum())
                tn2 = int(((t_on == 0) & (p_on == 0)).sum())
                _ok([s_tp, s_fp, s_fn, s_tn] == [tp2, fp2, fn2, tn2],
                    f"metrics_daily_chain[{model}] Σ tp/fp/fn/tn {s_tp}/{s_fp}/{s_fn}/{s_tn} == train_predictions 总数")
                _ok(int(g["tp"].sum() + g["fn"].sum()) == int((preds["target_state"] == 1).sum()),
                    f"metrics_daily_chain[{model}] tp+fn 恒等 = {int((preds['target_state']==1).sum())}")
            mday = tdir / "metrics_daily.csv"
            if mday.exists():
                mdf_daily = pd.read_csv(mday)
                for _, r in cdf.iterrows():
                    match = mdf_daily[(mdf_daily["model"] == r["model"]) & (mdf_daily["split"] == r["split"]) & (mdf_daily["date"] == r["date"])]
                    if not match.empty:
                        _ok(int(match.iloc[0]["n_points"]) == int(r["n_points"]),
                            f"metrics_daily_chain[{r['model']}/{r['split']}/{r['date']}] n_points {int(r['n_points'])} == metrics_daily")
        except Exception as e:  # noqa: BLE001
            _ok(False, f"metrics_daily_chain 校验异常: {e}")


def audit_infer(idir: Path | None, min_on: int, fill_off: int, args) -> None:
    print("== INFER ==")
    if idir is None:
        _ok(False, "infer 目录不存在");  return
    csv = idir / "predictions" / "inference_result.csv"
    _ok(csv.exists(), "inference_result.csv 在位")
    if not csv.exists():
        return
    df = pd.read_csv(csv)
    meta = json.loads((idir / "meta.json").read_text(encoding="utf-8")) \
        if (idir / "meta.json").exists() else {}
    n = len(df)
    _ok(n == meta.get("n_points", n), f"行数 {n} == meta.n_points {meta.get('n_points')}")
    if args.expect_n is not None:
        _ok(n == args.expect_n, f"行数 == 期望 {args.expect_n}")

    _ok(list(df.columns) == INFER_RESULT_COLUMNS,
        f"列契约逐列一致（{len(INFER_RESULT_COLUMNS)} 列）")

    _ok(df["pred"].notna().all(), "pred 无 NaN")
    _ok(df["pred_state"].isin([0, 1]).all(), "pred_state ∈ {{0,1}}")
    _ok(df["target_state"].dropna().isin([0, 1]).all(), "target_state ∈ {{0,1,空}}")
    for col in ("on_thr_w", "decision_thr_w", "user_id"):
        u = df[col].unique().tolist()
        _ok(len(u) == 1, f"{col} 单值 = {u}")
    on_thr = float(df["on_thr_w"].iloc[0])
    dec = float(df["decision_thr_w"].iloc[0])

    ts = pd.to_datetime(df["timestamp"])
    _ok(ts.is_monotonic_increasing, "时间戳严格递增")
    dt = ts.diff().dropna()
    n_gap = int((dt != pd.Timedelta(minutes=15)).sum())
    print(f"  · 间隔 15min 外的断点 {n_gap} 处（跨天/缺数），跨度 {ts.iloc[0]} ~ {ts.iloc[-1]}")

    day = ts.dt.strftime("%Y-%m-%d")
    per_day = day.value_counts().sort_index()
    print(f"  · {per_day.size} 天，逐日行数 {per_day.to_dict()}")
    df["_day"] = day
    day_max = df.groupby("_day")["target_state"].max()
    off_days = sorted(day_max[day_max == 0].index.tolist())
    print(f"  · 全关天 {len(off_days)}：{', '.join(off_days)}")

    replay = postprocess_state(df["pred"].to_numpy(float), dec, min_on, fill_off)
    _ok(bool((replay.astype(int) == df["pred_state"].astype(int)).all()),
        f"pred_state 判决链重放（thr={dec:g}, min_on={min_on}, fill_off={fill_off}）逐位一致")
    prob = state_probability(df["pred"].to_numpy(float), dec)
    _ok(bool(abs(prob - df["pred_prob"].to_numpy(float)).max() < 1e-5),
        "pred_prob ≈ sigmoid(pred|decision_thr_w) 契约（tol 1e-5）")

    m = df["target_state"].notna()
    tsv = df.loc[m, "target_state"].astype(int).to_numpy()
    psv = df.loc[m, "pred_state"].astype(int).to_numpy()
    tp = int(((tsv == 1) & (psv == 1)).sum())
    fp = int(((tsv == 0) & (psv == 1)).sum())
    fn = int(((tsv == 1) & (psv == 0)).sum())
    tn = int(((tsv == 0) & (psv == 0)).sum())
    off_mask = df.loc[m, "_day"].isin(off_days).to_numpy()
    off_fp = int(((tsv == 0) & (psv == 1) & off_mask).sum())
    print(f"  · 总混淆: tp={tp} fp={fp} fn={fn} tn={tn}（Σ={tp+fp+fn+tn}，"
          f"tp+fn={tp+fn}=ts1 恒等）；全关日 fp={off_fp}")
    if args.expect_confusion:
        e = [int(x) for x in args.expect_confusion.split(",")]
        _ok([tp, fp, fn, tn] == e, f"总混淆 == 期望 {e}")
    if args.expect_off_day_fp is not None:
        _ok(off_fp == args.expect_off_day_fp, f"全关日 fp == 期望 {args.expect_off_day_fp}")
    for d, g in df[m].groupby("_day"):
        gtp = int(((g.target_state == 1) & (g.pred_state == 1)).sum())
        gfp = int(((g.target_state == 0) & (g.pred_state == 1)).sum())
        gfn = int(((g.target_state == 1) & (g.pred_state == 0)).sum())
        print(f"    {d} tp={gtp} fp={gfp} fn={gfn}")

    # I10 metrics_daily_chain.csv（链口径日级，I.J 审计 J1；有真值时必在）
    chain = idir / "metrics_daily_chain.csv"
    has_target = int(m.sum()) > 0
    if has_target:
        _ok(chain.exists(), "metrics_daily_chain.csv 在位（链口径日级）")
    else:
        # 无真值时链口径日级无定义，文件可选
        if not chain.exists():
            print("  · 无真值，metrics_daily_chain.csv 可选（未生成）")
    if chain.exists():
        try:
            cdf = pd.read_csv(chain)
            has_c = all(c in cdf.columns for c in ("model", "date", "n_points", "tp", "fp", "fn", "tn", "state_thr_w", "decision_thr_w", "post_min_on", "post_fill_short_off"))
            _ok(has_c, "metrics_daily_chain 列完备（model/date/n_points/tp⋯/state_thr/decision_thr/post_*）")
            _ok((cdf["decision_thr_w"] == dec).all() and (cdf["state_thr_w"] == on_thr).all() and (cdf["post_min_on"] == min_on).all() and (cdf["post_fill_short_off"] == fill_off).all(),
                f"metrics_daily_chain 阈值列自描述 decision={dec:g} state={on_thr:g} post {min_on}/{fill_off}")
            _ok(int(cdf["tp"].sum()) == tp and int(cdf["fp"].sum()) == fp and int(cdf["fn"].sum()) == fn and int(cdf["tn"].sum()) == tn,
                f"metrics_daily_chain Σ tp/fp/fn/tn {int(cdf['tp'].sum())}/{int(cdf['fp'].sum())}/{int(cdf['fn'].sum())}/{int(cdf['tn'].sum())} == inference_result 总数")
            _ok(int((cdf["tp"] + cdf["fn"]).sum()) == int((df.loc[m, "target_state"] == 1).sum()),
                f"metrics_daily_chain tp+fn 恒等 = {int((df.loc[m, 'target_state']==1).sum())}")
            # 逐日对照（链口径）
            for _, r in cdf.iterrows():
                d = r["date"]
                # df 此时仍含 _day 列（未 drop）
                g = df[df["_day"] == d] if "_day" in df.columns else df[pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m-%d") == d]
                gm = g[g["target_state"].notna()]
                if len(gm) == 0:
                    continue
                gtp = int(((gm["target_state"] == 1) & (gm["pred_state"] == 1)).sum())
                gfp = int(((gm["target_state"] == 0) & (gm["pred_state"] == 1)).sum())
                gfn = int(((gm["target_state"] == 1) & (gm["pred_state"] == 0)).sum())
                _ok(int(r["tp"]) == gtp and int(r["fp"]) == gfp and int(r["fn"]) == gfn,
                    f"metrics_daily_chain[{d}] tp/fp/fn {int(r['tp'])}/{int(r['fp'])}/{int(r['fn'])} == 明细逐日")
            mday = idir / "metrics_daily.csv"
            if mday.exists():
                mdf_daily = pd.read_csv(mday)
                for _, r in cdf.iterrows():
                    match = mdf_daily[mdf_daily["date"] == r["date"]]
                    if not match.empty:
                        _ok(int(match.iloc[0]["n_points"]) == int(r["n_points"]),
                            f"metrics_daily_chain[{r['date']}] n_points {int(r['n_points'])} == metrics_daily")
        except Exception as e:  # noqa: BLE001
            _ok(False, f"metrics_daily_chain 校验异常: {e}")
    df.drop(columns=["_day"], inplace=True)

    om = idir / "offline_metrics.json"
    if om.exists():
        off = json.loads(om.read_text(encoding="utf-8"))
        macros = {k: (v.get("macro") if isinstance(v, dict) else v) for k, v in off.items()}
        print(f"  · offline_metrics（模型能力口径）: {macros}")
        if args.baseline_run:
            bdir = _find_user_dir(Path(args.baseline_run), args.user_key)
            b = _latest(bdir, "infer")
            bom = (b / "offline_metrics.json") if b else None
            if bom and bom.exists():
                boff = json.loads(bom.read_text(encoding="utf-8"))
                bmac = {k: (v.get("macro") if isinstance(v, dict) else v) for k, v in boff.items()}
                diff = {k: (bmac[k], macros[k]) for k in bmac
                        if k in macros and bmac[k] != macros[k]}
                _ok(not diff, f"offline_metrics 与 baseline 逐键一致（模型逐位复现）"
                              f"{'; 差异: ' + str(diff) if diff else ''}")
            else:
                _ok(False, f"baseline offline_metrics.json 未找到（{bom}）")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="单用户 run 产物一键审计")
    ap.add_argument("--run-root", required=True, help="输出根目录：可为用户目录本身（如 outputs/<user> 含 train/infer）或父目录（如 outputs 需配合 --user-key）")
    ap.add_argument("--user-key", default=None, help="用户目录名（单用户时可省略）")
    ap.add_argument("--min-on", type=int, default=1)
    ap.add_argument("--fill-off", type=int, default=3)
    ap.add_argument("--expect-n", type=int, default=None, help="期望 inference 行数")
    ap.add_argument("--expect-confusion", default=None, help="期望总混淆 tp,fp,fn,tn")
    ap.add_argument("--expect-off-day-fp", type=int, default=None)
    ap.add_argument("--baseline-run", default=None, help="对照 run 根目录（offline 逐键比对）；语义同 --run-root，支持用户目录或父目录+--user-key")
    args = ap.parse_args(argv)

    FAILS.clear()
    udir = _find_user_dir(Path(args.run_root), args.user_key)
    print(f"审计: {args.run_root} / {udir.name}")
    audit_train(_latest(udir, "train"), args.min_on, args.fill_off)
    audit_infer(_latest(udir, "infer"), args.min_on, args.fill_off, args)
    n_fail = len(FAILS)
    print(f"\n== 审计结果: {'全部通过 ✅' if n_fail == 0 else f'{n_fail} 项未过 ❌'} ==")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
