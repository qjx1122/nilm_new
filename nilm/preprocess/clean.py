"""清洗：去重 / 负功率裁剪 / 短缺口插值（长缺口保留 NaN，由数据集构造时剔除）。"""

from __future__ import annotations

import pandas as pd

from nilm.common.logging import get_logger
from nilm.preprocess.base import Transformer

log = get_logger("preprocess.clean")


class Cleaner(Transformer):
    """通用时序清洗器。

    参数
    ----
    clip_negative : 是否把有功功率列的负值裁剪为 0（计量回送等异常）
    max_gap_interp  : 允许线性插值的最长连续缺口（点数），超过则保留 NaN
    """

    def __init__(self, clip_negative: bool = True, max_gap_interp: int = 2) -> None:
        self.clip_negative = clip_negative
        self.max_gap_interp = max_gap_interp

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df[~df.index.duplicated(keep="first")].sort_index()

        if self.clip_negative:
            p_cols = [c for c in df.columns if str(c).startswith(("p_", "branch_"))]
            if p_cols:
                df[p_cols] = df[p_cols].clip(lower=0.0)

        num_cols = df.select_dtypes("number").columns
        df[num_cols] = df[num_cols].interpolate(method="linear", limit=self.max_gap_interp)
        n_nan = int(df[num_cols].isna().sum().sum())
        if n_nan:
            log.warning("清洗后仍有 %d 个 NaN（长缺口，将在数据集构造时剔除）", n_nan)
        return df
