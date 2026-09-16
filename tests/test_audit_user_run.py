"""audit_user_run（run 产物一键审计）——合成 run 树的通过/捕获/期望断言三测。

覆盖：T1/T2/T3 train 侧跨产物对账、I1-I7 infer 侧契约（列/重放/pred_prob/
混淆/全关日）、I9 baseline 逐键对照、I8 期望值断言；损坏注入必须被抓住。
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from nilm.postprocess.state import postprocess_state, state_probability
from scripts.audit_user_run import main

USER = "800080252844_4206894986488"
DAY1_PRED = [5, 15, 25, 35, 45, 5, 5, 5, 5, 5, 5, 5]          # 全关天：@10 链 fp=4
DAY2_PRED = [8, 9, 100, 100, 3, 4, 100, 100, 60, 40, 12, 13]  # 开机日：tp=8 fp=2


def _infer_frame() -> pd.DataFrame:
    frames = []
    for day, (pred, tgt) in {"2026-01-01": (DAY1_PRED, [0] * 12),
                             "2026-01-02": (DAY2_PRED, [0, 0] + [1] * 8 + [0, 0])}.items():
        frames.append(pd.DataFrame({
            "timestamp": pd.date_range(f"{day} 00:00:00", periods=12, freq="15min")
                           .strftime("%Y-%m-%d %H:%M:%S"),
            "user_id": USER, "target": [t * 100.0 for t in tgt],
            "target_state": tgt, "on_thr_w": 10.0, "pred": pred,
            "pred_state": 0, "decision_thr_w": 10.0, "pred_prob": 0.0}))
    df = pd.concat(frames, ignore_index=True)
    df["pred_state"] = postprocess_state(df["pred"].to_numpy(float), 10.0, 1, 3).astype(int)
    df["pred_prob"] = np.round(state_probability(df["pred"].to_numpy(float), 10.0), 6)
    return df


def _train_frames() -> dict[str, pd.DataFrame]:
    """train/val 各 12 行（DAY1/DAY2 模式），test 24 行（两日拼接）。"""
    out = {}
    for split, preds in {"train": [DAY1_PRED], "val": [DAY2_PRED],
                         "test": [DAY1_PRED, DAY2_PRED]}.items():
        parts = []
        for i, p in enumerate(preds):
            day = {"train": "2025-12-01", "val": "2025-12-02",
                   "test": ["2026-01-01", "2026-01-02"][i]}[split]
            tgt = [0] * 12 if p is DAY1_PRED else [0, 0] + [1] * 8 + [0, 0]
            parts.append(pd.DataFrame({
                "timestamp": pd.date_range(f"{day} 00:00:00", periods=12, freq="15min")
                               .strftime("%Y-%m-%d %H:%M:%S"),
                "split": split, "target": [t * 100.0 for t in tgt],
                "target_state": tgt, "on_thr_w": 10.0, "decision_thr_w": 10.0,
                "pred_transformer": p}))
        df = pd.concat(parts, ignore_index=True)
        df["pred_state_transformer"] = postprocess_state(
            df["pred_transformer"].to_numpy(float), 10.0, 1, 3).astype(int)
        out[split] = df
    return out


def _make_run(root: Path, infer_df: pd.DataFrame | None = None) -> Path:
    udir = root / USER
    tdir = udir / "train" / "20260101_000000"
    idir = udir / "infer" / "20260101_000001"
    tdir.mkdir(parents=True), idir.mkdir(parents=True)

    tr = _train_frames()
    (tdir / "predictions").mkdir(parents=True, exist_ok=True)
    preds_df = pd.concat(tr.values(), ignore_index=True)
    preds_df.to_csv(tdir / "predictions" / "train_predictions.csv", index=False)
    # metrics_by_split：test 行 = (8,6,0,10)（与 infer 混淆/state_strategy raw 一致）
    pd.DataFrame([
        {"model": "transformer", "split": "train", "mae": 1.0, "f1": 0.9, "tp": 5, "fp": 1, "fn": 0, "tn": 6},
        {"model": "transformer", "split": "val", "mae": 2.0, "f1": 0.8, "tp": 4, "fp": 0, "fn": 1, "tn": 7},
        {"model": "transformer", "split": "test", "mae": 3.0, "f1": 0.7273, "tp": 8, "fp": 6, "fn": 0, "tn": 10},
    ]).to_csv(tdir / "metrics_by_split.csv", index=False)
    pd.DataFrame([
        {"model": "transformer", "strategy": "raw_on_thr", "scope": "all_days",
         "decision_thr_w": 10.0, "post_min_on": 0, "post_fill_short_off": 0,
         "f1": 0.7273, "precision": 0.5714, "recall": 1.0, "tp": 8, "fp": 6, "fn": 0},
        {"model": "transformer", "strategy": "decision+runs", "scope": "all_days",
         "decision_thr_w": 10.0, "post_min_on": 1, "post_fill_short_off": 3,
         "f1": 0.7273, "precision": 0.5714, "recall": 1.0, "tp": 8, "fp": 6, "fn": 0},
    ]).to_csv(tdir / "state_strategy_metrics.csv", index=False)
    # metrics_daily.csv + metrics_daily_chain.csv（I.J 链口径日级）
    preds_df["date"] = pd.to_datetime(preds_df["timestamp"]).dt.strftime("%Y-%m-%d")
    daily_rows, chain_rows = [], []
    for (split, date), g in preds_df.groupby(["split", "date"]):
        t = g["target_state"].to_numpy(dtype=int)
        p_raw = (g["pred_transformer"].to_numpy(float) >= 10.0).astype(int)
        p_chain = g["pred_state_transformer"].to_numpy(dtype=int)
        mae = float(np.mean(np.abs(g["target"].to_numpy(float) - g["pred_transformer"].to_numpy(float))))
        def _f1(tp, fp, fn):
            prec = tp / (tp + fp) if (tp + fp) > 0 else (1.0 if fn == 0 else 0.0)
            rec = tp / (tp + fn) if (tp + fn) > 0 else 1.0
            return 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
        tp_r, fp_r, fn_r, tn_r = int(((t == 1) & (p_raw == 1)).sum()), int(((t == 0) & (p_raw == 1)).sum()), int(((t == 1) & (p_raw == 0)).sum()), int(((t == 0) & (p_raw == 0)).sum())
        tp_c, fp_c, fn_c, tn_c = int(((t == 1) & (p_chain == 1)).sum()), int(((t == 0) & (p_chain == 1)).sum()), int(((t == 1) & (p_chain == 0)).sum()), int(((t == 0) & (p_chain == 0)).sum())
        daily_rows.append({"model": "transformer", "split": split, "date": date, "n_points": len(g), "mae": mae, "f1": _f1(tp_r, fp_r, fn_r), "tp": tp_r, "fp": fp_r, "fn": fn_r, "tn": tn_r, "state_thr_w": 10.0})
        chain_rows.append({"model": "transformer", "split": split, "date": date, "n_points": len(g), "mae": mae, "f1": _f1(tp_c, fp_c, fn_c), "tp": tp_c, "fp": fp_c, "fn": fn_c, "tn": tn_c, "state_thr_w": 10.0, "decision_thr_w": 10.0, "post_min_on": 1, "post_fill_short_off": 3})
    pd.DataFrame(daily_rows).to_csv(tdir / "metrics_daily.csv", index=False)
    pd.DataFrame(chain_rows).to_csv(tdir / "metrics_daily_chain.csv", index=False)

    df = infer_df if infer_df is not None else _infer_frame()
    (idir / "predictions").mkdir(parents=True, exist_ok=True)
    df.to_csv(idir / "predictions" / "inference_result.csv", index=False)
    (idir / "meta.json").write_text(json.dumps({"n_points": len(df)}), encoding="utf-8")
    (idir / "offline_metrics.json").write_text(
        json.dumps({"f1": {"macro": 0.7273}, "mae": {"macro": 3.0}}), encoding="utf-8")
    # infer 侧日级（能力 + 链口径，I.J）
    df["_date"] = pd.to_datetime(df["timestamp"]).dt.strftime("%Y-%m-%d")
    i_daily, i_chain = [], []
    for d, g in df.groupby("_date"):
        t = g["target_state"].to_numpy(dtype=int)
        p_raw = (g["pred"].to_numpy(float) >= 10.0).astype(int)
        p_c = g["pred_state"].to_numpy(dtype=int)
        mae = float(np.mean(np.abs(g["target"].to_numpy(float) - g["pred"].to_numpy(float))))
        def _f1i(tp, fp, fn):
            prec = tp / (tp + fp) if (tp + fp) > 0 else (1.0 if fn == 0 else 0.0)
            rec = tp / (tp + fn) if (tp + fn) > 0 else 1.0
            return 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
        tp_r, fp_r, fn_r, tn_r = int(((t == 1) & (p_raw == 1)).sum()), int(((t == 0) & (p_raw == 1)).sum()), int(((t == 1) & (p_raw == 0)).sum()), int(((t == 0) & (p_raw == 0)).sum())
        tp_c, fp_c, fn_c, tn_c = int(((t == 1) & (p_c == 1)).sum()), int(((t == 0) & (p_c == 1)).sum()), int(((t == 1) & (p_c == 0)).sum()), int(((t == 0) & (p_c == 0)).sum())
        i_daily.append({"model": "transformer", "date": d, "n_points": len(g), "mae": mae, "f1": _f1i(tp_r, fp_r, fn_r), "tp": tp_r, "fp": fp_r, "fn": fn_r, "tn": tn_r, "state_thr_w": 10.0})
        i_chain.append({"model": "transformer", "date": d, "n_points": len(g), "mae": mae, "f1": _f1i(tp_c, fp_c, fn_c), "tp": tp_c, "fp": fp_c, "fn": fn_c, "tn": tn_c, "state_thr_w": 10.0, "decision_thr_w": 10.0, "post_min_on": 1, "post_fill_short_off": 3})
    pd.DataFrame(i_daily).to_csv(idir / "metrics_daily.csv", index=False)
    pd.DataFrame(i_chain).to_csv(idir / "metrics_daily_chain.csv", index=False)
    df.drop(columns=["_date"], inplace=True)
    return udir


def test_audit_pass(tmp_path, capsys):
    _make_run(tmp_path / "run")
    _make_run(tmp_path / "baseline")            # offline 同值 → 逐键对照通过
    rc = main(["--run-root", str(tmp_path / "run"), "--expect-n", "24",
               "--expect-confusion", "8,6,0,10", "--expect-off-day-fp", "4",
               "--baseline-run", str(tmp_path / "baseline")])
    out = capsys.readouterr().out
    assert rc == 0 and "全部通过" in out
    assert "tp=8 fp=6 fn=0 tn=10" in out and "全关日 fp=4" in out
    assert "offline_metrics 与 baseline 逐键一致" in out
    assert "raw 行 == metrics_by_split test 行" in out
    assert "pred_prob ≈ sigmoid" in out and "全关天 1：2026-01-01" in out


def test_audit_catches_corruption(tmp_path, capsys):
    df = _infer_frame()
    df.loc[3, "pred_state"] = 1 - int(df.loc[3, "pred_state"])   # 破坏一位 pred_state
    _make_run(tmp_path / "run", infer_df=df)
    rc = main(["--run-root", str(tmp_path / "run")])
    out = capsys.readouterr().out
    assert rc == 1 and "✗" in out
    assert "pred_state 判决链重放" in out and "逐位一致" in out


def test_audit_expect_mismatch(tmp_path, capsys):
    _make_run(tmp_path / "run")
    rc = main(["--run-root", str(tmp_path / "run"), "--expect-confusion", "9,9,9,9"])
    out = capsys.readouterr().out
    assert rc == 1 and "总混淆 == 期望 [9, 9, 9, 9]" in out
