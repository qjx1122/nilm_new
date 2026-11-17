"""pooled 通用链路：normalize + pooled_splits + pooled_task 烟雾（新增，不影响单用户）。"""

import numpy as np
import pandas as pd
import pytest

from nilm.preprocess.normalize import (cap_from_bus, compute_cap,
                                       denormalize_target, normalize_features,
                                       normalize_target)
from nilm.preprocess.pooled_splits import (assign_pooled_splits, louo_folds,
                                           split_mask_for_index)


def test_compute_cap_and_roundtrip():
    s = pd.Series([0.0, 100.0, 200.0, 1000.0])
    cap = compute_cap(s, quantile=0.95)
    assert cap == pytest.approx(880.0)
    y = pd.Series([0.0, 100.0, 500.0])
    yn = normalize_target(y, cap)
    assert np.allclose(denormalize_target(yn.to_numpy(), cap), y.to_numpy())
    # 空/零回退
    assert compute_cap(pd.Series([], dtype=float)) == 1.0


def test_normalize_features_only_power_cols():
    idx = pd.date_range("2026-01-01", periods=3, freq="15min")
    feat = pd.DataFrame({
        "pbus": [100.0, 200.0, 300.0],
        "pa": [50.0, 100.0, 150.0],
        "ua": [220.0, 220.0, 220.0],
        "pfa": [0.9, 0.9, 0.9],
        "slot": [0, 1, 2],
        "roll_mean_p_1h": [100.0, 150.0, 200.0],
        "imb_p": [0.1, 0.2, 0.3],
    }, index=idx)
    out = normalize_features(feat, cap=100.0)
    assert out["pbus"].tolist() == [1.0, 2.0, 3.0]
    assert out["pa"].tolist() == [0.5, 1.0, 1.5]
    assert out["roll_mean_p_1h"].tolist() == [1.0, 1.5, 2.0]
    # 非功率列不变
    assert out["ua"].tolist() == [220.0, 220.0, 220.0]
    assert out["slot"].tolist() == [0, 1, 2]
    assert out["imb_p"].tolist() == [0.1, 0.2, 0.3]
    # 原表不变
    assert feat["pbus"].tolist() == [100.0, 200.0, 300.0]


def test_cap_from_bus():
    idx = pd.date_range("2026-01-01", periods=4, freq="15min")
    bus = pd.DataFrame({"pa": [10.0, 20.0, 30.0, 40.0],
                        "pb": [0.0, 0.0, 0.0, 0.0],
                        "pc": [0.0, 0.0, 0.0, 0.0]}, index=idx)
    assert cap_from_bus(bus, quantile=0.5) == pytest.approx(25.0)


def test_louo_folds():
    folds = louo_folds(["b", "a", "c"])
    assert len(folds) == 3
    for train, held in folds:
        assert held not in train and len(train) == 2
    with pytest.raises(ValueError):
        louo_folds(["only_one"])


def _make_user_frame(n_days=10, on=True):
    idx = pd.date_range("2026-01-01", periods=96 * n_days, freq="15min")
    y = pd.Series(500.0 if on else 5.0, index=idx)
    return idx, y, 10.0


def test_assign_pooled_splits_loo_and_time():
    frames = {"u1": _make_user_frame(10, True), "u2": _make_user_frame(10, False),
              "u3": _make_user_frame(10, True)}
    # LOO: u3 整体 test，u1/u2 内切 train/val
    assign = assign_pooled_splits(frames, ["u1", "u2"], ["u3"], val_ratio=0.2)
    assert set(assign["u3"].values()) == {"test"}
    assert set(assign["u1"].values()) <= {"train", "val"}
    # 日粒度互斥完备
    for uk in ("u1", "u2"):
        assert len(assign[uk]) == 10
    # mask 对齐
    idx, _, _ = frames["u1"]
    m = split_mask_for_index(idx, assign["u1"], "train")
    assert m.sum() > 0 and m.sum() < len(idx)
    # time 策略
    assign_t = assign_pooled_splits(frames, ["u1"], [], val_ratio=0.2, strategy="time")
    assert set(assign_t["u1"].values()) <= {"train", "val"}
    with pytest.raises(ValueError):
        assign_pooled_splits(frames, ["u1"], [], strategy="unknown")


