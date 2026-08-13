"""配置加载与组件工厂装配（依赖注入入口）。

所有「用什么数据 / 什么模型 / 什么指标」的决策都在 YAML 配置里，
这里只负责把配置翻译成组件实例。
"""

from __future__ import annotations

import random
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml

from nilm.common.logging import get_logger
from nilm.data_io import CsvBranchSource, CsvBusSource, DataSource

log = get_logger("pipeline.context")


def load_config(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError(f"配置文件格式错误（应为映射）: {path}")
    return cfg


def set_global_seed(cfg: dict) -> None:
    seed = int(cfg.get("seed", 42))
    random.seed(seed)
    np.random.seed(seed)


def make_output_dir(cfg: dict, output_root: str | Path | None = None) -> Path:
    """outputs/<exp_name>/<timestamp>/ —— 一次实验一个目录，保证可复现归档。"""
    root = Path(output_root or cfg.get("output_dir", "outputs"))
    exp = cfg.get("experiment_name", "exp")
    out = root / exp / datetime.now().strftime("%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    return out


def build_datasource(cfg: dict) -> DataSource:
    """按配置构造数据源（当前为 CSV 实现；换存储只改这里与配置）。"""
    d = cfg["data"]
    bus = CsvBusSource(d["bus_path"], timestamp_col=d.get("timestamp_col", "timestamp"),
                       column_map=d.get("bus_column_map"))
    branch = CsvBranchSource(d["branch_path"], timestamp_col=d.get("timestamp_col", "timestamp"),
                             column_map=d.get("branch_column_map"))

    class _Joined:
        """把两个源拼成 DataSource 接口（编排层面向接口编程）。"""

        def load_bus(self, start=None, end=None):
            return bus.load_bus(start, end)

        def load_branch(self, start=None, end=None):
            return branch.load_branch(start, end)

    return _Joined()
