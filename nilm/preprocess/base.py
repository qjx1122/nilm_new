"""预处理变换抽象接口：可串联（pipeline 编排层按配置顺序调用）。"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class Transformer(ABC):
    """无状态/轻状态变换统一接口。默认 fit_transform = transform。"""

    @abstractmethod
    def transform(self, df: pd.DataFrame) -> pd.DataFrame: ...

    def fit_transform(self, df: pd.DataFrame) -> pd.DataFrame:
        return self.transform(df)
