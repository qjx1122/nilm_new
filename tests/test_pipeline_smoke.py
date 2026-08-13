"""端到端 smoke：合成数据跑通 train→evaluate→compare，验证多模型对比流水线。

合成设定：3 个分路按固定占比 [0.5, 0.3, 0.2] 分享母线有功 + 小噪声，
日周期正弦驱动。基线/岭回归都应能拟合到有限误差。
"""

import numpy as np
import pandas as pd

from nilm.pipeline import runner

SHARES = [0.5, 0.3, 0.2]


def _make_synthetic(tmp_path, days=4, seed=0):
    rng = np.random.default_rng(seed)
    n5 = 288 * days
    idx5 = pd.date_range("2026-01-01", periods=n5, freq="5min")
    t = np.arange(n5)
    p = 100 + 40 * np.sin(2 * np.pi * (t % 288) / 288) + rng.normal(0, 3, n5)
    p = np.clip(p, 1.0, None)

    bus = pd.DataFrame({
        "U_A": 220 + rng.normal(0, 1, n5),
        "U_B": 221 + rng.normal(0, 1, n5),
        "U_C": 219 + rng.normal(0, 1, n5),
        "I_A": p / (3 * 220 * 0.9) + rng.normal(0, 0.01, n5),
        "I_B": p / (3 * 220 * 0.9) + rng.normal(0, 0.01, n5),
        "I_C": p / (3 * 220 * 0.9) + rng.normal(0, 0.01, n5),
        "P": p,
        "PF_A": np.clip(0.9 + rng.normal(0, 0.01, n5), 0.5, 1.0),
        "PF_B": np.clip(0.9 + rng.normal(0, 0.01, n5), 0.5, 1.0),
        "PF_C": np.clip(0.9 + rng.normal(0, 0.01, n5), 0.5, 1.0),
    })
    bus.insert(0, "timestamp", idx5.strftime("%Y-%m-%d %H:%M:%S"))
    bus_csv = tmp_path / "bus.csv"
    bus.to_csv(bus_csv, index=False)

    p15 = pd.Series(p, index=idx5).resample("15min").mean()
    branch = pd.DataFrame({
        f"branch_{k + 1}": SHARES[k] * p15.to_numpy() + rng.normal(0, 0.5, len(p15))
        for k in range(len(SHARES))
    })
    branch.insert(0, "timestamp", p15.index.strftime("%Y-%m-%d %H:%M:%S"))
    branch_csv = tmp_path / "branch.csv"
    branch.to_csv(branch_csv, index=False)
    return bus_csv, branch_csv


def _cfg(tmp_path, bus_csv, branch_csv):
    return {
        "experiment_name": "smoke",
        "seed": 0,
        "output_dir": str(tmp_path / "outputs"),
        "data": {
            "bus_path": str(bus_csv),
            "branch_path": str(branch_csv),
            "timestamp_col": "timestamp",
            "bus_column_map": {
                "U_A": "u_a", "U_B": "u_b", "U_C": "u_c",
                "I_A": "i_a", "I_B": "i_b", "I_C": "i_c",
                "P": "p_total", "PF_A": "pf_a", "PF_B": "pf_b", "PF_C": "pf_c",
            },
            "branch_column_map": {},
        },
        "preprocess": {"clip_negative": True, "max_gap_interp": 2,
                       "train_frac": 0.7, "val_frac": 0.15},
        "features": {"lags": [1, 2, 3, 4], "rolling_windows": ["1h", "6h", "24h"]},
        "models": [
            {"name": "history_profile"},
            {"name": "proportional"},
            {"name": "ridge", "params": {"alpha": 1.0}},
        ],
        "constraints": {"nonnegative": True, "sum_consistency": True},
        "metrics": ["mae", "rmse", "r2", "sae"],
    }


def test_end_to_end_multi_model_compare(tmp_path):
    bus_csv, branch_csv = _make_synthetic(tmp_path)
    info = runner.run_all(_cfg(tmp_path, bus_csv, branch_csv))

    # 三个模型全部产出结果
    assert set(info["results"]) == {"history_profile", "proportional", "ridge"}

    # 所有宏平均指标为有限值
    for model, metrics in info["results"].items():
        for metric, value in metrics.items():
            assert np.isfinite(value["macro"]), (model, metric)
            assert len(value["per_branch"]) == len(SHARES)

    # 对比表与最优模型
    table = info["table"]
    assert set(table.index) == {"history_profile", "proportional", "ridge"}
    assert info["summary"]["overall_best"] in table.index

    # 产物落盘
    from pathlib import Path
    out_dir = Path(info["output_dir"])
    assert (out_dir / "metrics.json").exists()
    assert (out_dir / "comparison.md").exists()
    assert (out_dir / "models" / "ridge.pkl").exists()
    assert "综合最优" in (out_dir / "comparison.md").read_text(encoding="utf-8")
