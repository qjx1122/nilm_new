"""用户 JSON 配置契约（指南 §12）：加载、键解析、优先级合并、字段校验。

规则（原文要点）：
- 入口：python run_batch_users.py --time-filter-config <path_to_json>；
- 配置键统一采用 user_key=<device>_<user>；单独 user_id 键必须通过明确映射层
  （_user_id_map）兼容，禁止隐式猜测（§12.1）；
- 优先级：具体 user_key 配置 > _default > 流水线硬编码默认值；
- 以下划线开头的顶级名称（_note_/_comment_/_default）不得作为用户数据名加载。
"""

from __future__ import annotations

import json
from pathlib import Path

from nilm.common.contracts import (CONFIG_RULES, RE_USER_DIR,
                                   is_reserved_config_key)
from nilm.common.logging import get_logger

log = get_logger("pipeline.user_config")


class UserConfigError(ValueError):
    """配置不符合 §12 契约。"""


def load_time_filter_config(path: str | Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if not isinstance(cfg, dict):
        raise UserConfigError(f"配置必须是 JSON 对象: {path}")
    return cfg


def list_user_keys(cfg: dict) -> list[str]:
    """枚举配置中的合法用户键：跳过 _ 前缀保留键；单独 user_id 键走映射层。"""
    id_map = cfg.get("_user_id_map") or {}
    if id_map and not isinstance(id_map, dict):
        raise UserConfigError("_user_id_map 必须是 {user_id: user_key} 映射对象")
    keys: list[str] = []
    for k in cfg:
        if is_reserved_config_key(k):
            continue
        if RE_USER_DIR.match(k):
            keys.append(k)
        elif k in id_map and RE_USER_DIR.match(str(id_map[k])):
            log.info("user_id 键 %r 经 _user_id_map 显式映射为 %r", k, id_map[k])
            keys.append(str(id_map[k]))
        else:
            raise UserConfigError(
                f"配置键 {k!r} 不是合法 user_key（<device>_<user>），"
                "也不在 _user_id_map 显式映射中（§12.1 禁止隐式猜测）")
    return sorted(set(keys))


def _validate_value(field: str, value):
    rule = CONFIG_RULES[field]
    if "choices" in rule and value not in rule["choices"]:
        raise UserConfigError(f"{field}={value!r} 不在允许集合 {rule['choices']}")
    if isinstance(value, bool):
        return value
    if "min" in rule or "max" in rule:
        if not isinstance(value, (int, float)):
            raise UserConfigError(f"{field} 必须为数值: {value!r}")
        if "min" in rule and value < rule["min"]:
            raise UserConfigError(f"{field}={value} 低于下限 {rule['min']}")
        if "max" in rule and value > rule["max"]:
            raise UserConfigError(f"{field}={value} 高于上限 {rule['max']}")
    return value


def _validate_split_ratios(value) -> list[float]:
    if not (isinstance(value, (list, tuple)) and len(value) == 3):
        raise UserConfigError(f"split_ratios 必须为 3 个数: {value!r}")
    ratios = [float(v) for v in value]
    if any(v < 0 for v in ratios) or abs(sum(ratios) - 1.0) > 1e-6:
        raise UserConfigError(f"split_ratios 必须非负且总和为 1: {value!r}")
    return ratios


def resolve_user_config(user_key: str, cfg: dict) -> dict:
    """按优先级合并并校验单个用户的配置（§12.1/§12.3）。"""
    merged: dict = {}
    provenance: dict = {}
    for layer_name, layer in (("default(硬编码)", {f: r["default"] for f, r in CONFIG_RULES.items()}),
                              ("_default", cfg.get("_default") or {}),
                              (user_key, cfg.get(user_key) or {})):
        for k, v in layer.items():
            merged[k] = v
            provenance[k] = layer_name

    # 字段校验（§12.3）
    for field in CONFIG_RULES:
        if field in merged and merged[field] is not None:
            if field == "split_ratios":
                merged[field] = _validate_split_ratios(merged[field])
            elif field in ("target_col",):
                if not isinstance(merged[field], str):
                    raise UserConfigError("target_col 必须为字符串")
            elif field in ("use_weather_features", "use_temp_based_season"):
                if not isinstance(merged[field], bool):
                    raise UserConfigError(f"{field} 必须为布尔值")
            elif field in ("post_min_on", "post_fill_short_off"):
                merged[field] = int(_validate_value(field, merged[field]))
            else:
                merged[field] = _validate_value(field, merged[field])

    # train/infer/splits 结构透传（时间过滤由 common.timefilter 执行）
    for section in ("train", "infer", "splits"):
        sec = (cfg.get(user_key) or {}).get(section) or (cfg.get("_default") or {}).get(section)
        if sec is not None:
            if not isinstance(sec, dict):
                raise UserConfigError(f"{user_key}.{section} 必须为对象")
            merged[section] = sec
            provenance[section] = (cfg.get(user_key) or {}).get(section) is not None and user_key or "_default"

    merged["_provenance"] = provenance
    merged["user_key"] = user_key
    return merged
