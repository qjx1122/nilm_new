"""threshold_sweep（判决链阈值扫描）——合成数据单元 + CLI e2e。

⑯ thr30b 回收后的零成本补证工具：pred 与 decision_thr_w 无关，可在同一份
inference_result.csv 上离线扫描任意阈值的完整判决链（thr+min_on+fill_off）
混淆矩阵与 fp 幅值分布。合成数据手工推演期望值（含 fill_short_off 回填、
边缘不回填、min_on 语义），锁死判决链行为。
"""

from pathlib import Path

import pandas as pd
import pytest

from scripts.threshold_sweep import (
    chain_replay_check, fp_amplitude, load_result, main, sweep,
)

# —— 合成数据：2 天 × 12 点 ——
# day1（全关天，target 全 0）：pred=[5,15,25,35,45,5,...]
#   @10: fp=4（15/25/35/45 成串，min_on(1) 不删、无回填位）
#   @30: fp=2（35/45）；@50: fp=0
# day2（开机日，target idx2-9=1）：pred=[8,9,100,100,3,4,100,100,60,40,12,13]
#   @10: ≥10 = idx2,3,6..9,10,11；idx4,5（3W/4W ≤3 窗）被 fill_off(3) 回填
#        → idx2-11 全开：tp=8, fp=2（idx10,11）
#   @30: ≥30 = idx2,3,6,7,8,9；idx4,5 回填 → idx2-9：tp=8, fp=0
#   @50: ≥50 = idx2,3,6,7,8；idx9（40W<50）尾部 off 段=末段不回填
#        → idx2-8：tp=7, fn=1（idx9）, fp=0
DAY1_PRED = [5, 15, 25, 35, 45, 5, 5, 5, 5, 5, 5, 5]
DAY2_PRED = [8, 9, 100, 100, 3, 4, 100, 100, 60, 40, 12, 13]


def _synthetic_result() -> pd.DataFrame:
    frames = []
    for day, (pred, tgt) in {
        "2026-01-01": (DAY1_PRED, [0] * 12),
        "2026-01-02": (DAY2_PRED, [0, 0] + [1] * 8 + [0, 0]),
    }.items():
        frames.append(pd.DataFrame({
            "timestamp": pd.date_range(f"{day} 00:00:00", periods=12,
                                       freq="15min").strftime("%Y-%m-%d %H:%M:%S"),
            "target_state": tgt,
            "pred": pred,
            "pred_state": [0] * 12,   # 占位，下方按 @10 链真值填充
            "on_thr_w": 10.0,
            "decision_thr_w": 10.0,
        }))
    df = pd.concat(frames, ignore_index=True)
    # 文件自描述 @10 判决链（复现校验用）：day1 idx1-4 开；day2 idx2-11 全开（回填 idx4,5）
    from nilm.postprocess.state import postprocess_state
    df["pred_state"] = postprocess_state(
        df["pred"].to_numpy(float), 10.0, 1, 3).astype(int)
    return df


def test_sweep_chain_semantics():
    """三阈值扫描：回填/边缘不回填/min_on 语义 + 全关日 fp 分解。"""
    out = sweep(_synthetic_result(), [10, 30, 50], min_on=1, fill_off=3)
    assert len(out) == 3
    r10, r30, r50 = out.iloc[0], out.iloc[1], out.iloc[2]
    # @10：tp=8 fp=6（全关 4 + 开机 2）fn=0；P=8/14, F1=0.7273
    assert (r10.tp, r10.fp, r10.fn, r10.tn) == (8, 6, 0, 10)
    assert (r10.off_day_fp, r10.on_day_fp) == (4, 2)
    assert (r10.precision, r10.recall, r10.f1) == (0.5714, 1.0, 0.7273)
    # @30：tp=8 fp=2（全关 2）fn=0；P=0.8, F1=0.8889
    assert (r30.tp, r30.fp, r30.fn, r30.tn) == (8, 2, 0, 14)
    assert (r30.off_day_fp, r30.on_day_fp) == (2, 0)
    assert (r30.precision, r30.recall, r30.f1) == (0.8, 1.0, 0.8889)
    # @50：tp=7 fn=1（idx9 尾段不回填）fp=0；P=1.0, R=0.875
    assert (r50.tp, r50.fp, r50.fn, r50.tn) == (7, 0, 1, 16)
    assert (r50.precision, r50.recall, r50.f1) == (1.0, 0.875, 0.9333)


def test_fp_amplitude_bands():
    """fp@10 幅值带：15/25/35/45（全关日）+12/13（开机日）。"""
    out = fp_amplitude(_synthetic_result(), base_thr=10)
    assert dict(zip(out["band"], out["count"])) == {
        "[10, 20)": 3, "[20, 30)": 1, "[30, 50)": 2,
        "[50, 100)": 0, "[100, 200)": 0, "[200, ∞)": 0,
    }
    assert out["count"].sum() == 6


def test_chain_replay_check():
    """自描述阈值复现 pred_state：参数一致→True；min_on 不符→False。"""
    df = _synthetic_result()
    assert chain_replay_check(df, min_on=1, fill_off=3) == (10.0, True)
    assert chain_replay_check(df, min_on=5, fill_off=3) == (10.0, False)


def test_main_end_to_end(tmp_path, capsys):
    """CLI e2e：写临时 CSV → 扫描 → 控制台两张表 + 复现校验行。"""
    csv = tmp_path / "inference_result.csv"
    _synthetic_result().to_csv(csv, index=False)
    rc = main(["--csv", str(csv), "--thresholds", "10,30,50"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "自描述 decision_thr_w: [10.0]" in out
    assert "pred_state 逐位一致 ✓" in out
    assert "全关天 1：2026-01-01" in out
    assert "0.8889" in out and "0.9333" in out      # 扫描表 F1
    assert "[30, 50)" in out and " 2 " in out        # 幅值分布表


def test_train_predictions_split(tmp_path, capsys):
    """train_predictions.csv 适配：--pred-col/--state-col/--split（跨段护栏曲线）。

    test 段=day2（开机日单日）：@10 链 tp=8 fp=2 fn=0（F1 0.8889）、@30 链
    tp=8 fp=0 fn=0（F1 1.0）；复现校验在改列名+段过滤后仍须逐位一致。
    """
    df = _synthetic_result()
    df["split"] = ["train"] * 12 + ["test"] * 12
    df = df.rename(columns={"pred": "pred_transformer",
                            "pred_state": "pred_state_transformer"})
    csv = tmp_path / "train_predictions.csv"
    df.to_csv(csv, index=False)

    # 函数级：列名适配 + 段过滤后的扫描值
    df2 = load_result(csv, pred_col="pred_transformer")
    df2 = df2[df2["split"] == "test"].reset_index(drop=True)
    s = sweep(df2, [10, 30], 1, 3, pred_col="pred_transformer")
    assert list(zip(s.tp, s.fp, s.fn)) == [(8, 2, 0), (8, 0, 0)]

    # CLI 级：split 过滤行 + 复现校验 ✓ + 单日（无全关天）
    rc = main(["--csv", str(csv), "--pred-col", "pred_transformer",
               "--state-col", "pred_state_transformer",
               "--split", "test", "--thresholds", "10,30"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "split 过滤: test（24 → 12 行）" in out
    assert "pred_state 逐位一致 ✓" in out
    assert "全关天 0" in out
    assert "0.8889" in out
