"""对比报告生成（Markdown 优先，绘图依赖 matplotlib 为可选扩展）。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from nilm.common.logging import get_logger

log = get_logger("reporting")


def write_markdown_report(out_dir: str | Path, exp_name: str, table: pd.DataFrame,
                          summary: dict, notes: list[str] | None = None) -> Path:
    """写出对比报告 Markdown，返回文件路径。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "comparison.md"

    lines = [
        f"# 负荷辨识模型对比报告 — {exp_name}",
        "",
        f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
        f"- 综合最优模型：**{summary.get('overall_best')}**",
        "",
        "## 指标矩阵（宏平均）",
        "",
        table.round(4).to_markdown(),
        "",
        "## 各指标最优",
        "",
    ]
    for metric, model in summary.get("best_per_metric", {}).items():
        lines.append(f"- {metric}: `{model}`")
    if notes:
        lines += ["", "## 备注", ""] + [f"- {n}" for n in notes]

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("对比报告已写入 %s", path)
    return path
