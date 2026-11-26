"""preprocess.splits：六种策略（含基于每日质量的 auto）、互斥完备、日粒度连续块与 qualified_days_detail 对账验证。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nilm.common.contracts import SPLIT_STRATEGIES, Status
from nilm.data_io.validator import (
    qualified_days_detail,
    qualified_days_summary,
    split_coverage_advice,
)
from nilm.pipeline.batch import run_batch
from nilm.pipeline.user_config import resolve_user_config
from nilm.pipeline.user_task import latest_done_dir
from nilm.preprocess.splits import (
    build_day_quality_profiles,
    build_split_masks,
    curate_auto_split_pool,
    initial_split,
    resolve_auto_quality_split,
    summarize_split_decision,
)
from scripts.auto_run_steps2to5 import ensure_user_config, step_sync_decision_thr

IDX = pd.date_range("2026-01-01", periods=96 * 60, freq="15min")  # 60 天
RATIOS = [0.6, 0.2, 0.2]


def _check(masks):
    total = masks["train"] | masks["val"] | masks["test"]
    assert total.all()                                    # 完备
    assert not ((masks["train"] & masks["val"]).any()
                or (masks["train"] & masks["test"]).any()
                or (masks["val"] & masks["test"]).any())  # 互斥
    # 切分以整天为单位（日内不打散，§11）
    for m in masks.values():
        per_day = m.groupby(m.index.date).nunique()
        assert (per_day == 1).all()


def test_all_strategies_valid():
    assert "auto" in SPLIT_STRATEGIES
    resolved = resolve_user_config("800080270733_4206673297219", {
        "800080270733_4206673297219": {"split_strategy": "auto"},
    })
    assert resolved["split_strategy"] == "auto"

    for strategy in ("auto", "time", "stratified_day", "stratified", "global_stratified"):
        masks = initial_split(IDX, RATIOS, strategy)
        _check(masks)

    # 构造合成 target：前 30 天关（max<on_thr），后 30 天开
    target = pd.Series(np.nan, index=IDX)
    cutoff = IDX[0] + pd.Timedelta(days=30)
    target.loc[IDX < cutoff] = 5.0
    target.loc[IDX >= cutoff] = 500.0

    for strat in ("stratified_by_state", "auto"):
        masks = initial_split(IDX, RATIOS, strat, target=target, on_thr_w=50)
        _check(masks)

        def _off_ratio(m):
            days = sorted(pd.Series(m.index[m].normalize()).unique())
            off = sum(1 for d in days if d < cutoff.normalize())
            return off / len(days) if days else 0

        ratios = {k: _off_ratio(v) for k, v in masks.items()}
        assert max(ratios.values()) - min(ratios.values()) < 0.15

    # 缺 target 时 stratified_by_state 退化为 time 而非抛错
    import warnings
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        masks_fb = initial_split(IDX, RATIOS, "stratified_by_state")
        _check(masks_fb)
        assert any("退化为 time" in str(x.message) for x in w)


def test_time_strategy_chronological():
    masks = initial_split(IDX, RATIOS, "time")
    tr, va, te = IDX[masks["train"]], IDX[masks["val"]], IDX[masks["test"]]
    assert tr.max() < va.min()
    assert va.max() < te.min()


def test_ratio_approximation():
    masks = initial_split(IDX, RATIOS, "time")
    n = len(IDX)
    assert abs(masks["train"].sum() / n - 0.6) < 0.02
    assert abs(masks["val"].sum() / n - 0.2) < 0.02


def test_build_with_anchors_and_repair():
    spec = {"test": {"include": [["2026-02-25", "2026-02-25"]]}}
    for strat in ("time", "auto"):
        masks = build_split_masks(IDX, [0.7, 0.15, 0.15], strat, spec)
        _check(masks)
        assert masks["test"][IDX.date == pd.Timestamp("2026-02-25").date()].all()


def test_invalid_ratios():
    with pytest.raises(ValueError):
        initial_split(IDX, [0.5, 0.5, 0.5], "time")
    with pytest.raises(ValueError):
        initial_split(IDX, RATIOS, "unknown_strategy")


def test_stratified_by_state_build_with_target():
    target = pd.Series(np.nan, index=IDX)
    cutoff = IDX[0] + pd.Timedelta(days=30)
    target.loc[IDX < cutoff] = 5.0
    target.loc[IDX >= cutoff] = 500.0
    masks = build_split_masks(IDX, RATIOS, "stratified_by_state",
                              target=target, on_thr_w=50)
    _check(masks)

    def _off_ratio(m):
        days = sorted(pd.Series(m.index[m].normalize()).unique())
        off = sum(1 for d in days if d < cutoff.normalize())
        return off / len(days) if days else 0

    ratios = {k: _off_ratio(v) for k, v in masks.items()}
    assert max(ratios.values()) - min(ratios.values()) < 0.15


# ---------------------------------------------------------------- auto 策略专项单元测试
def test_auto_split_contiguous_blocks_and_natural_gap_alignment():
    """验证 auto 策略相比 stratified_day 大幅减少窗口碎片，并自动贴合天然离线断口。"""
    full_days = pd.date_range("2026-03-01", periods=32, freq="D")
    # 模拟第 19 天与第 26 天因设备掉线被前置 day_gate 剔除，留下两处天然断口（剩余 30 天）
    dropped = {pd.Timestamp("2026-03-19"), pd.Timestamp("2026-03-26")}
    kept_days = [d for d in full_days if d not in dropped]
    idx_parts = [pd.date_range(d, periods=96, freq="15min") for d in kept_days]
    idx = idx_parts[0].append(idx_parts[1:])

    target = pd.Series(300.0, index=idx)
    # 每周日设全关天，使任意 6 天窗口均自然包含至少 1 个全关天和多个开机天
    for d in kept_days:
        if d.day_of_week == 6:
            target.loc[idx.normalize() == d] = 5.0

    dq = pd.DataFrame({
        "date": [d.strftime("%Y-%m-%d") for d in kept_days],
        "bus_score": [99.0] * len(kept_days),
        "branch_score": [99.0] * len(kept_days),
        "qualified": [1] * len(kept_days),
    })

    masks_auto, meta_auto = resolve_auto_quality_split(
        idx, [0.6, 0.2, 0.2], daily_quality=dq, target=target, on_thr_w=50.0
    )
    masks_dow = initial_split(idx, [0.6, 0.2, 0.2], "stratified_day")
    _check(masks_auto)

    sum_auto = summarize_split_decision(idx, masks_auto, "auto", dq, target, 50.0, auto_meta=meta_auto)
    sum_dow = summarize_split_decision(idx, masks_dow, "stratified_day", dq, target, 50.0)

    # auto 将切点精确对齐在两处天然断口上：人为新增切口为 0，每折仅 1 个连续自然日块！
    assert meta_auto["resolved_mode"] == "auto_contiguous_quality_blocks"
    assert meta_auto["artificial_cuts"] == 0
    assert meta_auto["natural_gap_cuts"] == 2
    assert sum_auto["by_split"]["val"]["contiguous_blocks"] == 1
    assert sum_auto["by_split"]["test"]["contiguous_blocks"] == 1
    assert sum_auto["by_split"]["train"]["contiguous_blocks"] == 1
    # stratified_day 则把各折切出更多连续段
    tot_blocks_auto = sum(sum_auto["by_split"][s]["contiguous_blocks"] for s in ("train", "val", "test"))
    tot_blocks_dow = sum(sum_dow["by_split"][s]["contiguous_blocks"] for s in ("train", "val", "test"))
    assert tot_blocks_auto == 3 < tot_blocks_dow


def test_auto_split_prefers_high_quality_days_for_val_and_test():
    """验证 auto 策略优先将 A 级高分完整天选入 val/test，将 70~75 分残缺天留在 train。"""
    days = pd.date_range("2026-04-01", periods=25, freq="D")
    idx = pd.date_range("2026-04-01", periods=96 * 25, freq="15min")
    target = pd.Series(400.0, index=idx)
    for i, d in enumerate(days):
        if i % 5 == 2:
            target.loc[idx.normalize() == d] = 3.0  # 均匀全关天

    # 最末尾 5 天（days 20..24）传感器频繁闪断，仅 72 分（C 级）；days 10..19 为 100 分精品天（A 级）
    bus_scores = [92.0] * 10 + [100.0] * 10 + [72.0] * 5
    br_scores = [92.0] * 10 + [100.0] * 10 + [73.0] * 5
    dq = pd.DataFrame({
        "date": [d.strftime("%Y-%m-%d") for d in days],
        "bus_score": bus_scores,
        "branch_score": br_scores,
        "qualified": [1] * 25,
    })

    masks, meta = resolve_auto_quality_split(idx, [0.6, 0.2, 0.2], dq, target, 50.0)
    _check(masks)
    summary = summarize_split_decision(idx, masks, "auto", dq, target, 50.0, auto_meta=meta)

    assert summary["by_split"]["val"]["mean_min_side_score"] >= 95.0
    assert summary["by_split"]["test"]["mean_min_side_score"] >= 95.0
    assert summary["by_split"]["val"]["tier_c_days"] == 0
    assert summary["by_split"]["test"]["tier_c_days"] == 0
    # 5 个 C 级低分天全部被安全留在 train 中充当补充样本，不污染 val/test 标尺
    assert summary["by_split"]["train"]["tier_c_days"] == 5


def test_auto_split_middle_off_day_cluster_preserves_training_coverage():
    # 15 all-on, 21 mixed (11 off), 14 all-on: contiguous val/test blocks
    # cannot both cover off-days without extracting nearly the whole middle cluster.
    idx = pd.date_range("2026-05-21", periods=50 * 96, freq="15min")
    daily_on = np.ones(50, dtype=bool)
    daily_on[15:36] = np.array([
        False, False, True, False, False, False, True, True, False, True, True,
        False, True, True, False, True, True, False, False, True, False,
    ])
    target = pd.Series(np.repeat(np.where(daily_on, 100.0, 0.0), 96), index=idx)
    masks, meta = resolve_auto_quality_split(idx, [0.6, 0.2, 0.2], target=target, on_thr_w=50.0)
    _check(masks)

    split_off = {}
    for name, mask in masks.items():
        day_mask = pd.Series(mask.to_numpy(), index=idx).groupby(idx.normalize()).any()
        split_off[name] = sum(
            not daily_on[(day - idx[0].normalize()).days]
            for day in day_mask.index[day_mask]
        )
    assert meta["train_off_days"] >= 6
    assert meta["train_off_floor"] == 6
    assert meta["off_rate_spread"] <= 0.20
    assert split_off["train"] >= 6
    assert sum(split_off.values()) == 11


@pytest.mark.parametrize("n_off,expected_scarcity,expected_min_off", [
    (0, "zero_off_days", (0, 0, 0)),
    (1, "single_off_day_reserved_for_train", (1, 0, 0)),
    (2, "two_off_days_train_val", (1, 1, 0)),
    (4, "sufficient", (1, 1, 1)),
])
def test_auto_split_off_day_scarcity_laws(n_off, expected_scarcity, expected_min_off):
    """验证全关天数为 0 / 1 / 2 / >=3 四档稀缺度下的铁律分配，以及小样本单日容量兼容性。"""
    idx = pd.date_range("2026-05-01", periods=96 * 15, freq="15min")
    days = sorted(pd.Series(idx.normalize()).unique())
    target = pd.Series(500.0, index=idx)
    # 故意将全关天集中放在时间轴后段或中段，测试算法是否主动重排保底
    off_positions = [13, 14, 7, 3][:n_off]
    for pos in off_positions:
        target.loc[idx.normalize() == days[pos]] = 2.0

    masks, meta = resolve_auto_quality_split(idx, [0.6, 0.2, 0.2], target=target, on_thr_w=50.0)
    _check(masks)
    assert meta["off_day_scarcity"] == expected_scarcity
    summary = summarize_split_decision(idx, masks, "auto", target=target, on_thr_w=50.0, auto_meta=meta)
    assert summary["by_split"]["train"]["off_days"] >= expected_min_off[0]
    assert summary["by_split"]["val"]["off_days"] >= expected_min_off[1]
    assert summary["by_split"]["test"]["off_days"] >= expected_min_off[2]

    # 当全关天 >= 3 时，split_coverage_advice 不应产生任何「不含全关天」告警
    if n_off >= 3:
        dq = pd.DataFrame({
            "date": [d.strftime("%Y-%m-%d") for d in days],
            "bus_score": [100.0] * 15,
            "branch_score": [100.0] * 15,
            "qualified": [1] * 15,
        })
        qd = qualified_days_detail(
            dq, target, 50.0,
            split_index={k: idx[masks[k].to_numpy()] for k in ("train", "val", "test")},
        )
        cov_adv = split_coverage_advice(qd)
        assert not any("不含" in msg and "全关天" in msg for msg in cov_adv)


def test_auto_split_small_5_day_pool_single_day_val_test_capacity():
    """极小样本（5 天：train=3, val=1, test=1）下，单日折容量=1 不会触发开+关死锁。"""
    idx = pd.date_range("2026-06-01", periods=96 * 5, freq="15min")
    days = sorted(pd.Series(idx.normalize()).unique())
    target = pd.Series(350.0, index=idx)
    target.loc[idx.normalize().isin([days[1], days[3]])] = 4.0  # 3 开 + 2 关
    masks, meta = resolve_auto_quality_split(idx, [0.6, 0.2, 0.2], target=target, on_thr_w=50.0)
    _check(masks)
    summary = summarize_split_decision(idx, masks, "auto", target=target, on_thr_w=50.0, auto_meta=meta)
    assert summary["by_split"]["train"]["days"] == 3
    assert summary["by_split"]["val"]["days"] == 1
    assert summary["by_split"]["test"]["days"] == 1
    assert summary["by_split"]["train"]["on_days"] >= 1
    assert summary["by_split"]["train"]["off_days"] >= 1


def test_curate_auto_split_pool_trims_stub_and_excess_old_off_days():
    """验证池级净水：剔除 <16 点残片天 + 0800 型全关日 >65% 时自动从最远期裁剪冗余全关天，且保护锚定日。"""
    # 构造 40 天 + 1 个 8 点残片天：前 26 天全关（春节/停产长假），后 14 天中 12 天开机 + 2 天周末全关（共 28 关 / 40 = 70%）
    idx_main = pd.date_range("2026-01-01", periods=96 * 40, freq="15min")
    idx_stub = pd.date_range("2026-02-10 00:00:00", periods=8, freq="15min")  # 仅 2 小时残片天
    idx = idx_main.append(idx_stub)

    bus = pd.DataFrame({"pa": 100.0, "pb": 100.0, "pc": 100.0}, index=idx)
    branch = pd.DataFrame({"p1": 2.0}, index=idx)
    days_main = sorted(pd.Series(idx_main.normalize()).unique())
    for i in range(26, 40):
        if i not in (31, 38):  # 留 2 个近期全关日
            branch.loc[idx.normalize() == days_main[i], "p1"] = 450.0
    target = branch["p1"].copy()

    # 人工显式锚定最早第 1 天进入 train，测试保护逻辑绝不误删该锚定日
    spec = {"train": {"include": [["2026-01-01", "2026-01-01"]]}}
    bus_c, br_c, tgt_c, info = curate_auto_split_pool(
        bus, branch, target, on_thr_w=50.0, splits_spec=spec, min_days=14.0
    )
    assert info["trimmed_stub_days"] == ["2026-02-10"]
    assert len(info["trimmed_excess_off_days"]) >= 12
    assert "2026-01-01" not in info["trimmed_excess_off_days"]  # 人工锚定日受保护
    assert "2026-01-02" in info["trimmed_excess_off_days"]      # 最早非锚定冗余全关日优先裁剪
    # 近期全关日完整保留
    assert pd.Timestamp("2026-02-01") in set(tgt_c.index.normalize())
    assert pd.Timestamp("2026-02-08") in set(tgt_c.index.normalize())
    assert 0.35 <= info["after_off_ratio"] <= 0.52


# ---------------------------------------------------------------- 端到端流水线 + qualified_days_detail.csv 100% 对账验证
def _write_e2e_quality_user(root: Path, user_key: str) -> Path:
    """构造含坏天、边缘低分天、远期过剩全关天与近期混合工况的 36 天真实工业合成用户。"""
    rng = np.random.default_rng(20260910)
    device, user = user_key.split("_")
    d = root / "trains" / user_key
    d.mkdir(parents=True, exist_ok=True)

    days = 36
    n15 = 96 * days
    idx15 = pd.date_range("2026-01-01", periods=n15, freq="15min")
    p1 = np.zeros(n15, dtype=np.float64)

    # 设计工况：
    # - Day 0..20 (21 天): 全关停产长假（触发 >65% 过剩全关天自动裁剪）
    # - Day 21: 分路大面积缺数坏天（score < 70，将被 day_gate 剔除，产生天然离线断口）
    # - Day 22..23: 轻度掉线 C 级合格天（缺 22 个点 -> ~77 分，应优先留 train）
    # - Day 24..35 (12 天): 100 分 A 级满点近期区段，含 10 个开机日 + 2 个近期全关日（Day 28, Day 34）
    for day_i in range(days):
        sl = slice(day_i * 96, (day_i + 1) * 96)
        arr = np.clip(12.0 + rng.normal(0.0, 2.0, 96), 1.0, 20.0)
        if day_i >= 22 and day_i not in (28, 34):
            arr[32:72] = 620.0 + rng.normal(0.0, 12.0, 40)
        p1[sl] = arr

    branch_df = pd.DataFrame({
        "time": idx15.strftime("%Y-%m-%d %H:%M:%S"),
        "p1": np.round(p1, 3),
        "p2": np.round(rng.uniform(0.0, 2.0, n15), 3),
    })
    # Day 21 注入 50% NaN（branch_score=50 < 70，非双达标坏天）
    branch_df.loc[21 * 96:21 * 96 + 47, "p1"] = np.nan
    # Day 22..23 注入 20 个 NaN（branch_score≈79，C 级边缘合格天）
    branch_df.loc[22 * 96:22 * 96 + 19, "p1"] = np.nan
    branch_df.loc[23 * 96:23 * 96 + 19, "p1"] = np.nan

    n5 = 288 * days
    idx5 = pd.date_range("2026-01-01", periods=n5, freq="5min")
    p1_clean = np.nan_to_num(branch_df["p1"].to_numpy(np.float64), nan=12.0)
    p1_5m = np.repeat(p1_clean, 3)
    bg_5m = 180.0 + 25.0 * np.sin(2 * np.pi * (np.arange(n5) % 288) / 288) + rng.normal(0.0, 5.0, n5)
    ptotal_w = np.clip((bg_5m + p1_5m) * 1000.0, 5000.0, None)
    pa = ptotal_w / 3.0
    pb = ptotal_w / 3.0
    pc = ptotal_w - pa - pb
    ua = np.full(n5, 220.0)
    pf = np.full(n5, 0.93)
    bus_df = pd.DataFrame({
        "event_time": idx5.strftime("%Y-%m-%d %H:%M:%S"),
        "load_iden_data0": ua, "load_iden_data1": ua, "load_iden_data2": ua,
        "load_iden_data3": pa / (ua * pf), "load_iden_data4": pb / (ua * pf), "load_iden_data5": pc / (ua * pf),
        "load_iden_data6": pa, "load_iden_data7": pb, "load_iden_data8": pc,
        "load_iden_data9": pf, "load_iden_data10": pf, "load_iden_data11": pf,
    })

    s_code, e_code = idx5[0].strftime("%y%m%d"), idx5[-1].strftime("%y%m%d")
    bus_df.to_csv(d / f"e241_{device}_{user}-Ch1-{s_code}-{e_code}.csv", index=False)
    branch_df.to_csv(d / f"{user}-{s_code}-{e_code}.csv", index=False)
    return d


def test_e2e_auto_split_reconciles_100_percent_with_qualified_days_detail(tmp_path: Path):
    """端到端运行 run_batch(split_strategy='auto') 并与 qualified_days_detail.csv 逐日/汇总严格对账。"""
    ukey = "800080270733_4206673297219"
    data_root = tmp_path / "data"
    out_root = tmp_path / "outputs"
    _write_e2e_quality_user(data_root, ukey)

    tf_cfg = {
        ukey: {
            "target_col": "p1",
            "split_strategy": "auto",
            "on_thr_w": "auto",
            "decision_thr_w": "auto",
            "pbus_mode": "auto",
            "post_min_on": "auto",
            "post_fill_short_off": "auto",
            "quality": {"day_gate": True, "min_days": 10, "min_on_day_ratio": 0.2},
        }
    }
    import yaml
    from tests.conftest import FIELD_MAP
    base_cfg = yaml.safe_load(Path("configs/default.yaml").read_text(encoding="utf-8"))
    base_cfg["bus_field_map"] = FIELD_MAP
    base_cfg["models"] = [
        {"name": "proportional", "params": {}},
        {"name": "ridge", "params": {"alpha": 1.0}},
    ]

    tf_path = tmp_path / "tf.json"
    base_path = tmp_path / "base.yaml"
    tf_path.write_text(json.dumps(tf_cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    base_path.write_text(yaml.safe_dump(base_cfg, allow_unicode=True), encoding="utf-8")

    batch_res = run_batch(
        time_filter_config_path=tf_path,
        base_config_path=base_path,
        data_root=data_root,
        output_root=out_root,
        user_keys=[ukey],
        stages=("train",),
        force=True,
    )
    assert batch_res["rows"][0].status == Status.OK

    tr_dir = latest_done_dir(out_root / ukey / "train")
    assert tr_dir is not None

    meta = json.loads((tr_dir / "meta.json").read_text(encoding="utf-8"))
    daily_q = pd.read_csv(tr_dir / "daily_quality.csv")
    q_detail = pd.read_csv(tr_dir / "qualified_days_detail.csv")
    q_advice = json.loads((tr_dir / "quality_advice.json").read_text(encoding="utf-8"))["advice"]
    pred_df = pd.read_csv(tr_dir / "predictions" / "train_predictions.csv")

    # 1. Day 21 (2026-01-22) 非双达标坏天已被剔除，其余 35 天全部进入 qualified_days_detail.csv
    qual_dates = sorted(daily_q.loc[daily_q["qualified"] == 1, "date"].tolist())
    assert "2026-01-22" not in qual_dates
    assert len(qual_dates) == 35
    assert sorted(q_detail["date"].tolist()) == qual_dates

    # 2. qualified_days_detail.csv 与 meta.json.quality.branch.qualified_days_stats 100% 守恒对账
    q_stats = meta["quality"]["branch"]["qualified_days_stats"]
    recomputed_stats = qualified_days_summary(q_detail)
    assert q_stats == recomputed_stats
    assert (
        q_stats["total_days"]
        == q_stats["train_days"] + q_stats["val_days"] + q_stats["test_days"] + q_stats["unused_days"]
    )
    # 远期过剩全关天被自动裁剪并透明记入「未使用」
    sdec = meta["split_decision"]
    trimmed_off = sdec["pool_curation"]["trimmed_excess_off_days"]
    assert len(trimmed_off) > 0
    assert q_stats["unused_days"] == len(trimmed_off)
    unused_in_csv = sorted(q_detail.loc[q_detail["dataset"] == "未使用", "date"].tolist())
    assert unused_in_csv == sorted(trimmed_off)
    # 所有被裁全关天在 qualified_days_detail.csv 中忠实标注 all_off == 1
    assert (q_detail.loc[q_detail["dataset"] == "未使用", "all_off"] == 1).all()

    # 3. train_predictions.csv 逐日归属与 qualified_days_detail.csv 100% 一一对应，零跨集混日
    pred_df["date"] = pd.to_datetime(pred_df["timestamp"]).dt.strftime("%Y-%m-%d")
    label_map = {"train": "训练集", "val": "验证集", "test": "测试集"}
    for split_en, split_zh in label_map.items():
        pred_dates = sorted(pred_df.loc[pred_df["split"] == split_en, "date"].unique().tolist())
        detail_dates = sorted(q_detail.loc[q_detail["dataset"] == split_zh, "date"].tolist())
        assert pred_dates == detail_dates
        assert len(pred_dates) == sdec["by_split"][split_en]["days"]

    # 4. 三折全部具备开机日与全关日双覆盖，split_coverage_advice 零失衡告警
    for split_en in ("train", "val", "test"):
        assert sdec["by_split"][split_en]["on_days"] >= 1
        assert sdec["by_split"][split_en]["off_days"] >= 1
    assert not any("不含" in msg and "全关天" in msg for msg in q_advice)

    # 5. C 级低分天 (Day 22, 23 -> 2026-01-23, 2026-01-24) 留在训练集，val/test 全为高分 A 级连续块
    assert set(
        q_detail.loc[q_detail["date"].isin(["2026-01-23", "2026-01-24"]), "dataset"]
    ) == {"训练集"}
    assert sdec["by_split"]["val"]["mean_min_side_score"] >= 95.0
    assert sdec["by_split"]["test"]["mean_min_side_score"] >= 95.0

    # 6. L-3 分层因果前缀特征隔离与 L-4 on_thr_w 零测试泄漏标记均已生效
    assert sdec["per_split_feature_isolation"] is True
    assert sdec["feature_isolation_mode"] == "causal_prefix_train_val_test"
    assert meta["on_thr_w_decision"]["zero_test_leakage_verified"] is True

    # 7. 验证 auto_run_steps2to5 对 --split-strategy auto 的原 JSON 写回门禁（仅 --force + --save-config 才更新）
    orig_tf = tmp_path / "time_filters_orig.json"
    orig_tf.write_text(json.dumps({
        ukey: {"target_col": "p1", "split_strategy": "stratified_day", "on_thr_w": 50.0}
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    ov = ensure_user_config(ukey, orig_tf, split_strategy="auto")
    assert ov != orig_tf
    # 仅 --save-config 未传 --force：不更新原 JSON 中的 split_strategy
    step_sync_decision_thr(
        user_key=ukey, out_root=out_root, effective_tf=ov, orig_tf=orig_tf,
        stage="train", split_strategy_arg="auto", save_config=True, force=False,
        ran_train=True, fresh_train_done=True,
    )
    assert json.loads(orig_tf.read_text(encoding="utf-8"))[ukey]["split_strategy"] == "stratified_day"
    # 同时传 --force + --save-config：同步更新 split_strategy="auto" 到原 JSON
    step_sync_decision_thr(
        user_key=ukey, out_root=out_root, effective_tf=ov, orig_tf=orig_tf,
        stage="train", split_strategy_arg="auto", save_config=True, force=True,
        ran_train=True, fresh_train_done=True,
    )
    assert json.loads(orig_tf.read_text(encoding="utf-8"))[ukey]["split_strategy"] == "auto"


def test_auto_split_733_outage_gap_and_post_outage_recovery_day():
    """复刻 733 户真实 39 天日历（06-13~14 断口 + 06-15 停电恢复仅 58 点 + 3744 重采样网格含 148 个 NaN）：
    验证 valid_mask 准确识别残缺天、val 绝不跨离线断口吞入 06-12 孤岛或 06-15 恢复日，切出干净单段 [06-16..06-22]。
    """
    exact_pts_733 = {
        "2026-04-19": 91,
        "2026-05-21": 91, "2026-05-22": 96, "2026-05-23": 96, "2026-05-24": 96,
        "2026-05-25": 95, "2026-05-26": 90, "2026-05-27": 96, "2026-05-28": 96,
        "2026-05-29": 96, "2026-05-30": 96, "2026-05-31": 96, "2026-06-01": 96,
        "2026-06-02": 82, "2026-06-03": 95, "2026-06-04": 96, "2026-06-05": 95,
        "2026-06-06": 96, "2026-06-07": 96, "2026-06-08": 96, "2026-06-09": 96,
        "2026-06-10": 96, "2026-06-11": 94, "2026-06-12": 96,
        "2026-06-15": 58,
        "2026-06-16": 96, "2026-06-17": 96, "2026-06-18": 96, "2026-06-19": 96,
        "2026-06-20": 96, "2026-06-21": 96, "2026-06-22": 96,
        "2026-06-23": 96, "2026-06-24": 65, "2026-06-25": 52, "2026-06-26": 96,
        "2026-06-27": 96, "2026-06-28": 96, "2026-06-29": 96,
    }
    grid_parts = [pd.date_range(d, periods=96, freq="15min") for d in exact_pts_733]
    grid_idx = grid_parts[0].append(grid_parts[1:])
    valid_mask = np.zeros(len(grid_idx), dtype=bool)
    offset = 0
    for _, k in exact_pts_733.items():
        valid_mask[offset + (96 - k): offset + 96] = True
        offset += 96
    assert valid_mask.sum() == 3596

    target = pd.Series(560.0, index=grid_idx)
    target.loc[grid_idx.normalize() == pd.Timestamp("2026-06-08")] = 2.0
    dq = pd.DataFrame({
        "date": list(exact_pts_733.keys()),
        "bus_score": [99.4 if k >= 90 else (92.0 if k >= 60 else 86.0) for k in exact_pts_733.values()],
        "branch_score": [99.4 if k >= 90 else (92.0 if k >= 60 else 86.0) for k in exact_pts_733.values()],
        "qualified": [1] * 39,
    })

    prof = build_day_quality_profiles(grid_idx, dq, target, 10.0, valid_mask=valid_mask)
    prof_by_date = prof.set_index("date")
    assert prof_by_date.loc["2026-06-15", "n_points"] == 58
    assert prof_by_date.loc["2026-06-15", "tier"] == "C"
    assert prof_by_date.loc["2026-06-25", "tier"] == "C"

    masks, meta = resolve_auto_quality_split(
        grid_idx, [0.6, 0.2, 0.2], daily_quality=dq, target=target, on_thr_w=10.0, valid_mask=valid_mask
    )
    _check(masks)
    clean_idx = grid_idx[valid_mask]
    clean_masks = {k: pd.Series(v.to_numpy()[valid_mask], index=clean_idx) for k, v in masks.items()}
    summary = summarize_split_decision(clean_idx, clean_masks, "auto", dq, target.loc[valid_mask], 10.0, auto_meta=meta)

    assert meta["internal_gaps"] == 0
    assert meta["induced_islands"] == 0
    assert summary["by_split"]["train"]["days"] == 25
    assert summary["by_split"]["val"]["days"] == 7
    assert summary["by_split"]["val"]["points"] == 672
    assert summary["by_split"]["val"]["contiguous_blocks"] == 1
    assert summary["by_split"]["val"]["tier_c_days"] == 0
    assert summary["by_split"]["test"]["days"] == 7
    assert summary["by_split"]["test"]["points"] == 597
    assert summary["by_split"]["test"]["contiguous_blocks"] == 1

    va_dates = sorted(pd.Series(clean_idx[clean_masks["val"].to_numpy()].normalize()).dt.strftime("%Y-%m-%d").unique())
    te_dates = sorted(pd.Series(clean_idx[clean_masks["test"].to_numpy()].normalize()).dt.strftime("%Y-%m-%d").unique())
    assert va_dates == [f"2026-06-{d:02d}" for d in range(16, 23)]
    assert te_dates == [f"2026-06-{d:02d}" for d in range(23, 30)]


def test_causal_prefix_isolation_keeps_boundary_points_and_blocks_test_to_train_leakage(tmp_path: Path):
    """验证分层因果前缀隔离：
    1) 时序衔接边界（train->val、val->test）首日保留完整 96 点，不因独立滞后白丢 5 点；
    2) 即便人工将早期的 2026-01-05 锚定为 test，篡改该 test 日母线功率对 train/val 预测与训练结果影响严格为 0。
    """
    import yaml
    from tests.conftest import FIELD_MAP

    ukey = "800080270733_4206673297219"
    base_cfg = yaml.safe_load(Path("configs/default.yaml").read_text(encoding="utf-8"))
    base_cfg["bus_field_map"] = FIELD_MAP
    base_cfg["models"] = [{"name": "ridge", "params": {"alpha": 1.0}}]
    base_path = tmp_path / "base.yaml"
    base_path.write_text(yaml.safe_dump(base_cfg, allow_unicode=True), encoding="utf-8")

    # 1) 验证连续切分边界首日满 96 点
    d1 = tmp_path / "d1"
    o1 = tmp_path / "o1"
    _write_e2e_quality_user(d1, ukey)
    tf1 = tmp_path / "tf1.json"
    tf1.write_text(json.dumps({ukey: {"target_col": "p1", "split_strategy": "auto"}}, indent=2), encoding="utf-8")
    run_batch(tf1, base_path, d1, o1, user_keys=[ukey], stages=("train",), force=True)
    tr1 = latest_done_dir(o1 / ukey / "train")
    pred1 = pd.read_csv(tr1 / "predictions" / "train_predictions.csv")
    pred1["date"] = pd.to_datetime(pred1["timestamp"]).dt.strftime("%Y-%m-%d")
    for sp in ("val", "test"):
        first_day = sorted(pred1.loc[pred1["split"] == sp, "date"].unique())[0]
        assert (pred1["date"] == first_day).sum() == 96

    # 2) 验证前置锚定 test 日的剧烈扰动绝不反向泄漏进 train 或 val
    d2a, d2b = tmp_path / "d2a", tmp_path / "d2b"
    o2a, o2b = tmp_path / "o2a", tmp_path / "o2b"
    u_dir_a = _write_e2e_quality_user(d2a, ukey)
    u_dir_b = _write_e2e_quality_user(d2b, ukey)
    # 在 d2b 中对被锚定为 test 的 2026-01-26 当天母线功率注入 50 倍剧烈扰动
    bus_csv_b = next(u_dir_b.glob("e241_*.csv"))
    bdf = pd.read_csv(bus_csv_b)
    m_test_day = bdf["event_time"].str.startswith("2026-01-26")
    for col in ("load_iden_data6", "load_iden_data7", "load_iden_data8"):
        bdf.loc[m_test_day, col] = bdf.loc[m_test_day, col] * 50.0
    bdf.to_csv(bus_csv_b, index=False)

    tf2 = tmp_path / "tf2.json"
    tf2.write_text(json.dumps({
        ukey: {
            "target_col": "p1",
            "split_strategy": "auto",
            "splits": {"test": {"include": [["2026-01-26", "2026-01-26"]]}},
        }
    }, indent=2), encoding="utf-8")
    run_batch(tf2, base_path, d2a, o2a, user_keys=[ukey], stages=("train",), force=True)
    run_batch(tf2, base_path, d2b, o2b, user_keys=[ukey], stages=("train",), force=True)

    pa = pd.read_csv(latest_done_dir(o2a / ukey / "train") / "predictions" / "train_predictions.csv")
    pb = pd.read_csv(latest_done_dir(o2b / ukey / "train") / "predictions" / "train_predictions.csv")
    for sp in ("train", "val"):
        sa = pa.loc[pa["split"] == sp, "pred_ridge"].to_numpy()
        sb = pb.loc[pb["split"] == sp, "pred_ridge"].to_numpy()
        np.testing.assert_allclose(sa, sb, rtol=0.0, atol=1e-12)


def test_auto_split_2844_cross_epoch_no_inversion_and_recent_on_coverage():
    """回归 2844 户真实 45 天跨年双时期日历（2025-07 全开 18 天 + 294 天跨年大断口 + 2026-05~06 含 4 处断口的 27 天）：
    1) 严禁将 test 倒置在 val/train 之前（杜绝旧版 test=[05-21..05-29] 三明治倒置抽空近期黄金训练段）；
    2) train 严格早于 val、val 严格早于 test（max(train) < min(val) < min(test)）；
    3) train 完整保留 2026 年 5 月全部 6 个连续开机日（05-21, 05-23..05-27），96 点有效滑窗 >= 2100 且 M_max=8；
    4) test 贴合 06-12 / 06-17 天然断口落在末尾 [06-17..06-28]，三折均具备 >=2 开 + >=2 关双覆盖。
    """
    from nilm.pipeline.auto_thresholds import resolve_postprocess_physical_bounds
    from nilm.preprocess.dataset import build_windows

    raw_2844 = [
        ("2025-07-11", True, 91), ("2025-07-12", True, 96), ("2025-07-13", True, 96),
        ("2025-07-14", True, 96), ("2025-07-15", True, 96),
        ("2025-07-17", True, 96), ("2025-07-18", True, 96), ("2025-07-19", True, 96),
        ("2025-07-20", True, 96), ("2025-07-21", True, 96), ("2025-07-22", True, 96),
        ("2025-07-23", True, 96), ("2025-07-24", True, 96), ("2025-07-25", True, 96),
        ("2025-07-26", True, 96), ("2025-07-27", True, 96), ("2025-07-28", True, 96),
        ("2025-07-30", True, 96),
        ("2026-05-21", True, 96), ("2026-05-22", False, 96), ("2026-05-23", True, 96),
        ("2026-05-24", True, 96), ("2026-05-25", True, 96), ("2026-05-26", True, 96),
        ("2026-05-27", True, 96), ("2026-05-28", False, 96), ("2026-05-29", False, 96),
        ("2026-05-30", False, 96), ("2026-05-31", False, 96), ("2026-06-01", True, 96),
        ("2026-06-02", False, 96), ("2026-06-03", False, 96), ("2026-06-04", False, 96),
        ("2026-06-08", False, 95),
        ("2026-06-12", True, 96),
        ("2026-06-17", False, 96), ("2026-06-18", False, 96), ("2026-06-19", True, 96),
        ("2026-06-20", True, 96), ("2026-06-21", False, 96), ("2026-06-22", True, 96),
        ("2026-06-23", True, 96),
        ("2026-06-26", False, 96), ("2026-06-27", True, 96), ("2026-06-28", True, 96),
    ]
    parts = [
        pd.date_range(pd.Timestamp(d) + pd.Timedelta(minutes=15 * (96 - pts)), periods=pts, freq="15min")
        for d, _, pts in raw_2844
    ]
    idx = parts[0].append(parts[1:])
    assert len(idx) == 4314

    tgt = pd.Series(2.0, index=idx)
    for d, on, _ in raw_2844:
        if on:
            d_mask = idx.normalize() == pd.Timestamp(d)
            # 模拟真实日内长班次开机（每日 32:80 共 48 个点 = 12 小时稳态运行，支持 M_max=8）
            pos = np.flatnonzero(d_mask)
            tgt.iloc[pos[24:min(len(pos), 80)]] = 620.0

    masks, meta = resolve_auto_quality_split(idx, [0.6, 0.2, 0.2], target=tgt, on_thr_w=20.0)
    _check(masks)
    summary = summarize_split_decision(idx, masks, "auto", target=tgt, on_thr_w=20.0, auto_meta=meta)

    assert meta["resolved_mode"] == "auto_contiguous_quality_blocks"
    assert meta["chronological_order"] is True
    assert meta["recent_cover_ok"] is True
    assert meta["recent_epoch_start_idx"] == 18
    assert meta["induced_islands"] == 0

    tr_idx = idx[masks["train"].to_numpy()]
    va_idx = idx[masks["val"].to_numpy()]
    te_idx = idx[masks["test"].to_numpy()]
    assert tr_idx.max() < va_idx.min()
    assert va_idx.max() < te_idx.min()

    tr_dates = set(pd.Series(tr_idx.normalize()).dt.strftime("%Y-%m-%d").unique())
    for must_train_day in ("2026-05-21", "2026-05-23", "2026-05-24", "2026-05-25", "2026-05-26", "2026-05-27"):
        assert must_train_day in tr_dates
    te_dates = sorted(pd.Series(te_idx.normalize()).dt.strftime("%Y-%m-%d").unique())
    assert te_dates[0] == "2026-06-17" and te_dates[-1] == "2026-06-28"

    for sp in ("train", "val", "test"):
        assert summary["by_split"][sp]["on_days"] >= 2
        assert summary["by_split"][sp]["off_days"] >= 2

    Xw, _, _ = build_windows(np.zeros((len(tr_idx), 2), np.float32),
                             np.zeros((len(tr_idx), 1), np.float32),
                             tr_idx, window=96, mode="seq2point")
    assert len(Xw) >= 2100
    bounds = resolve_postprocess_physical_bounds(tgt.loc[tr_idx].to_numpy(), tr_idx, on_thr_w=20.0)
    assert bounds["m_max"] == 8


