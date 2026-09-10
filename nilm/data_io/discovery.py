"""用户目录扫描与文件名契约校验（指南 §3.1/§3.2/§3.3/§13）。

职责：把 data/trains|infers 下的一级目录解析成「合法用户任务」或「带状态码的错误」。
边界：只读目录结构与文件名，不读取 CSV 内容；状态码全部来自 common.contracts.Status。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from nilm.common.contracts import (RE_USER_DIR, Status, parse_branch_filename,
                                   parse_bus_filename, split_user_key)
from nilm.common.logging import get_logger

log = get_logger("data_io.discovery")


@dataclass
class UserScanResult:
    """一个 <device>_<user> 目录的扫描结论。"""
    user_key: str
    mode: str                       # "train" | "infer"
    status: str = Status.OK
    message: str = ""
    bus_files: list[Path] = field(default_factory=list)
    branch_files: list[Path] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == Status.OK


def scan_user_dir(user_dir: Path, mode: str) -> UserScanResult:
    """扫描单个用户目录（指南 §13 逐项状态码）。

    train 模式：总线与分路标签都必须存在；
    infer 模式：总线必须存在，分路可选（仅离线评估，不参与生产推理，§3.1）。
    """
    user_key = user_dir.name
    res = UserScanResult(user_key=user_key, mode=mode)

    if not user_dir.is_dir():
        res.status, res.message = Status.INVALID_USER_DIR, f"不是目录: {user_dir}"
        return res
    if not RE_USER_DIR.match(user_key):
        res.status, res.message = Status.INVALID_USER_DIR, f"目录名不匹配 RE_USER_DIR: {user_key}"
        return res
    device, user = split_user_key(user_key)

    csvs = sorted(p for p in user_dir.iterdir() if p.suffix.lower() == ".csv")
    bus_files: list[Path] = []
    branch_files: list[Path] = []
    for p in csvs:
        bus_meta = parse_bus_filename(p.name)
        if bus_meta is not None:
            # device/user 必须与父目录一致（§3.2/§3.3）
            if bus_meta.device != device or bus_meta.user != user:
                res.status = Status.IDENTITY_MISMATCH
                res.message = f"总线文件名身份与目录不一致: {p.name}"
                return res
            bus_files.append(p)
            continue
        br_meta = parse_branch_filename(p.name)
        if br_meta is not None:
            if br_meta.user != user:
                res.status = Status.IDENTITY_MISMATCH
                res.message = f"分路文件名身份与目录不一致: {p.name}"
                return res
            branch_files.append(p)
            continue
        res.status = Status.INVALID_FILENAME
        res.message = f"文件名既不符合 RE_BUS 也不符合 RE_BR: {p.name}"
        return res

    res.bus_files, res.branch_files = bus_files, branch_files
    if not bus_files:
        res.status, res.message = Status.DATA_MISSING_BUS, "缺少总线 CSV"
        return res
    if mode == "train" and not branch_files:
        res.status, res.message = Status.DATA_MISSING_BRANCH_LABEL, "缺少分路标签 CSV"
        return res
    return res


def scan_root(root: Path, mode: str) -> list[UserScanResult]:
    """扫描一级用户目录；每个目录独立判定，互不阻塞（§13）。"""
    if not root.is_dir():
        log.warning("数据根目录不存在: %s", root)
        return []
    results = [scan_user_dir(p, mode) for p in sorted(root.iterdir()) if p.is_dir()]
    n_ok = sum(r.ok for r in results)
    log.info("扫描 %s（%s）：%d 个目录，合法 %d", root, mode, len(results), n_ok)
    return results
