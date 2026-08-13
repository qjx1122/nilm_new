"""多数据源用户数据批量合并（《多数据源用户数据批量合并脚本-功能需求文档》）。

两级串行（§3）：
  阶段一 单数据源内部合并（§4.1）：逐用户、逐通道，迭代两两合并；
  阶段二 多数据源跨目录合并（§4.2）：跨源同用户同通道，完全复用 §4.1 规则。

核心约束（§5）：
  - 仅同终端号/同用户号/同通道号参与合并，通道相互独立；
  - 任意一轮时间区间重叠 → 立即终止该用户通道批次，告警跳过、不强制合并、不覆盖；
  - 先内源后跨源，串行执行；
  - 新文件沿用原始命名规范，仅更新起止时间字段；
  - 输出严格复刻「数据源根目录 / 终端号_用户号」层级，不扁平化；
  - 原始数据只读（指南 §13：不得移动/重命名/覆盖）。

文件名解析复用 ``common.contracts.parse_bus_filename``（RE_BUS，指南 §3.2 原文正则）。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from nilm.common.contracts import (RE_USER_DIR, parse_bus_filename,
                                   parse_merge_filename)
from nilm.common.logging import get_logger

log = get_logger("data_io.merge")

TS_COL_CANDIDATES = ("event_time", "time", "timestamp")


class OverlapError(RuntimeError):
    """同组两个文件时间区间重叠（§4.1：立即终止该用户通道合并）。"""

    def __init__(self, f1: "BusFile", f2: "BusFile") -> None:
        self.f1, self.f2 = f1, f2
        super().__init__(
            f"时间区间重叠: [{f1.start} ~ {f1.end}]({f1.path.name}) "
            f"<-> [{f2.start} ~ {f2.end}]({f2.path.name})")


@dataclass
class BusFile:
    """RE_BUS 命名的待合并文件（文件名解析出的维度信息，§2.2）。"""
    path: Path
    device: str
    user: str
    ch: int
    start: date
    end: date
    source: str = ""          # 所属数据源（展示/日志用）

    @property
    def user_key(self) -> str:
        return f"{self.device}_{self.user}"

    @property
    def group_key(self) -> tuple[str, int]:
        """合并匹配依据：终端号+用户号+通道号完全一致（§2.2）。"""
        return (self.user_key, self.ch)

    def standard_name(self, start: date | None = None, end: date | None = None) -> str:
        """标准命名（§5 命名约束）：仅更新起止时间字段，YYmmdd。"""
        s = (start or self.start).strftime("%y%m%d")
        e = (end or self.end).strftime("%y%m%d")
        return f"e241_{self.device}_{self.user}-Ch{self.ch}-{s}-{e}.csv"


def _parse_ymd(code: str) -> date:
    """YYmmdd → date（§2.2：两位年份）。"""
    return date(2000 + int(code[0:2]), int(code[2:4]), int(code[4:6]))


def discover_source(source: Path) -> tuple[list[BusFile], list[Path]]:
    """扫描单个数据源根目录（§2.1 层级）。

    返回 (合法待合并文件列表, 跳过的文件列表)。
    只认「终端号_用户号」用户目录与**严格格式**文件名（无后缀）；其余文件不参与合并。
    """
    if not source.is_dir():
        raise FileNotFoundError(f"数据源目录不存在: {source}")
    found: list[BusFile] = []
    skipped: list[Path] = []
    for user_dir in sorted(p for p in source.iterdir() if p.is_dir()):
        if not RE_USER_DIR.match(user_dir.name):
            log.warning("[%s] 跳过非法用户目录: %s", source.name, user_dir.name)
            continue
        for f in sorted(user_dir.glob("*.csv")):
            meta = parse_merge_filename(f.name)
            if meta is None:
                if parse_bus_filename(f.name) is not None:
                    log.warning("[%s] 文件名带后缀，不符合合并严格格式（需求文档 §2.2），不参与合并: %s",
                                source.name, f.name)
                skipped.append(f)
                continue
            if user_dir.name != f"{meta.device}_{meta.user}":
                log.warning("[%s] 文件名身份与目录不一致，跳过: %s", source.name, f)
                skipped.append(f)
                continue
            found.append(BusFile(path=f, device=meta.device, user=meta.user, ch=meta.ch,
                                 start=_parse_ymd(meta.start), end=_parse_ymd(meta.end),
                                 source=source.name))
    return found, skipped


def ranges_overlap(a_start: date, a_end: date, b_start: date, b_end: date) -> bool:
    """闭区间重叠判定（§4.1 时间校验）。"""
    return a_start <= b_end and b_start <= a_end


def _detect_ts_col(df: pd.DataFrame) -> str:
    for c in TS_COL_CANDIDATES:
        if c in df.columns:
            return c
    return str(df.columns[0])


def merge_two_csvs(p1: Path, p2: Path, out_path: Path) -> dict:
    """内容合并：按时间列拼接、去重、排序（§4.1 合并两份数据）。

    输出保留原始时间列名（event_time/time 等），不改变列结构契约。
    """
    d1, d2 = pd.read_csv(p1), pd.read_csv(p2)
    ts1, ts2 = _detect_ts_col(d1), _detect_ts_col(d2)
    out_ts = ts1 if ts1 == ts2 else "timestamp"
    if ts1 != ts2 or list(d1.columns) != list(d2.columns):
        log.warning("列结构差异（%s vs %s），按列并集合并，缺失补空", list(d1.columns)[:4], list(d2.columns)[:4])
    d1, d2 = d1.rename(columns={ts1: "timestamp"}), d2.rename(columns={ts2: "timestamp"})
    d1["timestamp"] = pd.to_datetime(d1["timestamp"], errors="coerce")
    d2["timestamp"] = pd.to_datetime(d2["timestamp"], errors="coerce")
    merged = pd.concat([d1, d2], ignore_index=True).dropna(subset=["timestamp"])
    n_before = len(merged)
    merged = merged.drop_duplicates(subset=["timestamp"], keep="first").sort_values("timestamp")
    n_dup = n_before - len(merged)
    if n_dup:
        log.warning("合并去重 %d 个重复时间戳（保留先出现者）: %s + %s", n_dup, p1.name, p2.name)
    merged = merged.rename(columns={"timestamp": out_ts})
    out_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(out_path, index=False)
    return {"rows_out": len(merged), "duplicates_removed": n_dup}


class _TempArtifact:
    """迭代合并中的中间产物（文件 + 累计时间区间）。"""

    def __init__(self, path: Path, start: date, end: date) -> None:
        self.path, self.start, self.end = path, start, end


def merge_group(files: list[BusFile], out_dir: Path, tmp_dir: Path,
                phase: str, keep_single: bool = True) -> tuple[Path | None, dict]:
    """单组合并（§4.1 迭代两两合并）。

    返回 (输出文件路径或 None[跳过/无需输出], 信息 dict)。
    单文件：无需合并，按 keep_single 决定是否保留原文件（直接复制，原名不变）。
    多文件：按起始时间排序后迭代合并；任一轮重叠 → 抛 OverlapError（由调用方告警跳过整组）。
    """
    files = sorted(files, key=lambda f: (f.start, f.end))
    if len(files) == 1:
        if not keep_single:
            return None, {"action": "single_skipped_by_option"}
        out = out_dir / files[0].path.name
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(files[0].path, out)
        return out, {"action": "single_kept"}

    tmp_dir.mkdir(parents=True, exist_ok=True)
    current = _TempArtifact(files[0].path, files[0].start, files[0].end)
    consumed = [files[0].path]
    for f in files[1:]:
        if ranges_overlap(current.start, current.end, f.start, f.end):
            raise OverlapError(
                BusFile(current.path, files[0].device, files[0].user, files[0].ch,
                        current.start, current.end, files[0].source), f)
        nxt = tmp_dir / f"{phase}_{files[0].user_key}_Ch{files[0].ch}_{len(consumed)}.csv"
        merge_two_csvs(current.path, f.path, nxt)
        consumed.append(f.path)
        current = _TempArtifact(nxt, min(current.start, f.start), max(current.end, f.end))

    final_name = files[0].standard_name(current.start, current.end)
    out = out_dir / final_name
    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(current.path), out)
    return out, {"action": "merged", "n_files": len(files),
                 "range": [str(current.start), str(current.end)]}


def _warn_overlap(phase: str, err: OverlapError, member_paths: list[Path]) -> dict:
    """告警日志（§6.2：精准记录数据源路径、用户目录、文件名、冲突时间区间）。"""
    log.warning("%s 时间重叠，终止该用户通道合并并跳过整组（不强制合并、不覆盖数据）:\n    %s\n    组内文件: %s",
                phase, err, ", ".join(str(p) for p in member_paths))
    return {"status": "SKIPPED_OVERLAP", "conflict": str(err),
            "files": [str(p) for p in member_paths]}


def run_merge(sources: list[str | Path], output_root: str | Path,
              keep_original: bool = True) -> dict:
    """两级合并总入口（§3 串行：先内源、后跨源，不可逆序/并行）。

    输出结构（§5 目录结构约束）::

        <output_root>/<数据源名>/<终端号_用户号>/...   # 阶段一：单源合并结果（复刻原层级）
        <output_root>/cross_source/<终端号_用户号>/...  # 阶段二：合并后用户数据目录——
                                                       #   跨源合并结果；仅存在于单一数据源的用户，
                                                       #   其文件直接作为合并后用户数据文件放入
        <output_root>/logs/merge_run.log               # 运行日志（区分内源/跨源）
        <output_root>/logs/merge_warnings.log          # 异常告警日志
    """
    from nilm.common.logging import setup_logging  # 编排内聚：日志产物随输出目录

    sources = [Path(s) for s in sources]
    if len(sources) < 1:
        raise ValueError("至少需要一个数据源根目录")
    if len({s.resolve() for s in sources}) != len(sources):
        raise ValueError("数据源路径重复")
    output_root = Path(output_root)
    log_dir = output_root / "logs"
    setup_logging(log_dir / "merge_run.log")
    wlog = logging_warnings(log_dir / "merge_warnings.log")
    try:
        return _run_merge_impl(sources, output_root, log_dir, wlog, keep_original)
    finally:
        wlog.close()


def _run_merge_impl(sources: list[Path], output_root: Path, log_dir: Path,
                    wlog, keep_original: bool) -> dict:
    # 数据源目录名冲突处理（保留层级前提下加序号，记录日志）
    src_dirs: dict[str, Path] = {}
    for i, s in enumerate(sources, 1):
        name = s.name if s.name not in src_dirs else f"{s.name}_{i}"
        src_dirs[name] = s

    tmp_root = output_root / "_tmp_merge"
    report: dict = {"phase1_intra_source": {}, "phase2_cross_source": {},
                    "warnings": 0, "sources": {n: str(p) for n, p in src_dirs.items()}}

    # ============ 阶段一：单数据源内部合并（§4.1） ============
    log.info("===== 阶段一：单数据源内部合并（%d 个数据源）=====", len(src_dirs))
    stage1_files: list[BusFile] = []   # 供阶段二跨源匹配（每个源每组的输出文件）
    for src_name, src_path in src_dirs.items():
        found, skipped = discover_source(src_path)
        if skipped:
            log.info("[%s][内源] 非合并目标文件 %d 个（如分路 CSV），不参与合并", src_name, len(skipped))
        groups: dict[tuple[str, int], list[BusFile]] = {}
        for f in found:
            groups.setdefault(f.group_key, []).append(f)

        for (user_key, ch), members in sorted(groups.items()):
            out_dir = output_root / src_name / user_key
            try:
                out, info = merge_group(members, out_dir, tmp_root / src_name,
                                        phase=f"intra_{src_name}", keep_single=keep_original)
                if out is not None:
                    meta = parse_bus_filename(out.name)
                    stage1_files.append(BusFile(
                        path=out, device=meta.device, user=meta.user, ch=meta.ch,
                        start=_parse_ymd(meta.start), end=_parse_ymd(meta.end), source=src_name))
                    log.info("[%s][内源] %s Ch%d: %s -> %s", src_name, user_key, ch, info["action"], out.name)
                elif info.get("action") == "single_skipped_by_option":
                    # 单文件组未按选项保留到单源输出区，但仍需参与阶段二：
                    # 若该用户仅存在于单一数据源，其文件将直接成为合并后用户数据文件
                    m0 = members[0]
                    stage1_files.append(BusFile(path=m0.path, device=m0.device, user=m0.user,
                                                ch=m0.ch, start=m0.start, end=m0.end,
                                                source=src_name))
                report["phase1_intra_source"].setdefault(src_name, []).append(
                    {"user_key": user_key, "ch": ch, "status": "OK", **info})
            except OverlapError as e:
                report["warnings"] += 1
                wlog.write(f"[内源][{src_name}] 用户目录={user_key} 通道=Ch{ch} {e}\n"
                           f"    组内文件: {', '.join(str(m.path) for m in members)}\n")
                report["phase1_intra_source"].setdefault(src_name, []).append(
                    _warn_overlap("[内源]", e, [m.path for m in members]) |
                    {"user_key": user_key, "ch": ch})

    # ============ 阶段二：多数据源跨目录合并（§4.2） ============
    log.info("===== 阶段二：多数据源跨目录合并 =====")
    xgroups: dict[tuple[str, int], list[BusFile]] = {}
    for f in stage1_files:
        xgroups.setdefault(f.group_key, []).append(f)
    cross_dir = output_root / "cross_source"
    for (user_key, ch), members in sorted(xgroups.items()):
        srcs = {m.source for m in members}
        if len(srcs) < 2:
            # 用户目录仅存在于单一数据源（其余源中不存在该用户目录）：
            # 待合并文件直接作为合并后用户数据文件，放入合并后用户数据目录（用户要求）
            src_name = members[0].source
            out, info = merge_group(members, cross_dir / user_key, tmp_root / "cross",
                                    phase=f"passthrough_{user_key}_Ch{ch}", keep_single=True)
            log.info("[跨源] %s Ch%d 仅存在于数据源 %s（其余源无此用户目录），"
                     "直接作为合并后用户数据文件 -> %s",
                     user_key, ch, src_name, out.name if out else "-")
            report["phase2_cross_source"].setdefault(user_key, []).append(
                {**(info or {}), "ch": ch, "status": "OK", "action": "copied_single_source",
                 "sources": sorted(srcs), "output": str(out) if out else None})
            continue
        try:
            out, info = merge_group(members, cross_dir / user_key, tmp_root / "cross",
                                    phase=f"cross_{user_key}_Ch{ch}", keep_single=True)
            log.info("[跨源] %s Ch%d: %s 个数据源合并 -> %s", user_key, ch, len(srcs),
                     out.name if out else "-")
            report["phase2_cross_source"].setdefault(user_key, []).append(
                {"ch": ch, "status": "OK", "sources": sorted(srcs), **(info or {})})
        except OverlapError as e:
            report["warnings"] += 1
            wlog.write(f"[跨源] 用户目录={user_key} 通道=Ch{ch} {e}\n"
                       f"    数据源: {', '.join(sorted(srcs))}\n"
                       f"    组内文件: {', '.join(str(m.path) for m in members)}\n")
            report["phase2_cross_source"].setdefault(user_key, []).append(
                _warn_overlap("[跨源]", e, [m.path for m in members]) |
                {"ch": ch, "sources": sorted(srcs)})

    # 清理中间产物
    shutil.rmtree(tmp_root, ignore_errors=True)
    n_ok = sum(1 for src in report["phase1_intra_source"].values() for r in src if r["status"] == "OK")
    log.info("合并完成：内源组 %d 个 OK / 告警 %d 次；跨源组 %d 个；产物根目录 %s",
             n_ok, report["warnings"], len(report["phase2_cross_source"]), output_root)
    import json
    (log_dir / "merge_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return report


def logging_warnings(path: Path):
    """告警日志文件句柄（独立于运行日志，§6.2 异常告警日志）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    return open(path, "a", encoding="utf-8")
