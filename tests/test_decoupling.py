"""解耦隔离守卫（用户要求①）：用 AST 静态审计依赖方向，防止模块间横向耦合。

规则（与技术方案 §2.2 一致）：
- 业务模块只能依赖 ``nilm.common``（共享内核）与自身子模块；
- 业务模块之间禁止横向 import（data_io ↛ preprocess、models ↛ evaluation …）；
- 只有 ``nilm.pipeline``（编排层）允许组合全部模块。
一旦有人引入横向依赖，本测试立即失败。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "nilm"

# 业务功能模块：彼此隔离，只允许依赖 common
LEAF_MODULES = ["common", "data_io", "preprocess", "models", "evaluation",
                "reporting", "analysis", "events", "postprocess"]
# 编排层：唯一允许组合各模块的层
ORCHESTRATOR = "pipeline"


def _top_module(dotted: str) -> str:
    """'nilm.preprocess.align' -> 'preprocess'；非 nilm 返回 None。"""
    parts = dotted.split(".")
    if len(parts) < 2 or parts[0] != "nilm":
        return None
    return parts[1]


def _imports_of(pyfile: Path) -> set[str]:
    """返回该文件引用的顶层 nilm 模块名集合。"""
    tree = ast.parse(pyfile.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module:
                m = _top_module(node.module)
                if m:
                    found.add(m)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                m = _top_module(alias.name)
                if m:
                    found.add(m)
    return found


def test_leaf_modules_only_depend_on_common():
    violations = []
    for mod in LEAF_MODULES:
        mod_dir = ROOT / mod
        if not mod_dir.is_dir():
            continue
        allowed = {"common", mod}
        for py in mod_dir.rglob("*.py"):
            for dep in _imports_of(py):
                if dep not in allowed:
                    violations.append(f"nilm/{mod}/{py.name} -> nilm.{dep}")
    assert not violations, "发现横向依赖（违反解耦隔离）:\n" + "\n".join(violations)


def test_common_has_no_business_dependency():
    """common 是共享内核，不得反向依赖任何业务模块。"""
    violations = []
    for py in (ROOT / "common").rglob("*.py"):
        for dep in _imports_of(py):
            if dep != "common":
                violations.append(f"nilm/common/{py.name} -> nilm.{dep}")
    assert not violations, "common 反向依赖业务模块:\n" + "\n".join(violations)


def test_orchestrator_exists():
    assert (ROOT / ORCHESTRATOR).is_dir(), "编排层 pipeline 缺失"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
