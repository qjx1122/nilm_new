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
) -> pd.DataFrame:
    """构建逐自然日「质量-连续块-工况」画像表，供 split_strategy='auto' 与审计落盘共用。"""
    days = _days(index)
    cols = [
        "date", "day_ts", "n_points", "bus_score", "branch_score",
        "min_side_score", "mean_score", "tier", "is_on",
        "gap_before", "gap_after", "block_id", "block_len", "is_island",
    ]
    if not days:
        return pd.DataFrame(columns=cols)

    norm_idx = index.normalize()
    pt_counts = pd.Series(1, index=norm_idx).groupby(level=0).sum().to_dict()

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
    for i, d in enumerate(days):
        d_str = d.strftime("%Y-%m-%d")
        n_pts = int(pt_counts.get(d, 0))
        inferred = float(np.clip(100.0 * n_pts / POINTS_PER_DAY_15MIN, 0.0, 100.0))
        if d_str in dq_map:
            bs, brs = dq_map[d_str]
        else:
            bs, brs = inferred, inferred
        min_s = float(min(bs, brs))
        mean_s = float(0.5 * (bs + brs))
        if min_s >= 95.0 and n_pts >= 88:
            tier = "A"
        elif min_s >= 80.0 and n_pts >= 64:
            tier = "B"
        else:
            tier = "C"
        blen = block_sizes[block_ids[i]]
        rows.append({
            "date": d_str,
            "day_ts": d,
            "n_points": n_pts,
            "bus_score": round(bs, 2),
            "branch_score": round(brs, 2),
            "min_side_score": round(min_s, 2),
            "mean_score": round(mean_s, 2),
            "tier": tier,
            "is_on": None if state_map is None else bool(state_map.get(d, False)),
            "gap_before": bool(gap_before[i]),
            "gap_after": bool(gap_after[i]),
            "block_id": int(block_ids[i]),
            "block_len": int(blen),
            "is_island": bool(blen == 1),
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
    min_scores = prof["min_side_score"].to_numpy(np.float64)
    tier_a = (prof["tier"] == "A").to_numpy(np.float64)
    tier_c = (prof["tier"] == "C").to_numpy(np.float64)
    stub48 = (prof["n_points"] < 48).to_numpy(np.float64)
    island = prof["is_island"].to_numpy(np.float64)
    gap_before = prof["gap_before"].to_numpy(bool)

    ps_score = np.concatenate([[0.0], np.cumsum(min_scores)])
    ps_ta = np.concatenate([[0.0], np.cumsum(tier_a)])
    ps_tc = np.concatenate([[0.0], np.cumsum(tier_c)])
    ps_s48 = np.concatenate([[0.0], np.cumsum(stub48)])
    ps_isl = np.concatenate([[0.0], np.cumsum(island)])

    if has_state:
        on_arr = prof["is_on"].to_numpy(bool).astype(np.int64)
        off_arr = 1 - on_arr
        ps_on = np.concatenate([[0], np.cumsum(on_arr)])
        ps_off = np.concatenate([[0], np.cumsum(off_arr)])
        tot_on = int(ps_on[-1])
        tot_off = int(ps_off[-1])
        pool_off_rate = tot_off / n if n > 0 else 0.0
    else:
        ps_on = ps_off = None
        tot_on = tot_off = 0
        pool_off_rate = 0.0

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
                skews = []
                for cnt_off, cnt_tot in ((off_tr, n_tr), (off_va, n_va), (off_te, n_te)):
                    if cnt_tot >= 2:
                        skews.append(abs(cnt_off / cnt_tot - pool_off_rate))
                max_skew = max(skews) if skews else 0.0
                mean_skew = float(np.mean(skews)) if skews else 0.0
                skew_ok = True if enforce_skew_cap is None else (max_skew <= enforce_skew_cap)
            else:
                cover_ok = True
                skew_ok = True
                max_skew = 0.0
                mean_skew = 0.0

            # 质量评分（优先让 val/test 成为高分完整连续段，C 级残缺天与孤岛天优先留 train）
            q_terms = []
            ta_terms = []
            tc_terms = []
            s48_terms = []
            isl_terms = []
            for s0, s1, slen in ((v0, v1, n_va), (t0, t1, n_te)):
                if slen > 0:
                    q_terms.append((ps_score[s1] - ps_score[s0]) / (100.0 * slen))
                    ta_terms.append((ps_ta[s1] - ps_ta[s0]) / slen)
                    tc_terms.append((ps_tc[s1] - ps_tc[s0]) / slen)
                    s48_terms.append((ps_s48[s1] - ps_s48[s0]) / slen)
                    if slen >= 2:
                        isl_terms.append((ps_isl[s1] - ps_isl[s0]) / slen)
            q_util = (
                35.0 * (float(np.mean(q_terms)) if q_terms else 1.0)
                + 8.0 * (float(np.mean(ta_terms)) if ta_terms else 0.0)
                - 12.0 * (float(np.mean(tc_terms)) if tc_terms else 0.0)
                - 10.0 * (float(np.mean(s48_terms)) if s48_terms else 0.0)
                - 6.0 * (float(np.mean(isl_terms)) if isl_terms else 0.0)
            )

            # 连续性与天然断口复用评分
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
            art_cuts = len(cuts) - nat_cuts

            # train 被切成的区间段数（越少越好，1 段最优）
            intervals = sorted([(s0, s1) for s0, s1, sl in ((v0, v1, n_va), (t0, t1, n_te)) if sl > 0])
            tr_segs = 0
            cursor = 0
            for s0, s1 in intervals:
                if s0 > cursor:
                    tr_segs += 1
                cursor = s1
            if cursor < n:
                tr_segs += 1
            cont_util = 2.5 * nat_cuts - 6.0 * art_cuts - 4.0 * max(0, tr_segs - 1)

            # 开关均衡度与时序近端偏好
            bal_util = -(25.0 * mean_skew + 15.0 * max_skew) if has_state else 0.0
            va_center = ((v0 + 0.5 * n_va) / n) if n_va > 0 else 0.8
            te_center = ((t0 + 0.5 * n_te) / n) if n_te > 0 else 0.9
            chrono_bonus = 3.0 if (n_va == 0 or n_te == 0 or v0 < t0) else 0.0
            rec_util = 6.0 * (0.45 * va_center + 0.55 * te_center) + chrono_bonus

            total_util = q_util + cont_util + bal_util + rec_util
            cand_key = (
                1 if (cover_ok and skew_ok) else 0,
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
                    "skew_ok": bool(skew_ok),
                    "max_off_skew": round(float(max_skew), 4),
                    "mean_off_skew": round(float(mean_skew), 4),
                    "natural_gap_cuts": int(nat_cuts),
                    "artificial_cuts": int(art_cuts),
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
) -> tuple[dict[str, pd.Series], dict]:
    """基于每日数据质量、连续自然日块与开/关工况的确定性自动切分器。"""
    if len(ratios) != 3 or abs(sum(ratios) - 1.0) > 1e-6:
        raise ValueError(f"split_ratios 必须为 3 个和为 1 的数: {ratios}")
    if len(index) == 0:
        empty = {name: pd.Series(dtype=bool, index=index) for name in SPLIT_NAMES}
        return empty, {"resolved_mode": "auto_empty", "n_days": 0}

    prof = build_day_quality_profiles(index, daily_quality=daily_quality,
                                      target=target, on_thr_w=on_thr_w)
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

    n_tr, n_va, n_te = _compute_day_quotas(n_days, ratios)
    has_state = prof["is_on"].notna().all()

    if has_state:
        tot_on = int(prof["is_on"].sum())
        tot_off = n_days - tot_on
        req_on, req_off, off_scarcity = _compute_state_floors(tot_on, tot_off, n_tr, n_va, n_te)
    else:
        tot_on = tot_off = 0
        req_on = (0, 0, 0)
        req_off = (0, 0, 0)
        off_scarcity = "no_target_state"

    skew_cap = 0.25 if (has_state and n_days >= 12 and tot_on > 0 and tot_off > 0) else None
    best_pair, diag = _pick_best_two_slices(
        prof, n_va, n_te,
        has_state=has_state,
        req_on=req_on,
        req_off=req_off,
        enforce_skew_cap=skew_cap,
    )

    use_polarized = (
        has_state
        and n_days >= 6
        and tot_on > 0
        and tot_off > 0
        and (not diag.get("cover_ok", False) or not diag.get("skew_ok", True))
    )

    assign: dict[pd.Timestamp, str] = {}
    if not use_polarized and best_pair is not None:
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
        # 极化分布（如前 30 天全关 + 后 30 天全开）：在开/关子序列内部分别挑选高分连续块
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
            sub_p["gap_before"] = [
                (i == 0) or ((sub_days[i] - sub_days[i - 1]).days > 1)
                for i in range(len(sub_days))
            ]
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
) -> dict:
    """对最终生效的 train/val/test 掩码生成结构化质量与工况对账摘要（写入 meta.json）。"""
    prof = build_day_quality_profiles(index, daily_quality=daily_quality,
                                      target=target, on_thr_w=on_thr_w)
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
        tier_a_cnt = sum(1 for d in s_days if d in prof_by_day and prof_by_day[d]["tier"] == "A")
        tier_c_cnt = sum(1 for d in s_days if d in prof_by_day and prof_by_day[d]["tier"] == "C")
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
            "on_days": on_cnt,
            "off_days": off_cnt,
            "off_day_ratio": off_ratio,
            "mean_min_side_score": round(float(np.mean(scores)), 2) if scores else None,
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
        for k in ("off_day_scarcity", "natural_gap_cuts", "artificial_cuts", "max_off_skew", "utility_score"):
            if k in auto_meta:
                out[k] = auto_meta[k]
    if pool_curation:
        out["pool_curation"] = pool_curation
    return out


def initial_split(index: pd.DatetimeIndex, ratios: list[float],
                  strategy: str = "stratified_day",
                  target: pd.Series | None = None,
                  on_thr_w: float | None = None,
                  daily_quality: pd.DataFrame | None = None) -> dict[str, pd.Series]:
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
                      daily_quality: pd.DataFrame | None = None) -> dict[str, pd.Series]:
    """完整切分流程：初始策略 → 锚定 → 比例修复。

    - auto               : 支持传入 daily_quality + target/on_thr_w 进行质量与工况联合连续块切分；
    - stratified_by_state: 需传入 target/on_thr_w 以按日开/关分层。
    """
    masks = initial_split(
        index, ratios, strategy,
        target=target, on_thr_w=on_thr_w,
        daily_quality=daily_quality,
    )
    masks = anchor_splits(index, masks, splits_spec)
    masks = repair_empty_splits(masks, index, ratios)
    return masks
