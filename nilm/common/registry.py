"""通用注册表：模型注册表与指标注册表共同复用的内核组件。

用法::

    REG = Registry("model")

    @REG.register("ridge")
    class RidgeModel: ...

    model = REG.create("ridge", alpha=1.0)
"""

from __future__ import annotations

from typing import Callable, Generic, TypeVar

T = TypeVar("T")


class DuplicateNameError(ValueError):
    """注册名重复。"""


class Registry(Generic[T]):
    """名字 -> 工厂 的注册表。装饰器注册，工厂实例化。"""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._factories: dict[str, Callable[..., T]] = {}

    def register(self, name: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
        """装饰器：把类/函数以 ``name`` 注册进注册表。"""

        def decorator(factory: Callable[..., T]) -> Callable[..., T]:
            if name in self._factories:
                raise DuplicateNameError(f"{self.kind} 注册名重复: {name!r}")
            self._factories[name] = factory
            return factory

        return decorator

    def create(self, name: str, **kwargs) -> T:
        """按名实例化；未注册抛 KeyError（附可用名单，便于配置排错）。"""
        return self.get(name)(**kwargs)

    def get(self, name: str) -> Callable[..., T]:
        """返回注册的工厂/可调用对象本身（不实例化）——供指标这类「函数即实现」的场景。"""
        if name not in self._factories:
            raise KeyError(f"未注册的 {self.kind}: {name!r}，可用: {self.names()}")
        return self._factories[name]

    def names(self) -> list[str]:
        return sorted(self._factories)

    def __contains__(self, name: str) -> bool:
        return name in self._factories

    def __len__(self) -> int:
        return len(self._factories)
