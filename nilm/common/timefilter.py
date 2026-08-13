"""时间过滤引擎（指南 §12.4）：include/exclude 闭区间语义与切分锚定。

规则（原文）：
- include/exclude 元素为 [start, end] 闭区间；
- YYYY-MM-DD 自动扩展为当天 00:00:00 ~ 23:59:59；
- 先取 include 并集；include 为空表示所有有效行；再执行 exclude；
- splits.train/val/test 各自支持 include/exclude；
- include 冲突按 train → val → test 优先。
"""

from __future__ import annotations

import pandas as pd

Interval = tuple[pd.Timestamp, pd.Timestamp]


def parse_endpoint(s: str, is_end: bool) -> pd.Timestamp:
    """YYYY-MM-DD 扩展为当天 00:00:00（start）或 23:59:59（end）。"""
    ts = pd.Timestamp(s)
    if len(str(s).strip()) == 10:  # 纯日期
        ts = ts + pd.Timedelta(hours=23, minutes=59, seconds=59) if is_end else ts
    return ts


def parse_intervals(spec: list | None) -> list[Interval]:
    """把 [[start, end], ...] 解析为闭区间列表。"""
    out: list[Interval] = []
    for item in spec or []:
        if not (isinstance(item, (list, tuple)) and len(item) == 2):
            raise ValueError(f"时间区间必须为 [start, end] 二元组: {item!r}")
        a, b = parse_endpoint(str(item[0]), is_end=False), parse_endpoint(str(item[1]), is_end=True)
        if a > b:
            raise ValueError(f"区间起点晚于终点: {item!r}")
        out.append((a, b))
    return out


def include_mask(index: pd.DatetimeIndex, include: list[Interval]) -> pd.Series:
    """include 并集；空 include = 全部有效行。"""
    mask = pd.Series(False, index=index)
    if not include:
        return pd.Series(True, index=index)
    for a, b in include:
        mask |= (index.to_series() >= a) & (index.to_series() <= b)
    return mask


def apply_time_filter(index: pd.DatetimeIndex, include: list | None,
                      exclude: list | None) -> pd.Series:
    """先 include 并集（空=全部），再 exclude 剔除。返回布尔 mask。"""
    mask = include_mask(index, parse_intervals(include))
    for a, b in parse_intervals(exclude):
        s = index.to_series()
        mask &= ~((s >= a) & (s <= b))
    return mask


def filter_dataframe(df: pd.DataFrame, include: list | None,
                     exclude: list | None) -> pd.DataFrame:
    """对 DataFrame（DatetimeIndex）应用时间过滤。"""
    return df[apply_time_filter(df.index, include, exclude)]


def anchor_splits(index: pd.DatetimeIndex, base: dict[str, pd.Series],
                  splits_spec: dict | None) -> dict[str, pd.Series]:
    """splits.train/val/test 的 include 硬锚定与 exclude 精确排除（§12.4 切分顺序）。

    base : {'train','val','test'} -> 初始策略切分的布尔 mask（互斥）
    返回新的互斥 mask 字典。include 冲突按 train → val → test 优先。
    """
    splits_spec = splits_spec or {}
    masks = {k: base[k].copy() for k in ("train", "val", "test")}

    # include 硬锚定：冲突按 train → val → test 优先（§12.4）。
    # claimed 记录「已被某级 include 主张」的点，低优先级不得抢占。
    claimed = pd.Series(False, index=index)
    for name in ("train", "val", "test"):
        inc = parse_intervals((splits_spec.get(name) or {}).get("include"))
        if not inc:
            continue
        full_want = include_mask(index, inc)
        want = full_want & ~claimed
        for other in ("train", "val", "test"):
            if other != name:
                masks[other] = masks[other] & ~want
        masks[name] = masks[name] | want
        claimed = claimed | full_want

    # exclude 精确排除
    for name in ("train", "val", "test"):
        exc = parse_intervals((splits_spec.get(name) or {}).get("exclude"))
        for a, b in exc:
            s = index.to_series()
            masks[name] = masks[name] & ~((s >= a) & (s <= b))
    return masks
