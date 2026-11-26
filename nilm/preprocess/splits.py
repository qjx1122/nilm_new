"""数据集划分（指南 §11/§12.3/§12.4）：按日粒度切分，不随机打散时间点。

策略语义（§12.3 六种，工程解释记录于 STATUS.md 与 docs/AUTO_QUALITY_SPLIT_ANALYSIS.md）：
- auto                  : 基于每日数据质量（bus_score/branch_score、有效点数、天然离线断口）与
                          日开/关工况，全局搜索连续高质量块并保障 train/val/test 开/关双覆盖；
- time                  : 按时间顺序连续切分（前→后 = train→val→test）；
- stratified_day        : 按「星期几」分层，每个星期层内部按时间顺序切比例（硬编码缺省）；
- stratified            : 按「月份」分层，每个月层内部按时间顺序切比例；
- global_stratified     : 按日轮转交错分配（最大散布，保持日内连续）；
- stratified_by_state   : 按「日开/关状态」分层（on/off，阈值 on_thr_w），每类内部按时间顺序切比例，
                          使 train/val/test 的关占比≈池均，解决 B1 65%vs0% 池内失衡（2026-09-17）。

切分顺序（§12.4）：初始策略切分 → include 硬锚定 → 比例修复 → exclude 精确排除。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from nilm.common.timefilter import anchor_splits, apply_time_filter

SPLIT_NAMES = ("train", "val", "test")
POINTS_PER_DAY_15MIN = 96
STUB_DAY_MIN_POINTS = 16


def _days(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    if len(index) == 0:
        return []
    return sorted(pd.Series(index.normalize()).unique())


def _ratio_assign(days: list[pd.Timestamp], ratios: list[float]) -> dict[pd.Timestamp, str]:
    """把一段有序日期按比例切成 train/val/test（日粒度）。"""
    n = len(days)
    i1 = int(round(n * ratios[0]))
    i2 = int(round(n * (ratios[0] + ratios[1])))
    out: dict[pd.Timestamp, str] = {}
    for i, d in enumerate(days):
        out[d] = "train" if i < i1 else ("val" if i < i2 else "test")
    return out


def _daily_on_off(index: pd.DatetimeIndex, target: pd.Series | None,
                  on_thr_w: float | None) -> dict[pd.Timestamp, bool] | None:
    """计算逐日开/关状态（日峰值≥on_thr_w 为开），用于 stratified_by_state 与 auto。

    返回 {date_normalized: is_on}，缺 target/on_thr 时返回 None。
    """
    if target is None or on_thr_w is None:
        return None
    try:
        daily_max = target.groupby(target.index.normalize()).max()
        out: dict[pd.Timestamp, bool] = {}
        for d in _days(index):
            v = daily_max.get(d)
            if pd.isna(v):
                out[d] = False
            else:
                out[d] = bool(v >= float(on_thr_w))
        return out
    except Exception:
        return None


def _anchored_include_days(index: pd.DatetimeIndex,
                           splits_spec: dict | None) -> set[pd.Timestamp]:
    """提取 splits.{train,val,test}.include 显式锚定的自然日集合（池裁剪不可触碰）。"""
    if not splits_spec or len(index) == 0:
        return set()
    protected: set[pd.Timestamp] = set()
    for name in SPLIT_NAMES:
        spec = splits_spec.get(name) or {}
        if "include" in spec and spec["include"]:
            m = apply_time_filter(index, spec["include"], None)
            if m.any():
                protected.update(index[m.to_numpy()].normalize().unique())
    return protected


def build_day_quality_profiles(
    index: pd.DatetimeIndex,
    daily_quality: pd.DataFrame | None = None,
    target: pd.Series | None = None,
    on_thr_w: float | None = None,
    valid_mask: pd.Series | np.ndarray | None = None,
) -> pd.DataFrame:
    """构建逐自然日「质量-连续块-工况」画像表，供 split_strategy='auto' 与审计落盘共用。

    当传入 valid_mask（去除了原始缺数及滞后 NaN 后的布尔有效掩码）时，n_points 与
    effective_score 按真实有效样本点统计，避免重采样网格上的 NaN 空行掩盖半天掉线（如 733 户 06-15 仅 58 点）。
    """
    days = _days(index)
    cols = [
        "date", "day_ts", "n_points", "bus_score", "branch_score",
        "min_side_score", "effective_score", "mean_score", "tier", "is_on",
        "gap_before", "gap_after", "block_id", "block_len", "is_island",
        "intraday_gaps", "head_run", "tail_run", "mid_windows", "stream_link_prev",
    ]
    if not days:
        return pd.DataFrame(columns=cols)

    norm_idx = index.normalize()
    if valid_mask is not None:
        vm = np.asarray(valid_mask, dtype=bool)
        if len(vm) != len(index):
            raise ValueError(f"valid_mask 长度 {len(vm)} 与 index 长度 {len(index)} 不一致")
        pt_counts = pd.Series(vm.astype(np.int64), index=norm_idx).groupby(level=0).sum().to_dict()
        eff_idx = index[vm]
    else:
        pt_counts = pd.Series(1, index=norm_idx).groupby(level=0).sum().to_dict()
        eff_idx = index

    # 统计每日内部 >15min 缺数断口（intraday_gaps）及跨午夜 15min 连续衔接性（stream_link_prev），
    # 避免切分器只看跨日断口而把含日内缺口的碎片日全部挤压进 train 造成滑窗坍塌（如 724 户 06-05/07/09）。
    # 注：使用 Timedelta 比较而非裸 asi8，兼容 pandas 2.x (ns) 与 pandas 3.x (s/us/ms) 任意时间精度。
    step_td = pd.Timedelta(minutes=15)
    w_minus_1 = POINTS_PER_DAY_15MIN - 1
    day_stream_stats: dict[pd.Timestamp, tuple[int, int, int, int, pd.Timestamp | None, pd.Timestamp | None]] = {}
    if len(eff_idx) > 0:
        eff_sorted = eff_idx.sort_values()
        eff_norm_sorted = eff_sorted.normalize()
        change_pos = np.flatnonzero(eff_norm_sorted[1:] != eff_norm_sorted[:-1]) + 1
        starts = np.concatenate([[0], change_pos])
        ends = np.concatenate([change_pos, [len(eff_sorted)]])
        for s_i, e_i in zip(starts, ends):
            d_key = eff_norm_sorted[s_i]
            ts_sub = eff_sorted[s_i:e_i]
            if len(ts_sub) == 1:
                day_stream_stats[d_key] = (0, 1, 1, 0, ts_sub[0], ts_sub[0])
            else:
                brk = np.flatnonzero((ts_sub[1:] - ts_sub[:-1]) != step_td) + 1
                if len(brk) == 0:
                    k_len = int(len(ts_sub))
                    day_stream_stats[d_key] = (0, k_len, k_len, 0, ts_sub[0], ts_sub[-1])
                else:
                    seg_starts = np.concatenate([[0], brk])
                    seg_ends = np.concatenate([brk, [len(ts_sub)]])
                    seg_lens = seg_ends - seg_starts
                    mid_w = int(sum(max(0, int(L) - w_minus_1) for L in seg_lens[1:-1]))
                    day_stream_stats[d_key] = (
                        int(len(brk)),
                        int(seg_lens[0]),
                        int(seg_lens[-1]),
                        mid_w,
                        ts_sub[0],
                        ts_sub[-1],
                    )

    dq_map: dict[str, tuple[float, float]] = {}
    if daily_quality is not None and len(daily_quality) > 0 and "date" in daily_quality.columns:
        dq_dates = pd.to_datetime(daily_quality["date"]).dt.strftime("%Y-%m-%d")
        bus_s = pd.to_numeric(daily_quality.get("bus_score", 100.0), errors="coerce").fillna(100.0)
        br_s = pd.to_numeric(daily_quality.get("branch_score", 100.0), errors="coerce").fillna(100.0)
        for d_str, bs, brs in zip(dq_dates, bus_s, br_s):
            dq_map[str(d_str)] = (float(bs), float(brs))

    state_map = _daily_on_off(index, target, on_thr_w)

    n = len(days)
    gap_before = [
        (i == 0) or ((days[i] - days[i - 1]).days > 1)
        for i in range(n)
    ]
    gap_after = [
        (i == n - 1) or ((days[i + 1] - days[i]).days > 1)
        for i in range(n)
    ]

    block_ids: list[int] = []
    cur_block = -1
    for gb in gap_before:
        if gb:
            cur_block += 1
        block_ids.append(cur_block)
    block_sizes: dict[int, int] = {}
    for bid in block_ids:
        block_sizes[bid] = block_sizes.get(bid, 0) + 1

    rows: list[dict] = []
    prev_last_ts: pd.Timestamp | None = None
    for i, d in enumerate(days):
        d_str = d.strftime("%Y-%m-%d")
        n_pts = int(pt_counts.get(d, 0))
        inferred = float(np.clip(100.0 * n_pts / POINTS_PER_DAY_15MIN, 0.0, 100.0))
        if d_str in dq_map:
            bs, brs = dq_map[d_str]
        else:
            bs, brs = inferred, inferred
        min_s = float(min(bs, brs))
        eff_s = float(min(min_s, inferred))
        mean_s = float(0.5 * (bs + brs))
        if min_s >= 95.0 and n_pts >= 88:
            tier = "A"
        elif min_s >= 80.0 and n_pts >= 64:
            tier = "B"
        else:
            tier = "C"
        blen = block_sizes[block_ids[i]]
        intra_g, h_run, t_run, mid_w, first_ts, last_ts = day_stream_stats.get(
            d, (0, 0, 0, 0, None, None)
        )
        link_prev = bool(
            i > 0
            and (not gap_before[i])
            and first_ts is not None
            and prev_last_ts is not None
            and (first_ts - prev_last_ts) == step_td
        )
        prev_last_ts = last_ts
        rows.append({
            "date": d_str,
            "day_ts": d,
            "n_points": n_pts,
            "bus_score": round(bs, 2),
            "branch_score": round(brs, 2),
            "min_side_score": round(min_s, 2),
            "effective_score": round(eff_s, 2),
            "mean_score": round(mean_s, 2),
            "tier": tier,
            "is_on": None if state_map is None else bool(state_map.get(d, False)),
            "gap_before": bool(gap_before[i]),
            "gap_after": bool(gap_after[i]),
            "block_id": int(block_ids[i]),
            "block_len": int(blen),
            "is_island": bool(blen == 1),
            "intraday_gaps": int(intra_g),
            "head_run": int(h_run),
            "tail_run": int(t_run),
            "mid_windows": int(mid_w),
            "stream_link_prev": link_prev,
        })
    return pd.DataFrame(rows, columns=cols)


def curate_auto_split_pool(
    bus: pd.DataFrame,
    branch: pd.DataFrame,
    target: pd.Series,
    daily_quality: pd.DataFrame | None = None,
    on_thr_w: float | None = None,
    splits_spec: dict | None = None,
    min_days: float = 14.0,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series, dict]:
    """split_strategy='auto' 前置池级净水：
    1) 剥离有效对齐点数 < 16（不足 4 小时）的极短残片天（非人工锚定且样本余量充足时）；
    2) 当双达标总天数 >= 25、开机日 >= 8 且全关日占比 > 65% 时，从最早（远期）连续全关段起
       自动裁剪冗余全关天，将全关占比收敛至 ~45%，同时完整保留近期全关天供 val/test 使用。
    """
    norm_idx = bus.index.normalize()
    all_days = sorted(pd.Series(norm_idx).unique())
    protected = _anchored_include_days(bus.index, splits_spec)
    min_rows_floor = int(np.ceil(POINTS_PER_DAY_15MIN * float(min_days)))

    valid_row = (~bus.isna().any(axis=1)) & (~target.isna())
    valid_by_day = valid_row.groupby(norm_idx).sum().to_dict()

    state_before = _daily_on_off(bus.index, target, on_thr_w)
    before_days = len(all_days)
    before_off = (sum(1 for d in all_days if not state_before[d])
                  if state_before is not None else None)
    before_off_ratio = (round(before_off / before_days, 4)
                        if (before_off is not None and before_days > 0) else None)

    drop_stub_days: list[pd.Timestamp] = []
    cur_rows = len(bus)
    cur_day_set = set(all_days)

    stub_candidates = sorted(
        [d for d in all_days if d not in protected and int(valid_by_day.get(d, 0)) < STUB_DAY_MIN_POINTS],
        key=lambda d: (int(valid_by_day.get(d, 0)), d),
    )
    row_counts = pd.Series(1, index=norm_idx).groupby(level=0).sum().to_dict()
    for d in stub_candidates:
        d_rows = int(row_counts.get(d, 0))
        if (cur_rows - d_rows) >= min_rows_floor and (len(cur_day_set) - 1) >= 4:
            drop_stub_days.append(d)
            cur_day_set.remove(d)
            cur_rows -= d_rows

    drop_excess_off_days: list[pd.Timestamp] = []
    rem_days = sorted(cur_day_set)
    if state_before is not None and len(rem_days) >= 25:
        on_days = [d for d in rem_days if state_before.get(d, False)]
        off_days = [d for d in rem_days if not state_before.get(d, False)]
        n_rem = len(rem_days)
        n_on = len(on_days)
        n_off = len(off_days)
        if n_on >= 8 and n_off / n_rem > 0.65:
            target_off = max(6, int(round(n_on * (0.45 / 0.55))))
            excess = n_off - target_off
            if excess >= 2:
                # 保护最近期的一批全关天（至少 4 天），优先从最早的连续全关块按时间从旧到新裁剪
                keep_recent_off = set(off_days[-max(4, int(round(0.35 * target_off))):])
                off_set = set(off_days)
                trimmable = [
                    d for d in off_days
                    if d not in protected and d not in keep_recent_off
                ]
                # 连续停机长段中的远期全关天优先于开机日旁边的零星周末关天
                def _off_trim_priority(d: pd.Timestamp) -> tuple[int, pd.Timestamp]:
                    prev_off = (d - pd.Timedelta(days=1)) in off_set
                    next_off = (d + pd.Timedelta(days=1)) in off_set
                    in_off_run = 0 if (prev_off or next_off) else 1
                    return (in_off_run, d)

                for d in sorted(trimmable, key=_off_trim_priority):
                    if len(drop_excess_off_days) >= excess:
                        break
                    d_rows = int(row_counts.get(d, 0))
                    if (cur_rows - d_rows) < max(min_rows_floor, POINTS_PER_DAY_15MIN * 14):
                        break
                    if (len(cur_day_set) - 1) < 20:
                        break
                    drop_excess_off_days.append(d)
                    cur_day_set.remove(d)
                    cur_rows -= d_rows

    dropped_all = set(drop_stub_days) | set(drop_excess_off_days)
    if dropped_all:
        keep_mask = ~norm_idx.isin(dropped_all)
        bus_cur = bus.loc[keep_mask]
        branch_cur = branch.loc[keep_mask]
        target_cur = target.loc[keep_mask]
    else:
        bus_cur, branch_cur, target_cur = bus, branch, target

    final_days = sorted(cur_day_set)
    after_off = (sum(1 for d in final_days if not state_before[d])
                 if state_before is not None else None)
    after_off_ratio = (round(after_off / len(final_days), 4)
                       if (after_off is not None and len(final_days) > 0) else None)

    info = {
        "before_days": int(before_days),
        "after_days": int(len(final_days)),
        "before_off_ratio": before_off_ratio,
        "after_off_ratio": after_off_ratio,
        "trimmed_stub_days": sorted(d.strftime("%Y-%m-%d") for d in drop_stub_days),
        "trimmed_excess_off_days": sorted(d.strftime("%Y-%m-%d") for d in drop_excess_off_days),
    }
    return bus_cur, branch_cur, target_cur, info


def _compute_day_quotas(n_days: int, ratios: list[float]) -> tuple[int, int, int]:
    """按比例计算 (n_train, n_val, n_test) 整日配额，兼顾小样本下界保护。"""
    if n_days <= 0:
        return 0, 0, 0
    if n_days < 3:
        assign = _ratio_assign([pd.Timestamp("2025-01-01") + pd.Timedelta(days=i) for i in range(n_days)], ratios)
        vals = list(assign.values())
        return vals.count("train"), vals.count("val"), vals.count("test")

    r_tr, r_va, r_te = float(ratios[0]), float(ratios[1]), float(ratios[2])
    n_te = max(1, int(round(n_days * r_te))) if r_te > 0 else 0
    n_va = max(1, int(round(n_days * r_va))) if r_va > 0 else 0
    min_tr = 2 if (n_days >= 4 and r_tr >= 0.4) else (1 if r_tr > 0 else 0)
    while (n_days - n_va - n_te) < min_tr and (n_va + n_te) > 0:
        if n_va >= n_te and n_va > (1 if r_va > 0 and n_days >= 3 else 0):
            n_va -= 1
        elif n_te > (1 if r_te > 0 and n_days >= 3 else 0):
            n_te -= 1
        elif n_va > 0:
            n_va -= 1
        elif n_te > 0:
            n_te -= 1
        else:
            break
    n_tr = n_days - n_va - n_te
    return n_tr, n_va, n_te


def _compute_state_floors(
    total_on: int,
    total_off: int,
    n_tr: int,
    n_va: int,
    n_te: int,
) -> tuple[tuple[int, int, int], tuple[int, int, int], str]:
    """根据池内开/关日稀缺度与各折天数容量，计算 (req_on, req_off, off_scarcity_tag)。

    关键物理约束：单日容量 (n_s == 1) 的折不可能同时容纳 1 开 + 1 关，此时优先保开机日
    以使分类/回归评估有正样本，将稀缺全关日留给容量 >= 2 的折（首先保 train，其次 val）。
    """
    # 先确定开机日底线
    if total_on <= 0:
        req_on = [0, 0, 0]
    elif total_on == 1:
        req_on = [1 if n_tr >= 1 else 0, 0, 0]
    elif total_on == 2:
        if n_tr >= 1 and n_va >= 1:
            req_on = [1, 1, 0]
        elif n_tr >= 1 and n_te >= 1:
            req_on = [1, 0, 1]
        else:
            req_on = [min(2, n_tr), 0, 0]
    else:
        req_on = [1 if n_tr >= 1 else 0, 1 if n_va >= 1 else 0, 1 if n_te >= 1 else 0]

    rem_cap = [n_tr - req_on[0], n_va - req_on[1], n_te - req_on[2]]

    if total_off <= 0:
        req_off = [0, 0, 0]
        scarcity = "zero_off_days"
    elif total_off == 1:
        if rem_cap[0] >= 1:
            req_off = [1, 0, 0]
        elif n_tr >= 1 and total_on >= 2 and (n_va + n_te) >= 1:
            # 极小样本且唯一 1 个关天必须留 train
            req_off = [1, 0, 0]
            if req_on[0] + req_off[0] > n_tr:
                req_on[0] = max(0, n_tr - 1)
        else:
            req_off = [0, 0, 0]
        scarcity = "single_off_day_reserved_for_train"
    elif total_off == 2:
        req_off = [0, 0, 0]
        left = 2
        for idx in (0, 1, 2):
            if left > 0 and rem_cap[idx] >= 1:
                req_off[idx] = 1
                left -= 1
        scarcity = "two_off_days_train_val"
    else:
        req_off = [
            1 if rem_cap[0] >= 1 else 0,
            1 if rem_cap[1] >= 1 else 0,
            1 if rem_cap[2] >= 1 else 0,
        ]
        scarcity = "sufficient" if sum(req_off) == sum(1 for q in (n_tr, n_va, n_te) if q >= 2) else "capacity_limited"

    return (req_on[0], req_on[1], req_on[2]), (req_off[0], req_off[1], req_off[2]), scarcity


def _candidate_starts(n: int, length: int, gap_before: np.ndarray) -> list[int]:
    if length <= 0:
        return [0]
    max_s = n - length
    if max_s <= 0:
        return [0]
    if n <= 360:
        return list(range(max_s + 1))
    starts = {0, max_s}
    for i in range(max_s + 1):
        if gap_before[i] or (i + length < n and gap_before[i + length]):
            starts.add(i)
    stride = max(1, n // 180)
    starts.update(range(0, max_s + 1, stride))
    starts.update(range(max(0, max_s - 30), max_s + 1))
    return sorted(starts)


def _precompute_interval_window_stats(
    prof: pd.DataFrame,
    window_len: int = POINTS_PER_DAY_15MIN,
):
    """预计算任意连续日区间 [a, b) 的精确 15min 滑窗数与 15min 连续段数回调。"""
    n = len(prof)
    n_pts = prof["n_points"].to_numpy(np.int64)
    gap_before = prof["gap_before"].to_numpy(bool)
    if "intraday_gaps" in prof.columns and "head_run" in prof.columns:
        intra_gaps = prof["intraday_gaps"].to_numpy(np.int64)
        head_run = prof["head_run"].to_numpy(np.int64)
        tail_run = prof["tail_run"].to_numpy(np.int64)
        mid_win = prof["mid_windows"].to_numpy(np.int64)
        link_prev = prof["stream_link_prev"].to_numpy(bool)
    else:
        intra_gaps = np.zeros(n, dtype=np.int64)
        head_run = n_pts.copy()
        tail_run = n_pts.copy()
        mid_win = np.zeros(n, dtype=np.int64)
        link_prev = np.zeros(n, dtype=bool)
        if n > 1:
            link_prev[1:] = (~gap_before[1:]) & (n_pts[1:] == window_len) & (n_pts[:-1] == window_len)

    w_minus_1 = max(1, int(window_len) - 1)
    if n <= 360:
        win_mat = np.zeros((n + 1, n + 1), dtype=np.int64)
        seg_mat = np.zeros((n + 1, n + 1), dtype=np.int64)
        for a in range(n):
            cur_run = 0
            done_win = 0
            n_segs = 0
            for d in range(a, n):
                pts_d = int(n_pts[d])
                if pts_d <= 0:
                    if cur_run > 0:
                        done_win += max(0, cur_run - w_minus_1)
                        cur_run = 0
                else:
                    if d == a or (not link_prev[d]) or cur_run == 0:
                        if cur_run > 0:
                            done_win += max(0, cur_run - w_minus_1)
                        cur_run = int(head_run[d])
                        n_segs += 1
                    else:
                        cur_run += int(head_run[d])
                    ig_d = int(intra_gaps[d])
                    if ig_d > 0:
                        done_win += max(0, cur_run - w_minus_1) + int(mid_win[d])
                        cur_run = int(tail_run[d])
                        n_segs += ig_d
                win_mat[a, d + 1] = done_win + max(0, cur_run - w_minus_1)
                seg_mat[a, d + 1] = n_segs

        def _interval_stats(a: int, b: int) -> tuple[int, int]:
            if b <= a:
                return 0, 0
            return int(win_mat[a, b]), int(seg_mat[a, b])

        return _interval_stats

    ps_pts = np.concatenate([[0], np.cumsum(n_pts)])
    stream_brk = (~link_prev).astype(np.int64)
    ps_brk = np.concatenate([[0], np.cumsum(stream_brk + intra_gaps)])

    def _interval_stats_large(a: int, b: int) -> tuple[int, int]:
        if b <= a:
            return 0, 0
        pts_ab = int(ps_pts[b] - ps_pts[a])
        segs_ab = 1 + int(intra_gaps[a]) + int(ps_brk[b] - ps_brk[a + 1])
        return max(0, pts_ab - w_minus_1 * segs_ab), segs_ab

    return _interval_stats_large


def _pick_best_two_slices(
    prof: pd.DataFrame,
    n_va: int,
    n_te: int,
    has_state: bool = False,
    req_on: tuple[int, int, int] = (0, 0, 0),
    req_off: tuple[int, int, int] = (0, 0, 0),
    enforce_skew_cap: float | None = None,
) -> tuple[tuple[int, int] | None, dict]:
    """在有序日画像表 prof 上用前缀和精确搜索最优非重叠连续区间 (val_start, test_start)。"""
    n = len(prof)
    n_tr = n - n_va - n_te
    n_pts_arr = prof["n_points"].to_numpy(np.int64)
    min_scores = prof["min_side_score"].to_numpy(np.float64)
    tier_a = (prof["tier"] == "A").to_numpy(np.float64)
    tier_c = (prof["tier"] == "C").to_numpy(np.float64)
    stub48 = (n_pts_arr < 48).astype(np.float64)
    stub64 = (n_pts_arr < 64).astype(np.float64)
    island = prof["is_island"].to_numpy(np.float64)
    gap_before = prof["gap_before"].to_numpy(bool)
    outage_c = (gap_before & (prof["tier"] == "C").to_numpy(bool)).astype(np.float64)
    intra_gaps_arr = (
        prof["intraday_gaps"].to_numpy(np.int64)
        if "intraday_gaps" in prof.columns
        else np.zeros(n, dtype=np.int64)
    )

    ps_score = np.concatenate([[0.0], np.cumsum(min_scores)])
    ps_ta = np.concatenate([[0.0], np.cumsum(tier_a)])
    ps_tc = np.concatenate([[0.0], np.cumsum(tier_c)])
    ps_s48 = np.concatenate([[0.0], np.cumsum(stub48)])
    ps_s64 = np.concatenate([[0.0], np.cumsum(stub64)])
    ps_isl = np.concatenate([[0.0], np.cumsum(island)])
    ps_gap = np.concatenate([[0], np.cumsum(gap_before.astype(np.int64))])
    ps_outc = np.concatenate([[0.0], np.cumsum(outage_c)])
    ps_intra = np.concatenate([[0], np.cumsum(intra_gaps_arr)])

    interval_stats_fn = _precompute_interval_window_stats(prof, POINTS_PER_DAY_15MIN)
    pool_win, _ = interval_stats_fn(0, n)

    if has_state:
        on_arr = prof["is_on"].to_numpy(bool).astype(np.int64)
        off_arr = 1 - on_arr
        contig_on_arr = (
            (on_arr == 1)
            & (prof["block_len"].to_numpy(np.int64) >= 2)
            & (n_pts_arr >= 88)
        ).astype(np.int64)
        imperf_on_arr = (
            (on_arr == 1)
            & ((tier_c > 0.5) | (intra_gaps_arr > 0) | (n_pts_arr < POINTS_PER_DAY_15MIN))
        ).astype(np.float64)
        ps_on = np.concatenate([[0], np.cumsum(on_arr)])
        ps_off = np.concatenate([[0], np.cumsum(off_arr)])
        ps_contig_on = np.concatenate([[0], np.cumsum(contig_on_arr)])
        ps_imperf_on = np.concatenate([[0.0], np.cumsum(imperf_on_arr)])
        tot_on = int(ps_on[-1])
        tot_off = int(ps_off[-1])
        pool_off_rate = tot_off / n if n > 0 else 0.0
    else:
        off_arr = None
        ps_on = ps_off = ps_contig_on = ps_imperf_on = None
        tot_on = tot_off = 0
        pool_off_rate = 0.0

    # 检测跨多周/跨季节大断口（缺数 >= 21 天，且前后时期均具实质规模）：
    # 防止像 2844（2025-07 全开 18d + 10 个月断口 + 2026-05~06 混合 27d）那样把近期连续开机段抽空，
    # 或用远古 100% 全开期稀释后的池均关机率误杀近期顺时序切分。
    recent_start = 0
    if "day_ts" in prof.columns and n >= 18:
        days_ts = prof["day_ts"].tolist()
        for i in range(1, n):
            if (days_ts[i] - days_ts[i - 1]).days - 1 >= 21 and i >= 5 and (n - i) >= 12:
                recent_start = i

    if recent_start > 0 and has_state and ps_on is not None and ps_off is not None and ps_contig_on is not None:
        n_rec = n - recent_start
        rec_off_tot = int(ps_off[n] - ps_off[recent_start])
        rec_contig_on_tot = int(ps_contig_on[n] - ps_contig_on[recent_start])
        hist_off_rate = float(ps_off[recent_start]) / float(recent_start)
        rec_off_rate = float(rec_off_tot) / float(n_rec)
        epoch_off_shift = abs(hist_off_rate - rec_off_rate) >= 0.25
        if n_rec >= (n_va + n_te + 4) and rec_contig_on_tot >= 4:
            rec_tr_avail = n_rec - n_va - n_te
            min_rec_tr_contig_on = min(rec_contig_on_tot // 2, max(2, min(4, rec_tr_avail // 2)))
        else:
            min_rec_tr_contig_on = 0
    else:
        n_rec = 0
        rec_off_tot = 0
        rec_contig_on_tot = 0
        rec_off_rate = pool_off_rate
        epoch_off_shift = False
        min_rec_tr_contig_on = 0

    va_starts = _candidate_starts(n, n_va, gap_before)
    te_starts = _candidate_starts(n, n_te, gap_before)

    best_pair: tuple[int, int] | None = None
    best_key: tuple | None = None
    best_diag: dict = {}

    for v0 in va_starts:
        v1 = v0 + n_va
        for t0 in te_starts:
            t1 = t0 + n_te
            if n_va > 0 and n_te > 0 and not (v1 <= t0 or t1 <= v0):
                continue

            if has_state and ps_on is not None and ps_off is not None:
                on_va = int(ps_on[v1] - ps_on[v0]) if n_va > 0 else 0
                on_te = int(ps_on[t1] - ps_on[t0]) if n_te > 0 else 0
                on_tr = tot_on - on_va - on_te
                off_va = int(ps_off[v1] - ps_off[v0]) if n_va > 0 else 0
                off_te = int(ps_off[t1] - ps_off[t0]) if n_te > 0 else 0
                off_tr = tot_off - off_va - off_te

                cover_ok = (
                    on_tr >= req_on[0] and on_va >= req_on[1] and on_te >= req_on[2]
                    and off_tr >= req_off[0] and off_va >= req_off[1] and off_te >= req_off[2]
                )
                recent_cover_ok = True
                if min_rec_tr_contig_on > 0 and ps_contig_on is not None:
                    va_r0, va_r1 = max(v0, recent_start), max(v1, recent_start)
                    te_r0, te_r1 = max(t0, recent_start), max(t1, recent_start)
                    rec_contig_on_tr = (
                        rec_contig_on_tot
                        - (int(ps_contig_on[va_r1] - ps_contig_on[va_r0]) if n_va > 0 else 0)
                        - (int(ps_contig_on[te_r1] - ps_contig_on[te_r0]) if n_te > 0 else 0)
                    )
                    recent_cover_ok = rec_contig_on_tr >= min_rec_tr_contig_on

                skews = []
                for cnt_off, cnt_tot in ((off_tr, n_tr), (off_va, n_va), (off_te, n_te)):
                    if cnt_tot >= 2:
                        skews.append(abs(cnt_off / cnt_tot - pool_off_rate))
                max_skew = max(skews) if skews else 0.0
                mean_skew = float(np.mean(skews)) if skews else 0.0
                if enforce_skew_cap is None:
                    skew_ok = True
                elif epoch_off_shift and v0 >= recent_start and t0 >= recent_start:
                    rec_skews: list[float] = []
                    if n_va >= 2:
                        rec_skews.append(abs(off_va / n_va - rec_off_rate))
                    if n_te >= 2:
                        rec_skews.append(abs(off_te / n_te - rec_off_rate))
                    rec_tr_days = n_rec - n_va - n_te
                    if rec_tr_days >= 2:
                        rec_off_tr = rec_off_tot - off_va - off_te
                        rec_skews.append(abs(rec_off_tr / rec_tr_days - rec_off_rate))
                    max_rec_skew = max(rec_skews) if rec_skews else 0.0
                    mean_rec_skew = float(np.mean(rec_skews)) if rec_skews else 0.0
                    skew_ok = (max_skew <= enforce_skew_cap) or (max_rec_skew <= max(enforce_skew_cap, 0.30))
                    mean_skew = 0.5 * mean_skew + 0.5 * mean_rec_skew
                    max_skew = min(max_skew, max_rec_skew)
                else:
                    skew_ok = max_skew <= enforce_skew_cap
            else:
                cover_ok = True
                recent_cover_ok = True
                skew_ok = True
                max_skew = 0.0
                mean_skew = 0.0

            # 质量评分（优先让 val/test 成为高分完整连续段，C 级残缺天与孤岛天优先留 train）
            q_terms = []
            ta_terms = []
            tc_terms = []
            s48_terms = []
            isl_terms = []
            tot_tc_eval = 0.0
            tot_outc_eval = 0.0
            for s0, s1, slen in ((v0, v1, n_va), (t0, t1, n_te)):
                if slen > 0:
                    q_terms.append((ps_score[s1] - ps_score[s0]) / (100.0 * slen))
                    ta_terms.append((ps_ta[s1] - ps_ta[s0]) / slen)
                    tc_cnt = ps_tc[s1] - ps_tc[s0]
                    tc_terms.append(tc_cnt / slen)
                    tot_tc_eval += tc_cnt
                    tot_outc_eval += (ps_outc[s1] - ps_outc[s0])
                    s48_terms.append((ps_s48[s1] - ps_s48[s0]) / slen)
                    if slen >= 2:
                        isl_terms.append((ps_isl[s1] - ps_isl[s0]) / slen)
            q_util = (
                35.0 * (float(np.mean(q_terms)) if q_terms else 1.0)
                + 8.0 * (float(np.mean(ta_terms)) if ta_terms else 0.0)
                - 18.0 * (float(np.mean(tc_terms)) if tc_terms else 0.0)
                - 2.5 * tot_tc_eval
                - 6.0 * tot_outc_eval
                - 10.0 * (float(np.mean(s48_terms)) if s48_terms else 0.0)
                - 6.0 * (float(np.mean(isl_terms)) if isl_terms else 0.0)
            )

            # 连续性、天然断口复用、折内断口重罚与人为单日孤岛惩罚
            cuts: set[int] = set()
            if n_va > 0:
                if 0 < v0 < n:
                    cuts.add(v0)
                if 0 < v1 < n:
                    cuts.add(v1)
            if n_te > 0:
                if 0 < t0 < n:
                    cuts.add(t0)
                if 0 < t1 < n:
                    cuts.add(t1)
            nat_cuts = sum(1 for c in cuts if gap_before[c])
            # 停电恢复后首日半天残缺（c-1 紧邻断口且为 C 级，c 起恢复为 A 级满点天）：将残缺恢复日留给 train 视作准天然切口
            recovery_cuts = sum(
                1 for c in cuts
                if (not gap_before[c]) and c >= 1 and gap_before[c - 1]
                and tier_c[c - 1] > 0.5 and tier_a[c] > 0.5
            )
            art_cuts = len(cuts) - nat_cuts

            # val / test 内部吞入离线断口数（每吞入 1 个断口就把评估折切成碎片段）与切割制造的新单日孤岛数
            internal_gaps = 0
            induced_islands = 0
            for s0, s1, slen in ((v0, v1, n_va), (t0, t1, n_te)):
                if slen >= 2:
                    internal_gaps += int(ps_gap[s1] - ps_gap[s0 + 1])
                    if gap_before[s0 + 1] and island[s0] < 0.5:
                        induced_islands += 1
                    if gap_before[s1 - 1] and island[s1 - 1] < 0.5:
                        induced_islands += 1
            for c in cuts:
                if not gap_before[c]:
                    if c >= 1 and gap_before[c - 1] and island[c - 1] < 0.5 and tier_c[c - 1] < 0.5:
                        induced_islands += 1
                    if (c + 1 == n or gap_before[c + 1]) and island[c] < 0.5 and tier_c[c] < 0.5:
                        induced_islands += 1

            # train 被切成的区间段数、有效 15min 滑窗数及时序因果硬护栏：
            # 1) 禁止像 724 旧版那样把前 12 天完整好天全划给 val+test、仅拿后半月碎片日当 train（整段逆时序前置倒置）；
            # 2) 当 val 与 test 之间仅夹着因 req_off 刚性锁留 train 的稀缺全关日或残缺日时，豁免反三明治惩罚；
            # 3) 显式计入日内 >15min 缺数断口（intraday_gaps）并校验 train 96 点有效滑窗保底。
            intervals = sorted([(s0, s1) for s0, s1, sl in ((v0, v1, n_va), (t0, t1, n_te)) if sl > 0])
            tr_intervals: list[tuple[int, int]] = []
            cursor = 0
            for s0, s1 in intervals:
                if s0 > cursor:
                    tr_intervals.append((cursor, s0))
                cursor = s1
            if cursor < n:
                tr_intervals.append((cursor, n))

            tr_segs = len(tr_intervals)
            non_c_tr_segs = sum(
                1 for a, b in tr_intervals
                if (b - a) - float(ps_tc[b] - ps_tc[a]) >= 1.0
            )
            tr_win = 0
            tr_15m_segs = 0
            for a, b in tr_intervals:
                w_ab, sg_ab = interval_stats_fn(a, b)
                tr_win += w_ab
                tr_15m_segs += sg_ab

            eval_intra_gaps = sum(
                int(ps_intra[s1] - ps_intra[s0])
                for s0, s1, sl in ((v0, v1, n_va), (t0, t1, n_te)) if sl > 0
            )

            # 时序因果硬护栏（chrono_causal_ok）
            chrono_causal_ok = True
            future_tail_pen = 0.0
            if len(intervals) > 0:
                eval_first = intervals[0][0]
                eval_last = intervals[-1][1]
                tail_len = n - eval_last
                tail_tc = float(ps_tc[n] - ps_tc[eval_last])
                tail_s64 = float(ps_s64[n] - ps_s64[eval_last])
                non_c_tail = max(0, int(round(tail_len - max(tail_tc, tail_s64))))
                if tail_len > 0 and (tail_tc / tail_len) < 0.5:
                    future_tail_pen = 10.0 + 1.5 * (tail_len - tail_tc)

                if n >= 10 and n_tr >= 4:
                    min_front_tr = max(1, int(np.ceil(0.35 * n_tr)))
                    front_ok = eval_first >= min_front_tr
                    tail_off = int(ps_off[n] - ps_off[eval_last]) if (has_state and ps_off is not None) else 0
                    tail_forced_by_off = bool(
                        has_state and ps_off is not None and req_off[0] > 0
                        and tail_off > 0 and (off_tr - tail_off) < req_off[0]
                    )
                    if tail_forced_by_off and off_arr is not None:
                        tail_off_rel = np.flatnonzero(off_arr[eval_last:] > 0)
                        first_tail_off = (eval_last + int(tail_off_rel[0])) if len(tail_off_rel) > 0 else eval_last
                        tail_ok = (eval_last == first_tail_off) or (non_c_tail <= 3)
                    else:
                        tail_ok = (non_c_tail == 0)

                    order_forced_by_off = bool(
                        has_state and req_off[1] > 0 and req_off[2] == 0 and tot_off <= 2
                    )
                    order_ok = True if (n_va == 0 or n_te == 0 or order_forced_by_off or n < 12) else (v0 < t0)
                    chrono_causal_ok = bool(front_ok and tail_ok and order_ok)

            # 训练有效 96 点滑窗数保底护栏（train_win_ok）
            if n >= 12 and pool_win >= 200 and n_tr > 0:
                min_tr_win = int(np.floor(pool_win * max(0.48, 0.85 * (n_tr / float(n)))))
                train_win_ok = bool(tr_win >= min_tr_win)
            else:
                train_win_ok = True

            # 对称分离/反三明治保护（豁免因 req_off 刚性锁留 train 的稀缺全关日及邻接残缺日）
            split_apart_pen = 0.0
            if len(intervals) == 2 and intervals[0][1] < intervals[1][0]:
                mid_s0, mid_s1 = intervals[0][1], intervals[1][0]
                mid_len = mid_s1 - mid_s0
                mid_tc = float(ps_tc[mid_s1] - ps_tc[mid_s0])
                off_mid = int(ps_off[mid_s1] - ps_off[mid_s0]) if (has_state and ps_off is not None) else 0
                scarce_off_locked_in_mid = bool(
                    has_state and tot_off > 0 and (req_off[1] == 0 or req_off[2] == 0)
                    and off_mid > 0 and (off_tr - off_mid) < req_off[0]
                )
                if scarce_off_locked_in_mid:
                    mid_imperf_on = (
                        float(ps_imperf_on[mid_s1] - ps_imperf_on[mid_s0])
                        if ps_imperf_on is not None else mid_tc
                    )
                    extra_clean_on = max(0.0, float(mid_len - off_mid) - mid_imperf_on)
                    split_apart_pen = 1.5 * extra_clean_on
                elif mid_len > 0 and (mid_tc / mid_len) < 0.5:
                    split_apart_pen = 16.0 + 2.5 * (mid_len - mid_tc)
                else:
                    split_apart_pen = 4.0

            chrono_inv_pen = 18.0 if (n_va > 0 and n_te > 0 and t0 < v0) else 0.0
            eff_art_cuts = max(0, art_cuts - recovery_cuts)
            win_util = (
                14.0 * ((tr_win / float(pool_win)) - (n_tr / float(n)))
                if (pool_win >= POINTS_PER_DAY_15MIN and n > 0) else 0.0
            )
            cont_util = (
                2.5 * nat_cuts
                + 2.0 * recovery_cuts
                - 6.0 * eff_art_cuts
                - 4.0 * max(0, tr_segs - 1)
                - 9.0 * internal_gaps
                - 2.0 * eval_intra_gaps
                - 6.0 * induced_islands
                - future_tail_pen
                - split_apart_pen
                - chrono_inv_pen
                + win_util
            )

            # 开关均衡度与时序近端偏好
            bal_util = -(25.0 * mean_skew + 15.0 * max_skew) if has_state else 0.0
            va_center = ((v0 + 0.5 * n_va) / n) if n_va > 0 else 0.8
            te_center = ((t0 + 0.5 * n_te) / n) if n_te > 0 else 0.9
            chrono_bonus = 3.0 if (n_va == 0 or n_te == 0 or v0 < t0) else 0.0
            rec_util = 6.0 * (0.45 * va_center + 0.55 * te_center) + chrono_bonus

            total_util = q_util + cont_util + bal_util + rec_util
            cand_key = (
                1 if (cover_ok and recent_cover_ok and skew_ok and chrono_causal_ok and train_win_ok) else 0,
                1 if (cover_ok and recent_cover_ok and chrono_causal_ok and train_win_ok) else 0,
                1 if (cover_ok and recent_cover_ok and chrono_causal_ok) else 0,
                1 if (cover_ok and recent_cover_ok) else 0,
                1 if cover_ok else 0,
                round(total_util, 6),
                v0,
                t0,
            )
            if best_key is None or cand_key > best_key:
                best_key = cand_key
                best_pair = (v0, t0)
                best_diag = {
                    "cover_ok": bool(cover_ok),
                    "recent_cover_ok": bool(recent_cover_ok),
                    "skew_ok": bool(skew_ok),
                    "chrono_causal_ok": bool(chrono_causal_ok),
                    "train_win_ok": bool(train_win_ok),
                    "chronological_order": bool(n_va == 0 or n_te == 0 or v0 < t0),
                    "recent_epoch_start_idx": int(recent_start),
                    "max_off_skew": round(float(max_skew), 4),
                    "mean_off_skew": round(float(mean_skew), 4),
                    "natural_gap_cuts": int(nat_cuts),
                    "artificial_cuts": int(art_cuts),
                    "internal_gaps": int(internal_gaps),
                    "eval_intraday_gaps": int(eval_intra_gaps),
                    "induced_islands": int(induced_islands),
                    "train_day_segments": int(tr_segs),
                    "non_c_train_segments": int(non_c_tr_segs),
                    "train_15m_segments": int(tr_15m_segs),
                    "train_windows_96": int(tr_win),
                    "pool_windows_96": int(pool_win),
                    "utility_score": round(float(total_util), 4),
                }

    return best_pair, best_diag


def _allocate_stratified_quotas(
    total_on: int,
    total_off: int,
    n_tr: int,
    n_va: int,
    n_te: int,
    req_on: tuple[int, int, int],
    req_off: tuple[int, int, int],
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """在极化双段分层模式下精确分配各折全关日与开机日整数配额。"""
    n_total = n_tr + n_va + n_te
    off_rate = total_off / n_total if n_total > 0 else 0.0

    min_va_off = req_off[1]
    max_va_off = min(n_va - req_on[1], total_off - req_off[0] - req_off[2])
    q_va_off = int(np.clip(int(round(n_va * off_rate)), min_va_off, max(min_va_off, max_va_off)))

    min_te_off = req_off[2]
    max_te_off = min(n_te - req_on[2], total_off - req_off[0] - q_va_off)
    q_te_off = int(np.clip(int(round(n_te * off_rate)), min_te_off, max(min_te_off, max_te_off)))

    q_tr_off = total_off - q_va_off - q_te_off
    # 若 train 内全关天过多挤压了 req_on[0]（即 n_tr - q_tr_off < req_on[0]），向 val/test 疏导多余全关天
    max_tr_off = n_tr - req_on[0]
    while q_tr_off > max_tr_off:
        moved = False
        if q_va_off < (n_va - req_on[1]):
            q_va_off += 1
            q_tr_off -= 1
            moved = True
        if q_tr_off > max_tr_off and q_te_off < (n_te - req_on[2]):
            q_te_off += 1
            q_tr_off -= 1
            moved = True
        if not moved:
            break

    q_tr_on = n_tr - q_tr_off
    q_va_on = n_va - q_va_off
    q_te_on = n_te - q_te_off
    return (q_tr_on, q_va_on, q_te_on), (q_tr_off, q_va_off, q_te_off)


def resolve_auto_quality_split(
    index: pd.DatetimeIndex,
    ratios: list[float],
    daily_quality: pd.DataFrame | None = None,
    target: pd.Series | None = None,
    on_thr_w: float | None = None,
    valid_mask: pd.Series | np.ndarray | None = None,
) -> tuple[dict[str, pd.Series], dict]:
    """基于每日数据质量、连续自然日块与开/关工况的确定性自动切分器。"""
    if len(ratios) != 3 or abs(sum(ratios) - 1.0) > 1e-6:
        raise ValueError(f"split_ratios 必须为 3 个和为 1 的数: {ratios}")
    if len(index) == 0:
        empty = {name: pd.Series(dtype=bool, index=index) for name in SPLIT_NAMES}
        return empty, {"resolved_mode": "auto_empty", "n_days": 0}

    prof = build_day_quality_profiles(
        index, daily_quality=daily_quality,
        target=target, on_thr_w=on_thr_w, valid_mask=valid_mask,
    )
    days = prof["day_ts"].tolist()
    n_days = len(days)

    if n_days < 3:
        assign = _ratio_assign(days, ratios)
        day_split = pd.Series([assign[d] for d in index.normalize()], index=index)
        masks = {name: day_split == name for name in SPLIT_NAMES}
        return masks, {
            "resolved_mode": "auto_tiny_fallback",
            "n_days": n_days,
            "off_day_scarcity": "tiny_pool",
        }

    n_tr_base, n_va_base, n_te_base = _compute_day_quotas(n_days, ratios)
    has_state = prof["is_on"].notna().all()
    if has_state:
        tot_on = int(prof["is_on"].sum())
        tot_off = n_days - tot_on
    else:
        tot_on = tot_off = 0

    skew_cap = 0.25 if (has_state and n_days >= 12 and tot_on > 0 and tot_off > 0) else None

    # 弹性 ±1 天配额对齐（N >= 14 时启用）：当死守 round(N*0.2) 会迫使 val/test 跨越离线断口
    # 或吞入断口后半天残缺恢复日（如 733 户 39 天下 8+8=16 越过 06-13~14 断口）时，允许微调 ±1 天贴合连续满点块
    cand_quotas: list[tuple[int, int]] = [(n_va_base, n_te_base)]
    if n_days >= 14 and n_va_base >= 3 and n_te_base >= 3:
        base_tol = 2 if (n_va_base >= 8 and n_te_base >= 8) else 1
        max_diff = max(base_tol, abs(n_va_base - n_te_base) + 1)
        for dv in (-1, 0, 1):
            for dt in (-1, 0, 1):
                if dv == 0 and dt == 0:
                    continue
                c_va = n_va_base + dv
                c_te = n_te_base + dt
                c_tr = n_days - c_va - c_te
                if c_va >= 2 and c_te >= 2 and c_tr >= max(4, n_tr_base - 2) and abs(c_va - c_te) <= max_diff:
                    cand_quotas.append((c_va, c_te))

    best_overall_key: tuple | None = None
    best_pair: tuple[int, int] | None = None
    diag: dict = {}
    n_tr, n_va, n_te = n_tr_base, n_va_base, n_te_base
    req_on = (0, 0, 0)
    req_off = (0, 0, 0)
    off_scarcity = "no_target_state"

    target_va = n_days * float(ratios[1])
    target_te = n_days * float(ratios[2])
    base_quota_err = abs(n_va_base - target_va) + abs(n_te_base - target_te)

    def _eval_quota_candidates(
        quota_list: list[tuple[int, int]],
        require_clean_block_gain: bool = False,
        stage1_clean: bool = False,
    ) -> None:
        nonlocal best_overall_key, best_pair, diag, n_tr, n_va, n_te, req_on, req_off, off_scarcity
        for c_va, c_te in quota_list:
            c_tr = n_days - c_va - c_te
            if has_state:
                c_req_on, c_req_off, c_scarcity = _compute_state_floors(tot_on, tot_off, c_tr, c_va, c_te)
            else:
                c_req_on, c_req_off, c_scarcity = (0, 0, 0), (0, 0, 0), "no_target_state"

            c_pair, c_diag = _pick_best_two_slices(
                prof, c_va, c_te,
                has_state=has_state,
                req_on=c_req_on,
                req_off=c_req_off,
                enforce_skew_cap=skew_cap,
            )
            if c_pair is None:
                continue
            cand_is_clean = (
                c_diag.get("internal_gaps", 0) == 0
                and c_diag.get("non_c_train_segments", 1) <= 1
                and c_diag.get("chrono_causal_ok", True)
                and c_diag.get("train_win_ok", True)
            )
            if require_clean_block_gain and (stage1_clean or not cand_is_clean):
                continue

            dev_days = abs(c_va - n_va_base) + abs(c_te - n_te_base)
            cand_quota_err = abs(c_va - target_va) + abs(c_te - target_te)
            extra_quota_err = max(0.0, cand_quota_err - base_quota_err)
            adj_util = float(c_diag.get("utility_score", 0.0)) - 2.2 * extra_quota_err
            cov_ok = c_diag.get("cover_ok", False)
            rec_ok = c_diag.get("recent_cover_ok", True)
            skw_ok = c_diag.get("skew_ok", True)
            chr_ok = c_diag.get("chrono_causal_ok", True)
            win_ok = c_diag.get("train_win_ok", True)
            c_key = (
                1 if (cov_ok and rec_ok and skw_ok and chr_ok and win_ok) else 0,
                1 if (cov_ok and rec_ok and chr_ok and win_ok) else 0,
                1 if (cov_ok and rec_ok and chr_ok) else 0,
                1 if (cov_ok and rec_ok) else 0,
                1 if cov_ok else 0,
                round(adj_util, 6),
                -dev_days,
                c_tr,
            )
            if best_overall_key is None or c_key > best_overall_key:
                best_overall_key = c_key
                best_pair = c_pair
                c_diag = dict(c_diag)
                c_diag["utility_score"] = round(adj_util, 4)
                c_diag["quota_adjusted_days"] = int(dev_days)
                diag = c_diag
                n_tr, n_va, n_te = c_tr, c_va, c_te
                req_on, req_off, off_scarcity = c_req_on, c_req_off, c_scarcity

    _eval_quota_candidates(cand_quotas, require_clean_block_gain=False)

    # 二阶段评估块自动缩减：当 ±1 天配额仍被迫跨断口或把非 C 级 train 切碎（如 724 户唯一全关日 06-08 锁留 train
    # 且后方仅 4d + 断口 + 5d = 9 天 < 5+5 天）时，允许向 -2 天缩减贴合天然无断口连续块。
    stage1_clean = bool(
        diag.get("cover_ok", False)
        and diag.get("chrono_causal_ok", True)
        and diag.get("train_win_ok", True)
        and diag.get("internal_gaps", 0) == 0
        and diag.get("non_c_train_segments", 1) <= 1
    )
    if (not stage1_clean) and n_days >= 18 and n_va_base >= 4 and n_te_base >= 4:
        seen_q = set(cand_quotas)
        shrink_quotas: list[tuple[int, int]] = []
        min_eval_days = max(3, int(np.floor(n_days * 0.12)))
        max_tr_days = int(np.ceil(n_days * 0.72))
        max_diff_s2 = max(2, abs(n_va_base - n_te_base) + 1)
        for dv in (-2, -1, 0, 1):
            for dt in (-2, -1, 0, 1):
                c_va = n_va_base + dv
                c_te = n_te_base + dt
                if (c_va, c_te) in seen_q:
                    continue
                c_tr = n_days - c_va - c_te
                if (
                    c_va >= min_eval_days
                    and c_te >= min_eval_days
                    and max(4, n_tr_base - 2) <= c_tr <= max_tr_days
                    and abs(c_va - c_te) <= max_diff_s2
                ):
                    shrink_quotas.append((c_va, c_te))
                    seen_q.add((c_va, c_te))
        if shrink_quotas:
            _eval_quota_candidates(
                shrink_quotas,
                require_clean_block_gain=True,
                stage1_clean=stage1_clean,
            )

    use_polarized = (
        has_state
        and n_days >= 6
        and tot_on > 0
        and tot_off > 0
        and (not diag.get("cover_ok", False) or not diag.get("skew_ok", True))
    )

    # 极端破碎兜底：若连续块搜索同时违反因果时序护栏与训练有效产窗护栏，平滑回退至 stratified_day
    use_stratified_fallback = (
        (not use_polarized)
        and n_days >= 12
        and (
            best_pair is None
            or (not diag.get("chrono_causal_ok", True) and not diag.get("train_win_ok", True))
        )
    )

    assign: dict[pd.Timestamp, str] = {}
    if use_stratified_fallback:
        by_dow: dict[int, list[pd.Timestamp]] = {}
        for d in days:
            by_dow.setdefault(d.day_of_week, []).append(d)
        for _, ds in sorted(by_dow.items()):
            assign.update(_ratio_assign(ds, ratios))
        vals_fb = list(assign.values())
        n_tr = vals_fb.count("train")
        n_va = vals_fb.count("val")
        n_te = vals_fb.count("test")
        mode_name = "auto_fallback_stratified_day"
    elif not use_polarized and best_pair is not None:
        v0, t0 = best_pair
        va_set = set(days[v0:v0 + n_va]) if n_va > 0 else set()
        te_set = set(days[t0:t0 + n_te]) if n_te > 0 else set()
        for d in days:
            if d in va_set:
                assign[d] = "val"
            elif d in te_set:
                assign[d] = "test"
            else:
                assign[d] = "train"
        if not has_state:
            mode_name = "auto_quality_no_target"
        elif n_days < 10:
            mode_name = "auto_small_sample_contiguous"
        else:
            mode_name = "auto_contiguous_quality_blocks"
    else:
        # 极化分布（如前 30 天全关 + 后 30 天全开）：回退基准比例配额并在开/关子序列内部分别挑选高分连续块
        n_tr, n_va, n_te = n_tr_base, n_va_base, n_te_base
        req_on, req_off, off_scarcity = _compute_state_floors(tot_on, tot_off, n_tr, n_va, n_te)
        q_on, q_off = _allocate_stratified_quotas(
            tot_on, tot_off, n_tr, n_va, n_te, req_on, req_off
        )
        on_prof = prof[prof["is_on"] == True].reset_index(drop=True)   # noqa: E712
        off_prof = prof[prof["is_on"] == False].reset_index(drop=True)  # noqa: E712
        # 重新计算各子序列内部的天然断口标记（相邻日差 > 1 天即为天然断口）
        for sub_p, (_, sub_va, sub_te) in ((on_prof, q_on), (off_prof, q_off)):
            if len(sub_p) == 0:
                continue
            sub_days = sub_p["day_ts"].tolist()
            sub_p = sub_p.copy()
            sub_gb = [
                (i == 0) or ((sub_days[i] - sub_days[i - 1]).days > 1)
                for i in range(len(sub_days))
            ]
            sub_p["gap_before"] = sub_gb
            if "stream_link_prev" in sub_p.columns:
                sub_p["stream_link_prev"] = (
                    sub_p["stream_link_prev"].to_numpy(bool) & (~np.asarray(sub_gb, dtype=bool))
                )
            sub_pair, _ = _pick_best_two_slices(sub_p, sub_va, sub_te, has_state=False)
            sv0, st0 = sub_pair if sub_pair is not None else (max(0, len(sub_days) - sub_va - sub_te), max(0, len(sub_days) - sub_te))
            sva = set(sub_days[sv0:sv0 + sub_va]) if sub_va > 0 else set()
            ste = set(sub_days[st0:st0 + sub_te]) if sub_te > 0 else set()
            for d in sub_days:
                if d in sva:
                    assign[d] = "val"
                elif d in ste:
                    assign[d] = "test"
                else:
                    assign[d] = "train"
        mode_name = "auto_polarized_two_block"

    day_split = pd.Series([assign[d] for d in index.normalize()], index=index)
    masks = {name: day_split == name for name in SPLIT_NAMES}
    meta = {
        "resolved_mode": mode_name,
        "n_days": int(n_days),
        "quotas_days": {"train": int(n_tr), "val": int(n_va), "test": int(n_te)},
        "off_day_scarcity": off_scarcity,
        **diag,
    }
    return masks, meta


def summarize_split_decision(
    index: pd.DatetimeIndex,
    masks: dict[str, pd.Series],
    strategy: str,
    daily_quality: pd.DataFrame | None = None,
    target: pd.Series | None = None,
    on_thr_w: float | None = None,
    auto_meta: dict | None = None,
    pool_curation: dict | None = None,
    valid_mask: pd.Series | np.ndarray | None = None,
) -> dict:
    """对最终生效的 train/val/test 掩码生成结构化质量与工况对账摘要（写入 meta.json）。"""
    prof = build_day_quality_profiles(
        index, daily_quality=daily_quality,
        target=target, on_thr_w=on_thr_w, valid_mask=valid_mask,
    )
    prof_by_day = {row["day_ts"]: row for _, row in prof.iterrows()}
    norm_idx = index.normalize()

    by_split: dict[str, dict] = {}
    for name in SPLIT_NAMES:
        m = masks[name].to_numpy(bool) if name in masks and len(masks[name]) else np.zeros(len(index), bool)
        s_days = sorted(pd.Series(norm_idx[m]).unique()) if m.any() else []
        n_blocks = 0
        for i, d in enumerate(s_days):
            if i == 0 or (d - s_days[i - 1]).days > 1:
                n_blocks += 1
        scores = [float(prof_by_day[d]["min_side_score"]) for d in s_days if d in prof_by_day]
        eff_scores = [float(prof_by_day[d]["effective_score"]) for d in s_days if d in prof_by_day]
        tier_a_cnt = sum(1 for d in s_days if d in prof_by_day and prof_by_day[d]["tier"] == "A")
        tier_c_cnt = sum(1 for d in s_days if d in prof_by_day and prof_by_day[d]["tier"] == "C")
        intra_g_cnt = sum(int(prof_by_day[d]["intraday_gaps"]) for d in s_days if d in prof_by_day)
        if target is not None and on_thr_w is not None:
            on_cnt = sum(1 for d in s_days if d in prof_by_day and bool(prof_by_day[d]["is_on"]))
            off_cnt = len(s_days) - on_cnt
            off_ratio = round(off_cnt / len(s_days), 4) if s_days else 0.0
        else:
            on_cnt = None
            off_cnt = None
            off_ratio = None
        by_split[name] = {
            "days": int(len(s_days)),
            "points": int(m.sum()),
            "contiguous_blocks": int(n_blocks),
            "intraday_gaps": int(intra_g_cnt),
            "on_days": on_cnt,
            "off_days": off_cnt,
            "off_day_ratio": off_ratio,
            "mean_min_side_score": round(float(np.mean(scores)), 2) if scores else None,
            "mean_effective_score": round(float(np.mean(eff_scores)), 2) if eff_scores else None,
            "tier_a_days": int(tier_a_cnt),
            "tier_c_days": int(tier_c_cnt),
        }

    out = {
        "configured_strategy": strategy,
        "resolved_mode": (auto_meta or {}).get("resolved_mode", strategy),
        "total_assigned_days": int(sum(v["days"] for v in by_split.values())),
        "by_split": by_split,
    }
    if auto_meta:
        for k in (
            "off_day_scarcity", "natural_gap_cuts", "artificial_cuts",
            "internal_gaps", "eval_intraday_gaps", "induced_islands",
            "quota_adjusted_days", "chrono_causal_ok", "train_win_ok",
            "train_windows_96", "pool_windows_96",
            "max_off_skew", "utility_score",
        ):
            if k in auto_meta:
                out[k] = auto_meta[k]
    if pool_curation:
        out["pool_curation"] = pool_curation
    return out


def initial_split(index: pd.DatetimeIndex, ratios: list[float],
                  strategy: str = "stratified_day",
                  target: pd.Series | None = None,
                  on_thr_w: float | None = None,
                  daily_quality: pd.DataFrame | None = None,
                  valid_mask: pd.Series | np.ndarray | None = None) -> dict[str, pd.Series]:
    """初始策略切分，返回 {'train','val','test'} 布尔 mask（互斥）。

    - auto               : 基于 daily_quality + target/on_thr_w 自动搜索连续高质量块；
    - stratified_by_state: 需 target+on_thr_w；缺失时退化为 time（并日志警告）。
    """
    if len(ratios) != 3 or abs(sum(ratios) - 1.0) > 1e-6:
        raise ValueError(f"split_ratios 必须为 3 个和为 1 的数: {ratios}")
    if strategy == "auto":
        masks, _ = resolve_auto_quality_split(
            index, ratios,
            daily_quality=daily_quality,
            target=target,
            on_thr_w=on_thr_w,
            valid_mask=valid_mask,
        )
        return masks

    days = _days(index)
    assign: dict[pd.Timestamp, str] = {}

    if strategy == "time":
        assign = _ratio_assign(days, ratios)
    elif strategy == "stratified_day":
        by_dow: dict[int, list[pd.Timestamp]] = {}
        for d in days:
            by_dow.setdefault(d.day_of_week, []).append(d)
        for _, ds in sorted(by_dow.items()):
            assign.update(_ratio_assign(ds, ratios))
    elif strategy == "stratified":
        by_month: dict[int, list[pd.Timestamp]] = {}
        for d in days:
            by_month.setdefault(d.month, []).append(d)
        for _, ds in sorted(by_month.items()):
            assign.update(_ratio_assign(ds, ratios))
    elif strategy == "global_stratified":
        seq = (["train"] * int(ratios[0] * 100) + ["val"] * int(ratios[1] * 100)
               + ["test"] * (100 - int(ratios[0] * 100) - int(ratios[1] * 100)))
        for i, d in enumerate(days):
            assign[d] = seq[i % 100]
    elif strategy == "stratified_by_state":
        state_map = _daily_on_off(index, target, on_thr_w)
        if state_map is None:
            import warnings
            warnings.warn("stratified_by_state 缺 target/on_thr_w，退化为 time 策略")
            assign = _ratio_assign(days, ratios)
        else:
            by_state: dict[bool, list[pd.Timestamp]] = {True: [], False: []}
            for d in days:
                by_state[state_map[d]].append(d)
            for _, ds in sorted(by_state.items(), key=lambda x: x[0]):
                ds_sorted = sorted(ds)
                assign.update(_ratio_assign(ds_sorted, ratios))
    else:
        raise ValueError(f"未知 split_strategy: {strategy}")

    day_split = pd.Series([assign[d] for d in index.normalize()], index=index)
    return {name: day_split == name for name in SPLIT_NAMES}


def repair_empty_splits(masks: dict[str, pd.Series], index: pd.DatetimeIndex,
                        ratios: list[float]) -> dict[str, pd.Series]:
    """比例修复（§12.4 第 3 步的最小实现）：val/test 为空但配额>0 时，
    从 train 的末尾按日划拨，保证验证/测试集存在。"""
    days = _days(index)
    masks = {k: v.copy() for k, v in masks.items()}
    for name in ("val", "test"):
        if ratios[{"val": 1, "test": 2}[name]] <= 0 or masks[name].any():
            continue
        need = max(1, int(round(len(days) * ratios[{"val": 1, "test": 2}[name]])))
        train_days = [d for d in days if (index.normalize() == d)[masks["train"].to_numpy()].any()]
        give = train_days[-need:] if len(train_days) > need else []
        if not give:
            continue
        sel = index.normalize().isin(give)
        masks["train"] = masks["train"] & ~sel
        masks[name] = masks[name] | sel
    return masks


def build_split_masks(index: pd.DatetimeIndex, ratios: list[float], strategy: str,
                      splits_spec: dict | None = None,
                      target: pd.Series | None = None,
                      on_thr_w: float | None = None,
                      daily_quality: pd.DataFrame | None = None,
                      valid_mask: pd.Series | np.ndarray | None = None) -> dict[str, pd.Series]:
    """完整切分流程：初始策略 → 锚定 → 比例修复。

    - auto               : 支持传入 daily_quality + target/on_thr_w 进行质量与工况联合连续块切分；
    - stratified_by_state: 需传入 target/on_thr_w 以按日开/关分层。
    """
    masks = initial_split(
        index, ratios, strategy,
        target=target, on_thr_w=on_thr_w,
        daily_quality=daily_quality,
        valid_mask=valid_mask,
    )
    masks = anchor_splits(index, masks, splits_spec)
    masks = repair_empty_splits(masks, index, ratios)
    return masks
