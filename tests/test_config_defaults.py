"""配置一致性守卫：执行包 base-config 单文件加载（default.yaml 不参与）——
全局默认键必须同步进各 base 配置文件（2026-09-14 2844 试点首跑拦截复盘）。"""

from __future__ import annotations

import yaml


def _quality(path: str) -> dict:
    return yaml.safe_load(open(path, encoding="utf-8"))["quality"]


def test_day_gate_default_consistent_across_base_configs():
    assert _quality("configs/default.yaml")["day_gate"] is True      # ⑮ 全局默认（用户拍板）
    assert _quality("configs/base_t5.yaml")["day_gate"] is True      # 执行包 t5 档必须同口径
