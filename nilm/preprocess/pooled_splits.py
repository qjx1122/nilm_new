"""跨用户切分（pooled 通用模型专用，不影响单用户链路）。

两种模式：
- LOUO（Leave-One-User-Out）：外层按用户留一，内层在 train users 内按
  stratified_by_state 切 train/val；test 为整个留出用户（零样本泛化评估）。
- pooled_time：所有用户都参与，按用户内 stratified_by_state 切分后合并，
  保证每折关占比≈池均，且同一用户不跨折泄漏（日粒度）。

本模块只做「用户->split 归属」与「日->split 归属」，不拼 X/y（由
pipeline.pooled_task 负责拼接），与 splits.py 的日粒度语义一致。
"""

from __future__ import annotations

import pandas as pd

from nilm.common.logging import get_logger
from nilm.preprocess.splits import _daily_on_off, _days, _ratio_assign

log = get_logger("preprocess.pooled_splits")


def louo_folds(user_keys: list[str]) -> list[tuple[list[str], str]]:
    """Leave-One-User-Out 折：[(train_users, test_user), ...]。"""
    keys = sorted(user_keys)
    if len(keys) < 2:
        raise ValueError(f"LOUO 至少需要 2 个用户，当前 {len(keys)}")
    return [([k for k in keys if k != held], held) for held in keys]


def _assign_days_by_state(days: list[pd.Timestamp], state_map: dict,
                          ratios: list[float]) -> dict[pd.Timestamp, str]:
    """按开/关分层，每层内按时间顺序切比例（复用 splits 语义）。"""
    by_state: dict[bool, list[pd.Timestamp]] = {True: [], False: []}
    for d in days:
        by_state[bool(state_map.get(d, False))].append(d)
    assign: dict[pd.Timestamp, str] = {}
    for is_on, ds in sorted(by_state.items(), key=lambda x: x[0]):
        assign.update(_ratio_assign(sorted(ds), ratios))
    return assign


def assign_pooled_splits(
    user_frames: dict[str, tuple[pd.DatetimeIndex, pd.Series, float]],
    train_users: list[str],
    test_users: list[str],
    val_ratio: float = 0.2,
    strategy: str = "stratified_by_state",
) -> dict[str, dict[str, str]]:
    """分配 (user_key, date_str) -> train/val/test。

    user_frames: {user_key: (index, target_phys, on_thr_w)}
    train_users: 参与 train/val 切分的池内用户；
    test_users: 整体归 test 的用户（LOUO 留出用户）；
    val_ratio: train 池内 val 占比（train=1-val_ratio）；
    strategy: 仅支持 stratified_by_state（池化默认）与 time。

    返回 {user_key: {date_str: split}}，date_str 为 YYYY-MM-DD。
    """
    if not (0.0 < val_ratio < 1.0):
        raise ValueError(f"val_ratio 必须在 (0,1): {val_ratio}")
    ratios = [1.0 - val_ratio, val_ratio, 0.0]  # train/val/test（test 由用户级决定）
    out: dict[str, dict[str, str]] = {}
    for uk in train_users:
        index, target, on_thr = user_frames[uk]
        days = _days(index)
        if strategy == "stratified_by_state":
            state_map = _daily_on_off(index, target, float(on_thr))
            if state_map is None:
                log.warning("[%s] 缺 target/on_thr，退化为 time 切分", uk)
                assign = _ratio_assign(days, ratios)
            else:
                assign = _assign_days_by_state(days, state_map, ratios)
        elif strategy == "time":
            assign = _ratio_assign(days, ratios)
        else:
            raise ValueError(f"pooled split_strategy 仅支持 stratified_by_state/time: {strategy}")
        out[uk] = {d.strftime("%Y-%m-%d"): assign[d] for d in days}
    for uk in test_users:
        index, _, _ = user_frames[uk]
        out[uk] = {d.strftime("%Y-%m-%d"): "test" for d in _days(index)}
    return out


def split_mask_for_index(index: pd.DatetimeIndex, day_assign: dict[str, str],
                         split: str) -> pd.Series:
    """由日归属表生成与 index 对齐的布尔 mask。"""
    dates = index.normalize().strftime("%Y-%m-%d")
    return pd.Series([day_assign.get(d) == split for d in dates], index=index)
