#!/usr/bin/env python3
"""通用（pooled）负荷辨识入口 —— 新增链路，不影响单用户链路。

    # 训练通用模型（部分用户池化）
    python scripts/run_pooled.py --time-filter-config configs/time_filters.json \
        --base-config configs/base_universal.yaml --target-col p1 --stage train

    # LOUO 泛化评估（留一用户）
    python scripts/run_pooled.py --time-filter-config configs/time_filters.json \
        --base-config configs/base_universal.yaml --target-col p1 --stage train --loo

    # 新用户零样本推理（只有总线亦可）
    python scripts/run_pooled.py --time-filter-config configs/time_filters.json \
        --base-config configs/base_universal.yaml --target-col p1 --stage infer \
        --user-key <device>_<user>

    # 指定池内用户子集
    python scripts/run_pooled.py ... --users <uk1> <uk2> ...

产物：outputs/universal/<target_col>/train/<ts>/（模型/全局Scaler/LOUO汇总）
      outputs/universal/<target_col>/infer/<ts>/（inference_result.csv）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

from nilm.common.logging import get_logger, setup_logging  # noqa: E402
from nilm.data_io.discovery import scan_root  # noqa: E402
from nilm.pipeline.pooled_task import run_pooled_infer, run_pooled_train  # noqa: E402
from nilm.pipeline.user_config import (load_time_filter_config,  # noqa: E402
                                       resolve_user_config)

log = get_logger("cli.pooled")


def main() -> int:
    parser = argparse.ArgumentParser(description="通用（pooled）负荷辨识：部分用户池化训练 + 新用户零样本推理")
    parser.add_argument("--time-filter-config", required=True, help="用户 JSON 配置（§12）")
    parser.add_argument("--base-config", default="configs/base_universal.yaml")
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--output-root", default="outputs")
    parser.add_argument("--target-col", default=None, help="目标通道（默认取 base pooled.target_col）")
    parser.add_argument("--stage", choices=["train", "infer", "all"], default="train")
    parser.add_argument("--users", nargs="*", default=None, help="池内用户子集（默认全部 trains 用户）")
    parser.add_argument("--user-key", default=None, help="推理目标用户（--stage infer 必填）")
    parser.add_argument("--universal-dir", default=None, help="通用模型目录（默认最新 universal train）")
    parser.add_argument("--loo", action="store_true", help="留一用户（LOUO）泛化评估")
    args = parser.parse_args()

    setup_logging()
    tcfg = load_time_filter_config(args.time_filter_config)
    with open(args.base_config, "r", encoding="utf-8") as f:
        base_cfg = yaml.safe_load(f)
    tc = args.target_col or (base_cfg.get("pooled") or {}).get("target_col") or "p1"

    data_root, output_root = Path(args.data_root), Path(args.output_root)
    stages = ("train", "infer") if args.stage == "all" else (args.stage,)

    if "train" in stages:
        scans = {r.user_key: r for r in scan_root(data_root / "trains", "train") if r.ok}
        if args.users:
            scans = {k: v for k, v in scans.items() if k in set(args.users)}
        if not scans:
            log.error("trains 下无可用用户（data_root=%s）", data_root / "trains")
            return 1
        user_cfgs = {}
        for uk in scans:
            try:
                user_cfgs[uk] = resolve_user_config(uk, tcfg)
            except Exception as e:
                log.warning("跳过 %s 配置错误: %s", uk, e)
        res = run_pooled_train(sorted(scans.keys()), scans, user_cfgs, base_cfg,
                               output_root, target_col=tc, loo=args.loo)
        log.info("pooled train -> %s %s (%s)", res.status, res.message, res.output_dir)
        if res.status != "OK":
            return 1

    if "infer" in stages:
        if not args.user_key:
            log.error("--stage infer 必须指定 --user-key")
            return 1
        # 推理用户可能在 trains 或 infers 下，优先 infers
        infer_scans = {r.user_key: r for r in scan_root(data_root / "infers", "infer")}
        train_scans = {r.user_key: r for r in scan_root(data_root / "trains", "train")}
        scan = infer_scans.get(args.user_key) or train_scans.get(args.user_key)
        if scan is None or not scan.ok:
            log.error("找不到推理用户 %s 的可用数据", args.user_key)
            return 1
        try:
            user_cfg = resolve_user_config(args.user_key, tcfg)
        except Exception as e:
            log.error("推理用户配置错误: %s", e)
            return 1
        res = run_pooled_infer(args.user_key, scan, user_cfg, base_cfg, output_root,
                               universal_dir=args.universal_dir, target_col=tc)
        log.info("pooled infer -> %s %s (%s)", res.status, res.message, res.output_dir)
        if res.status != "OK":
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
