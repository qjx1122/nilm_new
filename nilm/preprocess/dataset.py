"""监督样本构建（指南 §10）：默认 L=96 滑窗（过去 24h），默认 Seq2Seq。

规则：窗口连续、输入与标签严格对齐；严重质量问题窗口不入训练；
保存样本索引、日期、窗口起止时间（§10）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from nilm.common.logging import get_logger
from nilm.common.schema import MODEL_STEP, segment_bounds

log = get_logger("preprocess.dataset")

DEFAULT_WINDOW = 96  # L=96，过去 24 小时（指南 §2.2/§10）


def drop_invalid_rows(features: pd.DataFrame, target: pd.Series) -> tuple[pd.DataFrame, pd.Series]:
    """对齐并剔除含 NaN 的行（长缺口/复合目标 NaN 不参与训练）。"""
    idx = features.index.intersection(target.index)
    f, y = features.loc[idx], target.loc[idx]
    mask = (~f.isna().any(axis=1)) & (~y.isna())
    dropped = int((~mask).sum())
    if dropped:
        log.info("剔除含 NaN 样本 %d 行", dropped)
    return f.loc[mask], y.loc[mask]


def build_windows(X: np.ndarray, y: np.ndarray, index: pd.DatetimeIndex,
                  window: int = DEFAULT_WINDOW, stride: int = 1,
                  mode: str = "seq2seq",
                  step: pd.Timedelta | None = MODEL_STEP) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    """滑窗样本（§10）。

    mode='seq2seq'  : y_w = 整个窗口的标签序列 (m, window)（默认，§10）
    mode='seq2point': y_w = 窗口末点标签 (m,)
    返回 (X_w, y_w, 样本元信息[窗口序号/起止时间/日期])。
    调用方必须传入单一划分内部的数据，保证窗口不跨 split（防泄漏）。

    时间连续性（§10「窗口必须连续」，W-1 修复 2026-09-10）：
    窗口只在时间连续段内构造（common.schema.segment_bounds，间隔 != step 视为
    间断）——间断两侧不拼窗；段尾不足一窗的点不产窗（记日志）。
    ``step=None`` 退化为纯位置滑窗（旧行为，仅建议合成数据测试使用）。
    """
    if mode not in ("seq2seq", "seq2point"):
        raise ValueError(f"未知窗口模式: {mode}（指南 §10：默认 Seq2Seq，可配置 Seq2Point）")
    if window < 2:
        raise ValueError("window 至少为 2")
    n = len(X)
    if n < window:
        raise ValueError(f"样本数 {n} 小于窗口长度 {window}（L={window}）")
    idx = pd.DatetimeIndex(index)
    if len(idx) != n:
        raise ValueError(f"index 长度 {len(idx)} 与样本数 {n} 不一致")

    bounds = segment_bounds(idx, step) if step is not None else [(0, n)]
    starts_list = [np.arange(s, e - window + 1, stride) for s, e in bounds
                   if e - s >= window]
    if not starts_list:
        raise ValueError(f"无长度 ≥ window({window}) 的时间连续段，无法构造窗口"
                         f"（共 {len(bounds)} 段，最长段 {max(e - s for s, e in bounds)} 点）")
    starts = np.concatenate(starts_list)
    n_windowed = len(starts) + (window - 1) * len(starts_list)
    if n_windowed < n:
        log.info("时间连续段构窗：%d 段中段尾不足一窗的 %d 个点不产窗（§10 窗口必须连续）",
                 len(bounds), n - n_windowed)
    Xw = np.stack([X[s:s + window] for s in starts], axis=0)
    if mode == "seq2seq":
        yw = np.stack([y[s:s + window] for s in starts], axis=0)
    else:
        yw = y[starts + window - 1]
    meta = pd.DataFrame({
        "sample_id": np.arange(len(starts)),
        "win_start": idx[starts],
        "win_end": idx[starts + window - 1],
        "date": idx[starts + window - 1].date,
    })
    return Xw, yw, meta


def build_flat(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """扁平样本（非序列基线模型用）。"""
    return X, y
