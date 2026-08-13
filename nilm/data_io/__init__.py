"""模块①：数据接入层。

职责：读取原始文件 → 列映射为标准 schema → 质量校验。
边界：不做特征工程、不感知模型。
"""

from nilm.data_io.base import BranchSource, BusSource, DataSource
from nilm.data_io.csv_source import CsvBranchSource, CsvBusSource

__all__ = ["DataSource", "BusSource", "BranchSource", "CsvBusSource", "CsvBranchSource"]
