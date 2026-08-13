"""common.registry：注册 / 实例化 / 重名与未知名防护。"""

import pytest

from nilm.common.registry import DuplicateNameError, Registry


def test_register_create_names_contains():
    reg = Registry("demo")

    @reg.register("a")
    class A:
        def __init__(self, x=1):
            self.x = x

    assert "a" in reg
    assert len(reg) == 1
    assert reg.create("a", x=7).x == 7
    assert reg.names() == ["a"]


def test_duplicate_raises():
    reg = Registry("demo")

    @reg.register("a")
    class A: ...

    with pytest.raises(DuplicateNameError):
        @reg.register("a")
        class B: ...


def test_unknown_name_raises_with_available_list():
    reg = Registry("demo")
    with pytest.raises(KeyError, match="可用"):
        reg.create("nope")
