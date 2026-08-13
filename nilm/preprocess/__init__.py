"""模块②：预处理与特征工程。

职责：清洗 / 288↔96 对齐 / 特征工程 / 数据集构造（时序划分防泄漏）。
边界：不读原始文件（输入由 data_io 提供）、不做评估。
"""

from nilm.preprocess.base import Transformer
from nilm.preprocess.clean import Cleaner
from nilm.preprocess.align import resample_bus_to_branch_freq, align_frames
from nilm.preprocess.features import build_features
from nilm.preprocess.dataset import time_split, build_supervised_matrix, build_windows

__all__ = [
    "Transformer",
    "Cleaner",
    "resample_bus_to_branch_freq",
    "align_frames",
    "build_features",
    "time_split",
    "build_supervised_matrix",
    "build_windows",
]
