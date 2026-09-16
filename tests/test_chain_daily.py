"""metrics_daily_chain（链口径日级）——回归同能力、分类按 pred_state 逐日。

覆盖：I.J 新增产物的列契约、阈值自描述、Σ==总数、TP+FN 恒等、n_points 与
能力口径对齐、以及 evaluate_daily_chain 的分类/回归分离语义。
"""

import numpy as np
import pandas as pd

from nilm.evaluation.metrics import evaluate_daily, evaluate_daily_chain
from nilm.postprocess.state import postprocess_state


def test_evaluate_daily_chain_regression_same_as_daily():
    """回归指标（mae/rmse/r2/sae）在链口径与能力口径应一致（同 y_pred）。"""
    idx = pd.date_range("2026-01-01", periods=4, freq="12h")
    y_true = np.array([[10.0], [20.0], [30.0], [40.0]])
    y_pred = np.array([[12.0], [18.0], [35.0], [38.0]])
    pred_state = np.array([0, 1, 1, 0])
    for name in ("mae", "rmse", "r2", "sae"):
        cap = evaluate_daily(y_true, y_pred, idx, [name], on_thr_w=10)
        chain = evaluate_daily_chain(y_true, y_pred, pred_state, idx, [name], on_thr_w=10)
        assert np.allclose(cap[name].to_numpy(), chain[name].to_numpy(), equal_nan=True)


def test_evaluate_daily_chain_classification_uses_pred_state():
    """分类指标（tp/fp/fn/tn/f1）按 target_state vs pred_state，不按 raw pred。"""
    idx = pd.date_range("2026-01-01", periods=4, freq="12h")
    y_true = np.array([[0.0], [0.0], [100.0], [100.0]])
    y_pred = np.array([[15.0], [25.0], [110.0], [5.0]])  # raw @10 => 1,1,1,0
    # chain @30 => pred_state 0,0,1,0
    pred_state = np.array([0, 0, 1, 0])
    # 能力口径 raw: 两日合计 tp1 fp2 fn1
    cap = evaluate_daily(y_true, y_pred, idx, ["tp", "fp", "fn", "tn", "f1"], on_thr_w=10)
    chain = evaluate_daily_chain(y_true, y_pred, pred_state, idx, ["tp", "fp", "fn", "tn", "f1"], on_thr_w=10)
    # 链口径: pred_state 0,0,1,0 => 合计 tp1 fp0 fn1 tn2
    assert int(chain["tp"].sum()) == 1 and int(chain["fp"].sum()) == 0
    assert int(chain["fn"].sum()) == 1 and int(chain["tn"].sum()) == 2
    assert int(cap["fp"].sum()) == 2  # 能力口径多 2 fp
    assert float(chain["f1"].sum()) != float(cap["f1"].sum())


def test_evaluate_daily_chain_grouping_and_sum():
    """按日分组 + Σ==总数 + TP+FN 恒等。"""
    idx = pd.date_range("2026-01-01", periods=6, freq="8h")  # 2 天 ×3
    y_true = np.array([[0], [0], [0], [100], [100], [100]], dtype=float)
    y_pred = np.array([[5], [15], [25], [110], [5], [110]], dtype=float)
    pred_state = postprocess_state(y_pred[:, 0], 30, 1, 3)
    df = evaluate_daily_chain(y_true, y_pred, pred_state, idx, ["tp", "fp", "fn", "tn", "f1"], on_thr_w=10)
    assert len(df) == 2 and set(df["date"]) == {"2026-01-01", "2026-01-02"}
    # 逐日 n_points
    assert list(df["n_points"]) == [3, 3]
    # 总数 = 全量直接算
    t_on = y_true[:, 0] >= 10
    p_on = pred_state.astype(bool)
    tp = int((p_on & t_on).sum())
    fp = int((p_on & ~t_on).sum())
    assert int(df["tp"].sum()) == tp and int(df["fp"].sum()) == fp
    # TP+FN 恒等 = 真值开点数
    assert int((df["tp"] + df["fn"]).sum()) == int(t_on.sum())


def test_batch_produces_chain_with_threshold_columns(tmp_path):
    """端到端：train/infer 均产 metrics_daily_chain.csv，且阈值列与配置一致。"""
    import json, yaml
    from pathlib import Path
    from tests.conftest import write_user_dir
    from nilm.pipeline.batch import run_batch

    USER_KEY = "800080252844_4206894986488"
    tmp = Path(tmp_path)
    data_root = tmp / "data"
    out_root = tmp / "outputs"
    base_cfg = yaml.safe_load(Path("configs/default.yaml").read_text())
    tf = {USER_KEY: {"target_col": "p1", "on_thr_w": 10.0, "decision_thr_w": 30.0, "post_min_on": 2, "post_fill_short_off": 1},
          "_default": {"on_thr_w": 10.0}}
    tf_file = tmp / "tf.json"
    tf_file.write_text(json.dumps(tf))
    base_file = tmp / "base.yaml"
    base_file.write_text(yaml.safe_dump(base_cfg))
    write_user_dir(data_root, USER_KEY, days=9, seed=1)
    write_user_dir(data_root, USER_KEY, days=9, seed=1, mode_dir="infers")
    info = run_batch(str(tf_file), base_config_path=str(base_file), data_root=data_root, output_root=out_root, stages=("train", "infer"), user_keys=[USER_KEY])
    # 断言 OK
    import pandas as pd
    train_dir = sorted((out_root / USER_KEY / "train").iterdir())[-1]
    infer_dir = sorted((out_root / USER_KEY / "infer").iterdir())[-1]
    for p in (train_dir / "metrics_daily_chain.csv", infer_dir / "metrics_daily_chain.csv"):
        assert p.exists(), p
        df = pd.read_csv(p)
        assert {"model", "date", "n_points", "tp", "fp", "fn", "tn", "state_thr_w", "decision_thr_w", "post_min_on", "post_fill_short_off"} <= set(df.columns)
        assert (df["state_thr_w"] == 10.0).all()
        assert (df["decision_thr_w"] == 30.0).all()
        assert (df["post_min_on"] == 2).all()
        assert (df["post_fill_short_off"] == 1).all()
        # n_points 与能力口径对齐
        cap = pd.read_csv(p.parent / "metrics_daily.csv")
        # train 有 split 列，infer 无
        if "split" in df.columns:
            merged = pd.merge(df, cap, on=["model", "split", "date"], suffixes=("_chain", "_cap"))
            assert (merged["n_points_chain"] == merged["n_points_cap"]).all()
        else:
            merged = pd.merge(df, cap, on=["model", "date"], suffixes=("_chain", "_cap"))
            assert (merged["n_points_chain"] == merged["n_points_cap"]).all()
