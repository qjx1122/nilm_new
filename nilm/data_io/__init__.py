"""模块①：数据接入层（指南 §3/§4）。

职责：用户目录扫描与契约校验（discovery）→ CSV 加载与字段映射（csv_source）
→ schema/质量报告与门禁（validator）。
边界：不做特征工程、不感知模型；原始数据只读，绝不覆盖（§13）。
"""

from nilm.data_io.base import BranchLoader, BusLoader
from nilm.data_io.csv_source import CsvBranchLoader, CsvBusLoader
from nilm.data_io.discovery import UserScanResult, scan_root, scan_user_dir

__all__ = ["BusLoader", "BranchLoader", "CsvBusLoader", "CsvBranchLoader",
           "UserScanResult", "scan_root", "scan_user_dir"]
