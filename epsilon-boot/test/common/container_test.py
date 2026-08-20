"""核心注册与解析功能检查点测试。

验证 Container 的 register/resolve 基本功能、Scope 行为、
循环依赖检测、未注册类型诊断和 Provider 异常包装。
"""

from typing import Protocol

import pytest

from common.container import Container
from common.container_errors import (
    CircularDependencyError,
    DependencyNotRegisteredError,
    ProviderError,
)
from common.container_models import Scope

# ── 测试用 Protocol 和实现 ──


class GreeterPort(Protocol):
    def greet(self) -> str: ...


class HelloGreeter:
    def greet(self) -> str:
        return "hello"


class ByeGreeter:
    def greet(self) -> str:
        return "bye"


# ── Singleton 行为 ──


async def test_singleton_returns_same_instance():
    c = Container()
    c.register(GreeterPort, HelloGreeter, Scope.SINGLETON)
    a = await c.resolve(GreeterPort)
    b = await c.resolve(GreeterPort)
    assert a is b


# ── Transient 行为 ──


async def test_transient_returns_different_instances():
    c = Container()
    c.register(GreeterPort, HelloGreeter, Scope.TRANSIENT)
    a = await c.resolve(GreeterPort)
    b = await c.resolve(GreeterPort)
    assert a is not b


# ── 后注册覆盖先注册 ──


async def test_last_registration_wins():
    c = Container()
    c.register(GreeterPort, HelloGreeter)
    c.register(GreeterPort, ByeGreeter)
    result = await c.resolve(GreeterPort)
    assert result.greet() == "bye"


# ── 异步 Provider ──


async def test_async_provider():
    async def make_greeter():
        return HelloGreeter()

    c = Container()
    c.register(GreeterPort, make_greeter)
    result = await c.resolve(GreeterPort)
    assert result.greet() == "hello"


# ── 未注册类型 ──


async def test_unregistered_type_raises():
    c = Container()
    with pytest.raises(DependencyNotRegisteredError) as exc_info:
        await c.resolve(GreeterPort)
    assert "GreeterPort" in str(exc_info.value)


# ── 循环依赖检测 ──


async def test_circular_dependency_detected():
    class A: ...

    class B: ...

    c = Container()

    async def make_a():
        return await c.resolve(B)

    async def make_b():
        return await c.resolve(A)

    c.register(A, make_a)
    c.register(B, make_b)

    with pytest.raises(CircularDependencyError):
        await c.resolve(A)


# ── Provider 异常包装 ──


async def test_provider_error_wraps_original():
    def bad_provider():
        raise ValueError("boom")

    c = Container()
    c.register(GreeterPort, bad_provider)
    with pytest.raises(ProviderError) as exc_info:
        await c.resolve(GreeterPort)
    assert "boom" in str(exc_info.value)
    assert exc_info.value.cause.__class__ is ValueError


# ── 依赖链传递解析 ──


async def test_dependency_chain_resolution():
    class ServiceA:
        def __init__(self, b: "ServiceB"):
            self.b = b

    class ServiceB:
        def __init__(self):
            self.value = 42

    c = Container()
    c.register(ServiceB, ServiceB)

    async def make_a():
        b = await c.resolve(ServiceB)
        return ServiceA(b)

    c.register(ServiceA, make_a)

    a = await c.resolve(ServiceA)
    assert a.b.value == 42
    # Singleton: resolving again should give same chain
    a2 = await c.resolve(ServiceA)
    assert a is a2
    assert a.b is a2.b


# ── has_async_resource 查询 API（需求 6.3.3） ──


async def test_has_async_resource_returns_false_when_not_registered():
    """未注册的资源名返回 False。"""
    c = Container()
    assert c.has_async_resource("redis") is False
    assert c.has_async_resource("local_persistence") is False


async def test_has_async_resource_returns_true_after_registration():
    """已注册的资源名返回 True。"""
    c = Container()

    async def _init() -> None:
        pass

    async def _cleanup() -> None:
        pass

    c.register_async_resource("local_persistence", _init, _cleanup)
    assert c.has_async_resource("local_persistence") is True
    # 不相关名字仍返回 False
    assert c.has_async_resource("redis") is False