def test_pooled_task_smoke(tmp_path):
    """pooled_task 端到端烟雾：合成 2 用户 -> 训练 -> 新用户推理（ridge only 快速）。"""
    from pathlib import Path

    import yaml
    from nilm.data_io.discovery import scan_root
    from nilm.pipeline.pooled_task import run_pooled_infer, run_pooled_train

    data_root = tmp_path / "data"
    (data_root / "trains").mkdir(parents=True)
    (data_root / "infers").mkdir(parents=True)
    rng = np.random.default_rng(0)
    # 2 个训练用户：总线 5min(288/天) + 分路 15min(96/天)，10 天
    for uk, scale in (("800080270001_4200000000001", 1.0), ("800080270002_4200000000002", 2.0)):
        device, user = uk.split("_")
        d = data_root / "trains" / uk
        d.mkdir(parents=True)
        idx5 = pd.date_range("2026-01-01", periods=288 * 10, freq="5min")
        on = (idx5.hour >= 8) & (idx5.hour < 18)
        pbus = np.where(on, 500.0 * scale, 20.0 * scale) + rng.normal(0, 5, len(idx5))
        bus = pd.DataFrame({
            "event_time": idx5.strftime("%Y-%m-%d %H:%M:%S"),
            "load_iden_data9": 220.0, "load_iden_data45": 220.0, "load_iden_data81": 220.0,
            "load_iden_data1": pbus / 220.0, "load_iden_data37": 0.0, "load_iden_data73": 0.0,
            "load_iden_data7": pbus, "load_iden_data43": 0.0, "load_iden_data79": 0.0,
            "load_iden_data8": 0.95, "load_iden_data44": 0.95, "load_iden_data80": 0.95,
        })
        bus.to_csv(d / f"e241_{device}_{user}-Ch1-260101-260110.csv", index=False)
        idx15 = pd.date_range("2026-01-01", periods=96 * 10, freq="15min")
        on15 = (idx15.hour >= 8) & (idx15.hour < 18)
        br = pd.DataFrame({"time": idx15.strftime("%Y-%m-%d %H:%M:%S"),
                           "p1": np.where(on15, 400.0 * scale, 0.0) + rng.normal(0, 3, len(idx15))})
        br.to_csv(d / f"{user}-260101-260110.csv", index=False)
    # 推理用户：只有总线
    uk_new = "800080270003_4200000000003"
    device, user = uk_new.split("_")
    d = data_root / "infers" / uk_new
    d.mkdir(parents=True)
    idx5 = pd.date_range("2026-01-01", periods=288 * 5, freq="5min")
    on = (idx5.hour >= 8) & (idx5.hour < 18)
    pbus = np.where(on, 500.0, 20.0) + rng.normal(0, 5, len(idx5))
    bus = pd.DataFrame({
        "event_time": idx5.strftime("%Y-%m-%d %H:%M:%S"),
        "load_iden_data9": 220.0, "load_iden_data45": 220.0, "load_iden_data81": 220.0,
        "load_iden_data1": pbus / 220.0, "load_iden_data37": 0.0, "load_iden_data73": 0.0,
        "load_iden_data7": pbus, "load_iden_data43": 0.0, "load_iden_data79": 0.0,
        "load_iden_data8": 0.95, "load_iden_data44": 0.95, "load_iden_data80": 0.95,
    })
    bus.to_csv(d / f"e241_{device}_{user}-Ch1-260101-260105.csv", index=False)

    base_cfg = yaml.safe_load(open("configs/base_universal.yaml", encoding="utf-8"))
    # 烟雾只用 ridge（transformer 慢），epochs 不影响 ridge
    base_cfg["models"] = [{"name": "ridge", "params": {"alpha": 1.0, "off_weight": 1.0, "off_thr_w": 10.0}}]
    base_cfg["quality"]["day_gate"] = False
    base_cfg["quality"]["min_days"] = 1
    base_cfg["preprocess"]["min_overlap"] = 0.1

    scans = {r.user_key: r for r in scan_root(data_root / "trains", "train") if r.ok}
    assert len(scans) == 2
    user_cfgs = {uk: {"target_col": "p1", "on_thr_w": 10.0, "split_ratios": [0.6, 0.2, 0.2],
                      "split_strategy": "stratified_by_state", "post_min_on": 1,
                      "post_fill_short_off": 3} for uk in scans}
    out_root = tmp_path / "outputs"
    res = run_pooled_train(sorted(scans.keys()), scans, user_cfgs, base_cfg,
                           out_root, target_col="p1", loo=False)
    assert res.status == "OK", res.message
    assert (Path(res.output_dir) / "meta.json").exists()
    assert (Path(res.output_dir) / "models" / "ridge.pkl").exists()
    assert (Path(res.output_dir) / "metrics_by_user.csv").exists()

    infer_scans = {r.user_key: r for r in scan_root(data_root / "infers", "infer") if r.ok}
    assert uk_new in infer_scans
    res2 = run_pooled_infer(uk_new, infer_scans[uk_new],
                            {"target_col": "p1", "on_thr_w": 10.0, "post_min_on": 1,
                             "post_fill_short_off": 3},
                            base_cfg, out_root, target_col="p1")
    assert res2.status == "OK", res2.message
    assert (Path(res2.output_dir) / "predictions" / "inference_result.csv").exists()
