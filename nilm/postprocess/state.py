"""开态后处理（指南 §12.3 字段：on_thr_w / post_min_on / post_fill_short_off）。

- on_thr_w            : 功率 ≥ 阈值判为开态（W）；
- post_min_on         : 开态段最短持续点数，短开段视为噪声置关；
- post_fill_short_off : 两个开态段之间的短关断（≤N 点）填充为开。
"""

from __future__ import annotations

import numpy as np


def power_to_state(power: np.ndarray, on_thr_w: float) -> np.ndarray:
    """功率 → 开态布尔序列。"""
    return np.asarray(power, dtype=np.float64) >= float(on_thr_w)


def _run_lengths(state: np.ndarray) -> list[tuple[int, int, bool]]:
    """返回 [(start, end_exclusive, value), ...] 的游程列表。"""
    runs = []
    n = len(state)
    i = 0
    while i < n:
        j = i
        while j < n and state[j] == state[i]:
            j += 1
        runs.append((i, j, bool(state[i])))
        i = j
    return runs


def enforce_min_on(state: np.ndarray, min_on: int) -> np.ndarray:
    """post_min_on：长度 < min_on 的开态段置为关。"""
    if min_on <= 0:
        return state.copy()
    out = state.copy()
    for s, e, v in _run_lengths(state):
        if v and (e - s) < min_on:
            out[s:e] = False
    return out


def fill_short_off(state: np.ndarray, max_off: int) -> np.ndarray:
    """post_fill_short_off：长度 ≤ max_off 且两侧均为开态的关断段填充为开。"""
    if max_off <= 0:
        return state.copy()
    out = state.copy()
    runs = _run_lengths(state)
    for k, (s, e, v) in enumerate(runs):
        if not v and (e - s) <= max_off and k > 0 and k < len(runs) - 1:
            if runs[k - 1][2] and runs[k + 1][2]:
                out[s:e] = True
    return out


def postprocess_state(power: np.ndarray, on_thr_w: float, min_on: int = 1,
                      fill_off: int = 3) -> np.ndarray:
    """组合流程：判开态 → 去短开 → 填短关。"""
    st = power_to_state(power, on_thr_w)
    st = enforce_min_on(st, min_on)
    st = fill_short_off(st, fill_off)
    return st
