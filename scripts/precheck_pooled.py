#!/usr/bin/env python3
"""pooled 泛化重验预检（模式B防引号坑专用）。

用法（PowerShell，无引号参数，复制即跑）：
    python scripts/precheck_pooled.py

背景：PowerShell 下 `python -c '... open("configs/...") ...'` 的双引号会被
原生命令解析剥离，python 收到 `open(configs/...)` 报 NameError: configs。
本脚本把预检逻辑入库，用户只跑无引号命令，规避该坑（协议：配置入库优于本地创建）。

检查项（期望值见打印，kW 口径）：
- time_filters.json 用户键（isinstance 守卫，跳过顶层字符串全局键）
- _default / 2844 / 778 的 pt_ratio/ct_ratio/branch_scale/on_thr_w/target_col
- data/trains、data/infers 目录与 scan_root ok 计数
- torch 版本、outputs/universal/p2/train 最新时间戳与非LOO判定（如有）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from nilm.data_io.discovery import scan_root  # noqa: E402
from nilm.pipeline.user_config import (load_time_filter_config,  # noqa: E402
                                       resolve_user_config)

ROOT = Path(__file__).resolve().parents[1]
TF = ROOT / "configs" / "time_filters.json"
USERS = ["800080252844_4206894986488", "800080270778_4200903422131"]


def main() -> int:
    print("=== pooled 预检 ===")
    print(f"cwd: {ROOT}")
    cfg = load_time_filter_config(TF)
    # 用户键：isinstance 守卫（顶层有 infer_model 等字符串全局键，不能直接 .get）
    keys = [k for k, v in cfg.items() if isinstance(v, dict) and not k.startswith("_")]
    print(f"用户键数: {len(keys)}")
    print(f"2844在列: {'800080252844_4206894986488' in keys} 778在列: {'800080270778_4200903422131' in keys}")
    d = cfg.get("_default") or {}
    print(f"_default pt/ct/branch: {d.get('pt_ratio')} {d.get('ct_ratio')} {d.get('branch_scale')} "
          f"(期望 1.0 1.0 1.0)")
    for uk in USERS:
        u = resolve_user_config(uk, cfg)
        print(f"{uk}: pt={u.get('pt_ratio')} ct={u.get('ct_ratio')} branch={u.get('branch_scale')} "
              f"on_thr={u.get('on_thr_w')} dec={u.get('decision_thr_w')} target={u.get('target_col')} "
              f"(期望 2844:100/120/1.0/10/30/p2 778:100/120/1.0/50/None跟on_thr/p2)")
    for sub in ("trains", "infers"):
        root = ROOT / "data" / sub
        if not root.is_dir():
            print(f"data/{sub}: 不存在（期望存在）")
            continue
        dirs = sorted(p.name for p in root.iterdir() if p.is_dir())
        print(f"data/{sub}: {len(dirs)}目录 {dirs[:8]}{'...' if len(dirs) > 8 else ''}")
        try:
            scans = scan_root(root, "train" if sub == "trains" else "infer")
            ok = [s.user_key for s in scans if s.ok]
            print(f"  scan ok {len(ok)}: {ok[:8]}{'...' if len(ok) > 8 else ''}")
        except Exception as e:  # noqa: BLE001
            print(f"  scan异常: {e}")
    try:
        import torch
        print(f"torch: {torch.__version__} (期望 2.3.1+cu121)")
    except Exception as e:  # noqa: BLE001
        print(f"torch导入失败: {e}")
    uni = ROOT / "outputs" / "universal" / "p2" / "train"
    if uni.is_dir():
        stamps = sorted(p.name for p in uni.iterdir() if p.is_dir())
        print(f"outputs/universal/p2/train: {stamps} (最新 {stamps[-1] if stamps else '无'})")
        nonloo = [p.name for p in uni.iterdir() if p.is_dir() and (p / "meta.json").exists()]
        print(f"  非LOO（顶层meta.json）: {sorted(nonloo)} (Step5须用此目录，勿用LOO)")
    else:
        print("outputs/universal/p2/train: 尚无（Step3/4后生成）")
    print("=== 预检完 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
