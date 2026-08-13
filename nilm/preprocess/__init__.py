"""模块②：预处理与样本工程（指南 §5/§8/§10/§11/§12.4）。

边界：不读原始文件（输入由 data_io 提供）、不做评估、不碰模型。
"""

from nilm.preprocess.align import align_frames, estimate_time_offset, resample_bus
from nilm.preprocess.base import Transformer
from nilm.preprocess.clean import Cleaner
from nilm.preprocess.dataset import DEFAULT_WINDOW, build_windows, drop_invalid_rows
from nilm.preprocess.features import build_features
from nilm.preprocess.scaling import TrainFitScaler
from nilm.preprocess.splits import build_split_masks, initial_split
from nilm.preprocess.target import build_target, parse_target_col, resolve_target_cols

__all__ = [
    "Transformer", "Cleaner",
    "resample_bus", "align_frames", "estimate_time_offset",
    "build_features",
    "DEFAULT_WINDOW", "build_windows", "drop_invalid_rows",
    "TrainFitScaler",
    "build_split_masks", "initial_split",
    "parse_target_col", "resolve_target_cols", "build_target",
]
