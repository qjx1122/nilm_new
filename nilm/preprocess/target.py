"""目标列契约（指南 §3.3/§12.3）：target_col 解析与复合目标构建。

规则（原文）：
- 支持 target_col=p1 或 p1+p2、p1+p2+p3 等复合目标；
- 复合列按行累加，skipna=False；任一组成列 NaN 时复合目标为 NaN；
- 目标字符串忽略大小写和空格；禁止重复分量。
"""

from __future__ import annotations

import pandas as pd

from nilm.common.logging import get_logger

log = get_logger("preprocess.target")


class TargetSpecError(ValueError):
    """target_col 不符合契约。"""


def parse_target_col(target_col: str) -> list[str]:
    """解析 target_col 字符串为分路列名列表（小写、去空格、禁重复）。"""
    if not isinstance(target_col, str) or not target_col.strip():
        raise TargetSpecError(f"target_col 非法: {target_col!r}")
    parts = [p.strip().lower() for p in target_col.split("+")]
    parts = [p for p in parts if p]
    if not parts:
        raise TargetSpecError(f"target_col 解析为空: {target_col!r}")
    if len(parts) != len(set(parts)):
        raise TargetSpecError(f"target_col 含重复分量: {target_col!r}")
    return parts


def resolve_target_cols(target_col: str | None, branch: pd.DataFrame) -> list[str]:
    """解析并校验列存在性；None 时走回退链（记录决策，见 STATUS.md）。

    回退链（指南未明确，工程实现并记录）：p1 → 首个 pN 列。
    """
    if target_col is None:
        p_cols = sorted(c for c in branch.columns if str(c).lower().startswith("p"))
        fallback = "p1" if "p1" in (c.lower() for c in branch.columns) else (p_cols[0] if p_cols else None)
        if fallback is None:
            raise TargetSpecError("分路数据无任何 pN 列，无法回退 target_col")
        log.warning("target_col 缺省，按回退链使用 %r", fallback)
        target_col = fallback
    cols = parse_target_col(target_col)
    lower_map = {str(c).lower(): c for c in branch.columns}
    missing = [c for c in cols if c not in lower_map]
    if missing:
        raise TargetSpecError(f"target_col 分量不存在于分路数据: {missing}")
    return [lower_map[c] for c in cols]


def build_target(branch: pd.DataFrame, cols: list[str]) -> pd.Series:
    """构建目标序列：单列直接取；复合列按行累加（skipna=False）。"""
    if len(cols) == 1:
        y = branch[cols[0]].astype(float)
    else:
        y = branch[cols].astype(float).sum(axis=1, skipna=False)
    y.name = "target"
    return y
