#!/usr/bin/env python3
"""多数据源用户数据批量合并脚本（《多数据源用户数据批量合并脚本-功能需求文档》）。

用法（§6.1 输入参数）：
    python scripts/merge_user_data.py --sources <源1> <源2> [<源3> ...] \
        [--output-root outputs/merged] [--log-dir <日志目录>] \
        [--no-keep-original]

两级串行（§3）：先单数据源内部合并（§4.1），再多数据源跨目录合并（§4.2）。
时间区间重叠 → 告警并跳过该用户通道（不强制合并、不覆盖数据）；原始数据只读。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nilm.common.logging import get_logger, setup_logging  # noqa: E402
from nilm.data_io.merge import run_merge  # noqa: E402

log = get_logger("cli.merge")


def main() -> int:
    parser = argparse.ArgumentParser(description="多数据源用户数据批量合并（先内源、后跨源）")
    parser.add_argument("--sources", nargs="+", required=True,
                        help="多个数据源根目录路径（必填，§6.1）")
    parser.add_argument("--output-root", default="outputs/merged",
                        help="结果输出目录（可选，默认 outputs/merged）")
    parser.add_argument("--log-dir", default=None,
                        help="日志存储目录（可选，默认 <output-root>/logs）")
    parser.add_argument("--no-keep-original", action="store_true",
                        help="不保留单文件组的原始文件到输出（默认保留；原始输入目录永不改动）")
    args = parser.parse_args()

    setup_logging()
    report = run_merge(args.sources, output_root=args.output_root,
                       keep_original=not args.no_keep_original)

    # 自定义日志目录（§6.1 可选项）：复制一份到指定位置
    if args.log_dir:
        dst = Path(args.log_dir)
        dst.mkdir(parents=True, exist_ok=True)
        for f in (Path(args.output_root) / "logs").glob("*"):
            shutil.copy2(f, dst / f.name)
        log.info("日志已复制到 %s", dst)

    log.info("内源合并: %s", {s: len(rs) for s, rs in report["phase1_intra_source"].items()})
    log.info("跨源合并组: %d | 告警: %d", len(report["phase2_cross_source"]), report["warnings"])
    log.info("详细报告: %s", Path(args.output_root) / "logs" / "merge_report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
