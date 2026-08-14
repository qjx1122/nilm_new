"""多用户批量执行编排（指南 §13）：扫描、任务生成、失败隔离、断点续跑、状态表。

规则（原文要点）：
- 自动扫描 data/trains/ 与 data/infers/ 一级用户目录，每个合法 user_key 独立任务；
- 单用户失败不得阻塞其他用户；必须支持断点续跑；
- 原始 data/trains 和 data/infers 不得移动、重命名或覆盖（本模块只读）；
- 批量状态表按接口字段输出 user_id（§0）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

from nilm.common.contracts import INFERS_DIR, Status, TRAINS_DIR, split_user_key
from nilm.common.logging import get_logger
from nilm.data_io.discovery import UserScanResult, scan_root
from nilm.pipeline.user_config import (UserConfigError, list_user_keys,
                                       load_time_filter_config, resolve_user_config)
from nilm.pipeline.user_task import (UserTaskResult, latest_done_dir,
                                     run_user_infer, run_user_train)

log = get_logger("pipeline.batch")

STATUS_COLUMNS = ["user_id", "user_key", "mode", "status", "message",
                  "output_dir", "finished_at"]


@dataclass
class BatchRow:
    user_id: str
    user_key: str
    mode: str
    status: str
    message: str = ""
    output_dir: str = ""
    finished_at: str = ""


def _row(user_key: str, mode: str, status: str, message: str = "",
         output_dir: str = "") -> BatchRow:
    _, user_id = split_user_key(user_key) or ("", user_key)
    return BatchRow(user_id, user_key, mode, status, message, output_dir,
                    datetime.now().isoformat(timespec="seconds"))


def _from_result(r: UserTaskResult) -> BatchRow:
    return _row(r.user_key, r.mode, r.status, r.message, r.output_dir or "")


def run_batch(time_filter_config_path: str | Path,
              base_config_path: str | Path = "configs/default.yaml",
              data_root: str | Path = "data",
              output_root: str | Path = "outputs",
              stages: tuple[str, ...] = ("train", "infer"),
              user_keys: list[str] | None = None,
              resume: bool = True,
              force: bool = False) -> dict:
    """批量（或单用户）执行入口。返回 {'batch_dir','status_csv','rows'}。

    force=True：强制重新训练/推理——忽略已完成产物（``_DONE``）重新执行，
    优先级高于 resume；产物写入新的时间戳目录，不覆盖历史产物。
    """
    data_root, output_root = Path(data_root), Path(output_root)
    tcfg = load_time_filter_config(time_filter_config_path)
    with open(base_config_path, "r", encoding="utf-8") as f:
        base_cfg = yaml.safe_load(f)

    import numpy as np, random
    seed = int(base_cfg.get("seed", 42))
    random.seed(seed)
    np.random.seed(seed)

    batch_dir = output_root / "batch" / datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_dir.mkdir(parents=True, exist_ok=True)
    (batch_dir / "time_filter_config.json").write_text(
        Path(time_filter_config_path).read_text(encoding="utf-8"), encoding="utf-8")
    (batch_dir / "base_config.yaml").write_text(
        yaml.safe_dump(base_cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")

    if force:
        log.info("强制重跑模式（--force）：忽略已完成产物，全部重新训练/推理")

    cfg_keys = set(list_user_keys(tcfg))
    rows: list[BatchRow] = []

    # —— 扫描两个数据根（§13），无效目录同样进状态表
    scans: dict[str, dict[str, UserScanResult]] = {
        "train": {r.user_key: r for r in scan_root(data_root / TRAINS_DIR, "train")},
        "infer": {r.user_key: r for r in scan_root(data_root / INFERS_DIR, "infer")},
    }
    wanted = set(user_keys) if user_keys else None
    for mode, scan_map in scans.items():
        for uk, r in scan_map.items():
            if not r.ok:
                if wanted is not None and uk not in wanted:
                    continue  # 单用户模式只报告指定 user_key
                rows.append(_row(uk, mode, r.status, r.message))
    for mode in stages:
        scan_map = scans[mode]
        keys = sorted(scan_map.keys() | (cfg_keys if mode == "train" else set()))
        for uk in keys:
            if wanted is not None and uk not in wanted:
                continue
            scan = scan_map.get(uk)
            if scan is None:
                # train 根缺目录 = 无训练数据；infer 根缺目录 = 不参与推理
                if mode == "train" and uk in cfg_keys:
                    rows.append(_row(uk, mode, Status.DATA_MISSING_BUS,
                                     f"data/{TRAINS_DIR}/{uk} 不存在"))
                continue
            if not scan.ok:
                continue  # 已在上面记录过状态
            try:
                user_cfg = resolve_user_config(uk, tcfg)
            except UserConfigError as e:
                rows.append(_row(uk, mode, Status.FAILED, f"配置错误: {e}"))
                continue

            # 断点续跑：已有 _DONE 的跳过（§13）；force 强制重跑（优先级更高）
            if resume and not force:
                done = latest_done_dir(output_root / uk / mode)
                if done is not None:
                    rows.append(_row(uk, mode, Status.SKIPPED_RESUME, f"已完成: {done}", str(done)))
                    continue

            if mode == "train":
                result = run_user_train(uk, scan, user_cfg, base_cfg, output_root)
            else:
                result = run_user_infer(uk, scan, user_cfg, base_cfg, output_root)
            rows.append(_from_result(result))
            log.info("批量[%s] %s -> %s %s", mode, uk, result.status, result.message)

    # —— 批量状态表（§0：按接口字段输出 user_id）
    table = pd.DataFrame([asdict(r) for r in rows])[STATUS_COLUMNS]
    status_csv = batch_dir / "batch_status.csv"
    table.to_csv(status_csv, index=False, encoding="utf-8")
    ok_n = int((table["status"] == Status.OK).sum())
    log.info("批量执行完成：%d 行，OK %d，状态表 -> %s", len(table), ok_n, status_csv)
    return {"batch_dir": str(batch_dir), "status_csv": str(status_csv), "rows": rows,
            "summary": table["status"].value_counts().to_dict()}
