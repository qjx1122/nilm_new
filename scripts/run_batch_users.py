#!/usr/bin/env python3
"""批量用户执行入口（指南 §12 规定入口）：

    python run_batch_users.py --time-filter-config <path_to_json>

扩展参数：
    --user-key <device>_<user>   单用户模式（只执行指定 user_key）
    --stage train|infer|all      执行阶段（默认 all = train→infer）
    --no-resume                  禁用断点续跑
    --force                      强制重新训练/推理（忽略已完成产物，优先级高于断点续跑）
    --data-root / --output-root / --base-config

单用户与多用户走同一代码路径（§13）：单用户 = users=[一个 key] 的批量执行。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nilm.common.logging import get_logger, setup_logging  # noqa: E402
from nilm.pipeline.batch import run_batch  # noqa: E402

log = get_logger("cli.batch")


def main() -> int:
    parser = argparse.ArgumentParser(description="NILM 多算法模型批量用户执行（指南 V2.1 §12/§13）")
    parser.add_argument("--time-filter-config", required=True, help="用户 JSON 配置（§12）")
    parser.add_argument("--user-key", default=None,
                        help="单用户模式：只执行该 user_key（<device>_<user>）")
    parser.add_argument("--stage", choices=["train", "infer", "all"], default="all")
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--output-root", default="outputs")
    parser.add_argument("--base-config", default="configs/default.yaml")
    parser.add_argument("--no-resume", action="store_true", help="禁用断点续跑")
    parser.add_argument("--force", action="store_true",
                        help="强制重新训练/推理：忽略已完成产物（_DONE）重新执行，"
                             "产物写入新时间戳目录（优先级高于断点续跑）")
    args = parser.parse_args()

    setup_logging()
    stages = ("train", "infer") if args.stage == "all" else (args.stage,)
    user_keys = [args.user_key] if args.user_key else None
    mode_desc = f"单用户[{args.user_key}]" if args.user_key else "多用户批量"
    log.info("启动 %s 执行: stages=%s, config=%s, force=%s",
             mode_desc, stages, args.time_filter_config, args.force)

    info = run_batch(args.time_filter_config, base_config_path=args.base_config,
                     data_root=args.data_root, output_root=args.output_root,
                     stages=stages, user_keys=user_keys, resume=not args.no_resume,
                     force=args.force)
    log.info("状态汇总: %s", info["summary"])
    log.info("批量状态表: %s", info["status_csv"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
