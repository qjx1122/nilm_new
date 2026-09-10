"""合成数据工厂：构造符合指南 §3 契约的 <device>_<user> 数据目录。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

DEVICE = "800080270708"
USER = "4206602981958"
USER_KEY = f"{DEVICE}_{USER}"

STD_FIELDS = ["ua", "ub", "uc", "ia", "ib", "ic",
              "pa", "pb", "pc", "pfa", "pfb", "pfc"]
FIELD_MAP = {f: {"ch": 1, "column": f"load_iden_data{i}", "multiplier": 1.0}
             for i, f in enumerate(STD_FIELDS)}


def make_bus_df(days: int = 21, start: str = "2026-01-01", seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n5 = 288 * days
    idx5 = pd.date_range(start, periods=n5, freq="5min")
    t = np.arange(n5)
    p_total = np.clip(120 + 50 * np.sin(2 * np.pi * (t % 288) / 288)
                      + 15 * np.sin(2 * np.pi * (t % 96) / 96)
                      + rng.normal(0, 3, n5), 1.0, None)
    pa = p_total / 3 * (1 + rng.normal(0, 0.02, n5))
    pb = p_total / 3 * (1 + rng.normal(0, 0.02, n5))
    pc = p_total - pa - pb
    ua, ub, uc = (220 + rng.normal(0, 1, n5) for _ in range(3))
    pfa = np.clip(0.9 + rng.normal(0, 0.01, n5), 0.5, 1.0)
    pfb, pfc = pfa.copy(), pfa.copy()
    return pd.DataFrame({
        "event_time": idx5.strftime("%Y-%m-%d %H:%M:%S"),
        "load_iden_data0": ua, "load_iden_data1": ub, "load_iden_data2": uc,
        "load_iden_data3": pa / (ua * pfa), "load_iden_data4": pb / (ub * pfb),
        "load_iden_data5": pc / (uc * pfc),
        "load_iden_data6": pa, "load_iden_data7": pb, "load_iden_data8": pc,
        "load_iden_data9": pfa, "load_iden_data10": pfb, "load_iden_data11": pfc,
    }), idx5


def make_branch_df(idx5: pd.DatetimeIndex, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed + 100)
    p_total = pd.Series(np.clip(120 + 50 * np.sin(2 * np.pi * (np.arange(len(idx5)) % 288) / 288), 1, None),
                        index=idx5).resample("15min").mean()
    n = len(p_total)
    return pd.DataFrame({
        "time": p_total.index.strftime("%Y-%m-%d %H:%M:%S"),
        "p1": 0.5 * p_total.to_numpy() + rng.normal(0, 0.5, n),
        "p2": 0.3 * p_total.to_numpy() + rng.normal(0, 0.5, n),
    })


def write_user_dir(root: Path, user_key: str = USER_KEY, days: int = 21,
                   with_branch: bool = True, mode_dir: str = "trains",
                   seed: int = 0) -> Path:
    """在 root/<mode_dir>/<user_key>/ 写入合法的总线+分路 CSV（§3.2/§3.3 命名）。"""
    device, user = user_key.split("_")
    d = root / mode_dir / user_key
    d.mkdir(parents=True, exist_ok=True)
    bus, idx5 = make_bus_df(days=days, seed=seed)
    s_code, e_code = idx5[0].strftime("%y%m%d"), idx5[-1].strftime("%y%m%d")
    bus.to_csv(d / f"e241_{device}_{user}-Ch1-{s_code}-{e_code}.csv", index=False)
    if with_branch:
        branch = make_branch_df(idx5, seed=seed)
        branch.to_csv(d / f"{user}-{s_code}-{e_code}.csv", index=False)
    return d


@pytest.fixture
def base_cfg() -> dict:
    """测试用基础配置（与 configs/default.yaml 同构，阈值放宽）。"""
    return {
        "experiment_name": "test", "seed": 0, "output_dir": "outputs",
        "quality": {"max_missing_rate": 0.3, "min_coverage": 0.3,
                    "min_score": 30, "min_days": 3},
        "preprocess": {"clip_negative": True, "allow_negative_power": False,
                       "max_gap_interp": 2,
                       "agg_strategy": {"u": "mean", "i": "mean", "p": "mean", "pf": "recompute"}},
        "features": {"lags": [1, 2], "rolling_windows": ["1h", "6h"]},
        "dataset": {"window": 96, "mode": "seq2seq"},
        "bus_field_map": FIELD_MAP,
        "models": [{"name": "history_profile"}, {"name": "proportional"},
                   {"name": "ridge", "params": {"alpha": 1.0}}],
        "metrics": ["mae", "rmse", "r2", "sae"],
    }


@pytest.fixture
def base_cfg_file(tmp_path: Path, base_cfg: dict) -> Path:
    import yaml
    p = tmp_path / "base.yaml"
    p.write_text(yaml.safe_dump(base_cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


@pytest.fixture
def time_filter_file(tmp_path: Path) -> Path:
    cfg = {
        USER_KEY: {"target_col": "p1", "split_ratios": [0.7, 0.15, 0.15],
                   "split_strategy": "time", "on_thr_w": 10.0,
                   "post_min_on": 1, "post_fill_short_off": 3},
        "_default": {"on_thr_w": 10.0, "split_ratios": [0.6, 0.2, 0.2],
                     "split_strategy": "stratified_day"},
    }
    p = tmp_path / "time_filter.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p
