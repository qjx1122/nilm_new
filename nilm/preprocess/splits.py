"""数据集划分（指南 §11/§12.3/§12.4）：按日粒度切分，不随机打散时间点。

策略语义（§12.3 四种，工程解释记录于 STATUS.md 决策记录）：
- time             : 按时间顺序连续切分（前→后 = train→val→test）；
- stratified_day   : 按「星期几」分层，每个星期层内部按时间顺序切比例（默认）；
- stratified       : 按「月份」分层，每个月层内部按时间顺序切比例；
- global_stratified: 按日轮转交错分配（最大散布，保持日内连续）。

切分顺序（§12.4）：初始策略切分 → include 硬锚定 → 比例修复 → exclude 精确排除。
"""

from __future__ import annotations

import pandas as pd

from nilm.common.timefilter import anchor_splits


def _days(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
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


def initial_split(index: pd.DatetimeIndex, ratios: list[float],
                  strategy: str = "stratified_day") -> dict[str, pd.Series]:
    """初始策略切分，返回 {'train','val','test'} 布尔 mask（互斥）。"""
    if len(ratios) != 3 or abs(sum(ratios) - 1.0) > 1e-6:
        raise ValueError(f"split_ratios 必须为 3 个和为 1 的数: {ratios}")
    days = _days(index)
    assign: dict[pd.Timestamp, str] = {}

    if strategy == "time":
        assign = _ratio_assign(days, ratios)
    elif strategy == "stratified_day":
        by_dow: dict[int, list[pd.Timestamp]] = {}
        for d in days:
            by_dow.setdefault(d.dayofweek, []).append(d)
        for _, ds in sorted(by_dow.items()):
            assign.update(_ratio_assign(ds, ratios))
    elif strategy == "stratified":
        by_month: dict[int, list[pd.Timestamp]] = {}
        for d in days:
            by_month.setdefault(d.month, []).append(d)
        for _, ds in sorted(by_month.items()):
            assign.update(_ratio_assign(ds, ratios))
    elif strategy == "global_stratified":
        # 按目标比例轮转分配整日（展开成每 100 日的配额序列，确定性）
        seq = (["train"] * int(ratios[0] * 100) + ["val"] * int(ratios[1] * 100)
               + ["test"] * (100 - int(ratios[0] * 100) - int(ratios[1] * 100)))
        for i, d in enumerate(days):
            assign[d] = seq[i % 100]
    else:
        raise ValueError(f"未知 split_strategy: {strategy}")

    day_split = pd.Series([assign[d] for d in index.normalize()], index=index)
    return {name: day_split == name for name in ("train", "val", "test")}


def repair_empty_splits(masks: dict[str, pd.Series], index: pd.DatetimeIndex,
                        ratios: list[float]) -> dict[str, pd.Series]:
    """比例修复（§12.4 第 3 步的最小实现）：val/test 为空但配额>0 时，
    从 train 的末尾按日划拨，保证验证/测试集存在。"""
    days = _days(index)
    day_of = {d: i for i, d in enumerate(days)}
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
                      splits_spec: dict | None = None) -> dict[str, pd.Series]:
    """完整切分流程：初始策略 → 锚定 → 比例修复。"""
    masks = initial_split(index, ratios, strategy)
    masks = anchor_splits(index, masks, splits_spec)
    masks = repair_empty_splits(masks, index, ratios)
    return masks
