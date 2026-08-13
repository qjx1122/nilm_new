#!/usr/bin/env python3
"""CLI 入口：负荷辨识流水线。

用法示例：
    python scripts/run_pipeline.py --config configs/default.yaml --stage all
    python scripts/run_pipeline.py --config configs/default.yaml --stage train
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允许从仓库根目录直接运行脚本
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nilm.common.logging import get_logger, setup_logging  # noqa: E402
from nilm.pipeline import context, runner  # noqa: E402

log = get_logger("cli")


def main() -> int:
    parser = argparse.ArgumentParser(description="NILM 负荷辨识流水线")
    parser.add_argument("--config", default="configs/default.yaml", help="实验配置 YAML")
    parser.add_argument("--stage", choices=["all", "train", "evaluate", "compare"],
                        default="all", help="执行阶段")
    args = parser.parse_args()

    cfg = context.load_config(args.config)
    setup_logging()
    log.info("配置: %s | 阶段: %s", args.config, args.stage)

    if args.stage == "all":
        info = runner.run_all(cfg)
        log.info("综合最优模型: %s", info["summary"].get("overall_best"))
        return 0

    # 分阶段执行：train / evaluate / compare（evaluate 依赖 train 产物，同目录续跑）
    prep, out_dir, model_paths = runner.run_train(cfg)
    if args.stage == "train":
        return 0
    results, out_dir = runner.run_evaluate(cfg, prep, out_dir, model_paths)
    if args.stage == "evaluate":
        return 0
    runner.run_compare(cfg, results, out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
