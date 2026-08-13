"""CSV 数据源实现（指南 §3.2/§3.3/§4）：按 Ch 关联、字段映射、倍率配置化。

要点：
- ChN 只是通道标识，物理含义必须通过 field_map 配置确认（§3.2）；
- 同一用户多个 Ch 文件按时间轴和 channel_id 关联；
- 文件名时间只用于初步识别，最终以 CSV 内实际时间戳为准（校验并记录）；
- CT/PT 倍率必须配置化（§4），经 field_map 的 multiplier 应用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from nilm.common.contracts import parse_branch_filename, parse_bus_filename
from nilm.common.logging import get_logger
from nilm.common.schema import BUS_REQUIRED, branch_power_columns
from nilm.data_io.base import BranchLoader, BusLoader

log = get_logger("data_io.csv")

BUS_TIMESTAMP_COL = "event_time"   # §3.2
BR_TIMESTAMP_COL = "time"          # §3.3


def _parse_ymd(s: str) -> pd.Timestamp | None:
    """文件名中的 6 位日期（yymmdd）→ Timestamp，仅用于初步识别。"""
    try:
        return pd.Timestamp(f"20{s[0:2]}-{s[2:4]}-{s[4:6]}")
    except Exception:
        return None


def _read_ts(path: Path, ts_col: str, sentinels: list | None = None) -> pd.DataFrame:
    df = pd.read_csv(path)
    if ts_col not in df.columns:
        raise ValueError(f"{path.name} 缺少时间列 {ts_col!r}（现有列: {list(df.columns)[:12]}…）")
    if sentinels:  # 哨兵值（如 INT32_MIN/MAX）→ NaN，禁止静默当真实值（§4）
        df = df.replace({s: np.nan for s in sentinels})
    df[ts_col] = pd.to_datetime(df[ts_col], errors="coerce")
    df = df.dropna(subset=[ts_col]).set_index(ts_col).sort_index()
    return df[~df.index.duplicated(keep="first")]


class CsvBusLoader(BusLoader):
    """总线 CSV 加载器。

    field_map 结构（每个标准字段一条目）::

        {"ua": {"ch": 1, "column": "load_iden_data0", "multiplier": 1.0, "unit": "V"}, ...}

    未给出 multiplier 的字段按 1.0 处理并标记 DATA_UNIT_UNKNOWN 提示（§4：禁止静默转换）。
    ``derive_phase_from_ptotal=True`` 时：无分相功率映射则按 ptotal/3 均分三相
    （临时假设，报告中显式标记 DERIVED_EQUAL_SPLIT，待点位表确认）。
    """

    def load(self, files: Sequence[Path], field_map: dict,
             sentinels: list | None = None,
             derive_phase_from_ptotal: bool = False) -> tuple[pd.DataFrame, dict]:
        report: dict = {"kind": "bus", "fields": {}, "issues": [], "file_time_check": []}

        # 1) 按 Ch 分组读入（同一 Ch 多个文件按时间拼接）
        ch_frames: dict[int, pd.DataFrame] = {}
        for f in files:
            meta = parse_bus_filename(f.name)
            assert meta is not None  # discovery 已校验
            df = _read_ts(f, BUS_TIMESTAMP_COL, sentinels)
            df.attrs["channel_id"] = meta.ch
            if meta.ch in ch_frames:
                ch_frames[meta.ch] = pd.concat([ch_frames[meta.ch], df]).sort_index()
                ch_frames[meta.ch] = ch_frames[meta.ch][~ch_frames[meta.ch].index.duplicated(keep="first")]
            else:
                ch_frames[meta.ch] = df
            # 文件名时间初步识别 vs CSV 实际时间戳校验（§3.2）
            start, end = _parse_ymd(meta.start), _parse_ymd(meta.end)
            if start is not None and len(df):
                inside = (df.index.normalize() >= start).any()
                report["file_time_check"].append(
                    {"file": f.name, "ok": bool(inside),
                     "csv_range": [str(df.index.min()), str(df.index.max())]})
                if not inside:
                    log.warning("文件 %s 的 CSV 时间戳与文件名时间范围明显不符", f.name)

        # 2) 字段映射（物理含义由配置确认，禁止假设 Ch 含义）
        out = pd.DataFrame(index=sorted(set().union(*[set(fr.index) for fr in ch_frames.values()]))
                           ) if ch_frames else pd.DataFrame()
        for std in BUS_REQUIRED + ["ptotal"]:
            spec = (field_map or {}).get(std)
            if spec is None:
                if std in BUS_REQUIRED:
                    report["issues"].append(f"字段映射缺失: {std}（SCHEMA_UNCONFIRMED）")
                continue
            ch, col = int(spec["ch"]), spec["column"]
            if ch not in ch_frames:
                report["issues"].append(f"字段 {std} 指向不存在的通道 Ch{ch}")
                continue
            frame = ch_frames[ch]
            if col not in frame.columns:
                report["issues"].append(f"Ch{ch} 缺少列 {col!r}（字段 {std}）")
                continue
            mult = float(spec.get("multiplier", 1.0))
            if "multiplier" not in spec:
                report["issues"].append(f"字段 {std} 未配置倍率/单位，标记 DATA_UNIT_UNKNOWN")
            out[std] = frame[col] * mult
            report["fields"][std] = {"ch": ch, "column": col, "multiplier": mult,
                                     "unit": spec.get("unit", "UNKNOWN")}

        # 3) 分相功率派生（临时假设，显式标记）：pa/pb/pc 缺失但 ptotal 存在时按 /3 均分
        if derive_phase_from_ptotal and "ptotal" in out.columns:
            for ph in ("pa", "pb", "pc"):
                if ph not in out.columns:
                    out[ph] = out["ptotal"] / 3.0
                    report["fields"][ph] = {"derived": "ptotal/3 (DERIVED_EQUAL_SPLIT, 待点位表确认)"}
                    report["issues"].append(f"{ph} 由 ptotal/3 均分派生（临时假设，DERIVED_EQUAL_SPLIT）")
        # pa..pc 补齐后重判必备字段缺失问题
        report["issues"] = [i for i in report["issues"]
                            if not (i.startswith("字段映射缺失") and any(
                                i.startswith(f"字段映射缺失: {ph}") and ph in out.columns
                                for ph in ("pa", "pb", "pc")))]
        out.index.name = "timestamp"
        return out, report


class CsvBranchLoader(BranchLoader):
    """分路 CSV 加载器：time 索引 + p1..pN（单位 W）。"""

    def load(self, files: Sequence[Path], sentinels: list | None = None) -> tuple[pd.DataFrame, dict]:
        report: dict = {"kind": "branch", "fields": {}, "issues": [], "file_time_check": []}
        frames = []
        for f in files:
            meta = parse_branch_filename(f.name)
            assert meta is not None
            df = _read_ts(f, BR_TIMESTAMP_COL, sentinels)
            p_cols = branch_power_columns(df)
            if not p_cols:
                report["issues"].append(f"{f.name} 缺少 pN 功率列")
                continue
            frames.append(df[p_cols])
            report["fields"].update({c: "W" for c in p_cols})
            start = _parse_ymd(meta.start)
            if start is not None and len(df):
                report["file_time_check"].append(
                    {"file": f.name, "ok": bool((df.index.normalize() >= start).any()),
                     "csv_range": [str(df.index.min()), str(df.index.max())]})
        if not frames:
            return pd.DataFrame(), report
        out = pd.concat(frames).sort_index()
        out = out[~out.index.duplicated(keep="first")]
        out.index.name = "timestamp"
        return out, report
