"""模块②：预处理与样本工程（指南 §5/§8/§10/§11/§12.4）。

边界：不读原始文件（输入由 data_io 提供）、不做评估、不碰模型。
"""

from nilm.preprocess.align import align_frames, estimate_time_offset, resample_bus
from nilm.preprocess.base import Transformer
from nilm.preprocess.clean import Cleaner
from nilm.preprocess.dataset import DEFAULT_WINDOW, build_windows, drop_invalid_rows
from nilm.preprocess.features import build_features
from nilm.preprocess.normalize import (apply_pt_ct, cap_from_bus, compute_cap,
                                       denormalize_target, normalize_features,
                                       normalize_target)
from nilm.preprocess.pooled_splits import (assign_pooled_splits, louo_folds,
                                           split_mask_for_index)
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
    # pooled 通用模型专用（新增，不影响单用户链路）
    "compute_cap", "cap_from_bus", "normalize_features", "normalize_target",
    "denormalize_target", "apply_pt_ct", "louo_folds", "assign_pooled_splits", "split_mask_for_index",
]
