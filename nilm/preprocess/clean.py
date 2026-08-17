"""清洗：去重 / 负功率裁剪（§2.3 非负性，反送电需显式配置）/ 短缺口插值。"""

from __future__ import annotations

import pandas as pd

from nilm.common.logging import get_logger
from nilm.common.schema import is_power_column
from nilm.preprocess.base import Transformer

log = get_logger("preprocess.clean")


def _interp_short_gaps(s: pd.Series, max_gap: int) -> pd.Series:
    """只插补长度 ≤ max_gap 的内部缺口；长缺口整段保留 NaN。

    注意：pandas ``interpolate(limit=N)`` 的语义是「每个缺口最多填 N 个点」，
    长缺口仍会被填掉前 N 个点（部分填充）——这会把全天缺失天"漏"出少量
    伪有效点，污染缺失天统计与无效天判定。因此这里按缺口游程长度整段
    决定是否插补（要么全补要么全不补）。
    """
    if max_gap <= 0:
        return s
    isna = s.isna()
    if not isna.any():
        return s
    # 缺口游程长度：连续 NaN 段内每个位置标注该段总长
    grp = (isna != isna.shift()).cumsum()
    run_len = isna.groupby(grp).transform("sum")
    # 只填内部缺口（两侧有值才可线性插值；首尾缺口不外推）
    filled = s.interpolate(method="linear", limit_area="inside")
    out = s.copy()
    mask = isna & (run_len <= max_gap)
    out[mask] = filled[mask]
    return out


class Cleaner(Transformer):
    """通用时序清洗器。

    clip_negative  : 是否把有功功率列负值裁剪为 0（§2.3：原则上非负；
                     业务允许反送电时由配置关闭，禁止静默处理）
    max_gap_interp : 允许线性插值的最长连续缺口（点数），超过整段保留 NaN
    """

    def __init__(self, clip_negative: bool = True, max_gap_interp: int = 2) -> None:
        self.clip_negative = clip_negative
        self.max_gap_interp = max_gap_interp

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df[~df.index.duplicated(keep="first")].sort_index()

        if self.clip_negative:
            p_cols = [c for c in df.columns if is_power_column(c)]
            if p_cols:
                df[p_cols] = df[p_cols].clip(lower=0.0)

        num_cols = df.select_dtypes("number").columns
        for c in num_cols:
            df[c] = _interp_short_gaps(df[c], self.max_gap_interp)
        n_nan = int(df[num_cols].isna().sum().sum())
        if n_nan:
            log.warning("清洗后仍有 %d 个 NaN（长缺口，将在样本构建时剔除，§10）", n_nan)
        return df
