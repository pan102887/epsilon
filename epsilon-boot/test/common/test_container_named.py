"""容器命名依赖属性测试和单元测试。

验证 Container 的命名依赖注册/解析功能的正确性属性，包括：
- 无名称注册/解析往返（向后兼容）
- 命名注册/解析往返
- 注册独立性
- 重复命名注册覆盖
- 命名依赖的 Scope 行为
- 未注册命名依赖的错误信息质量
- FastAPI 集成层正确传递名称
- 边界条件和集成验证
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from common.container import Container, container, inject, inject_all
from common.container_errors import (
    CircularDependencyError,
    DependencyNotRegisteredError,
)
from common.container_models import Scope, default_dependency_name, make_registry_key

# ── 测试辅助 ──

_type_counter = 0


def _make_protocol(label: str = "Proto") -> type[object]:
    """动态创建唯一的测试抽象类型，避免测试间类型冲突。"""
    global _type_counter
    _type_counter += 1
    return type(f"{label}_{_type_counter}", (), {})


def _make_provider(
    value: object | None = None,
) -> tuple[Callable[[], object], object]:
    """创建返回固定值的同步 Provider。"""
    sentinel = value if value is not None else object()

    def provider() -> object:
        return sentinel

    return provider, sentinel


# ── Property 1: 无名称注册/解析往返（向后兼容） ──


class TestProperty1UnnamedRoundTrip:
    """Feature: container-named-dependency, Property 1: 默认名称注册/解析往返。

    验证未显式命名注册后，以默认方式解析应返回该 Provider 创建的实例，
    且注册表键使用类型名称的 Spring 风格 lowerCamel 默认名称。
    """

    @settings(max_examples=100)
    @given(data=st.data())
    async def test_unnamed_register_resolve_round_trip(self, data: st.DataObject):
        """默认名称注册后解析，返回 Provider 创建的实例和命名元组键。"""
        c = Container()
        proto = _make_protocol()
        provider, sentinel = _make_provider()

        c.register(proto, provider)
        result = await c.resolve(proto)

        assert result is sentinel
        assert make_registry_key(proto) in c.capture_state().registry
        assert make_registry_key(proto) == (proto, default_dependency_name(proto))

    def test_default_dependency_name_uses_spring_decapitalize_rules(self):
        """普通类名首字母小写，连续大写开头的缩略词保持不变。"""

        class ModelRegistryPort: ...

        class URLService: ...

        class A1Service: ...

        class A: ...

        assert default_dependency_name(ModelRegistryPort) == "modelRegistryPort"
        assert default_dependency_name(URLService) == "URLService"
        assert default_dependency_name(A1Service) == "a1Service"
        assert default_dependency_name(A) == "a"


# ── Property 2: 命名注册/解析往返 ──


class TestProperty2NamedRoundTrip:
    """Feature: container-named-dependency, Property 2: 命名注册/解析往返。

    验证以 (type, name) 方式注册后，以相同的 (type, name) 方式解析
    应返回该 Provider 创建的实例。
    """

    @settings(max_examples=100)
    @given(name=st.text(min_size=1, max_size=20))
    async def test_named_register_resolve_round_trip(self, name: str):
        """命名注册后以相同名称解析，返回 Provider 创建的实例。"""
        c = Container()
        proto = _make_protocol()
        provider, sentinel = _make_provider()

        c.register(proto, provider, name=name)
        result = await c.resolve(proto, name=name)

        assert result is sentinel


# ── Property 3: 注册独立性 ──


class TestProperty3RegistrationIndependence:
    """Feature: container-named-dependency, Property 3: 注册独立性。

    验证同一类型同时存在无名称注册和多个不同名称的注册时，
    每个注册独立存在，解析任一注册不影响其他注册的结果。
    """

    @settings(max_examples=100)
    @given(
        names=st.lists(
            st.text(min_size=1, max_size=20),
            min_size=2,
            max_size=5,
            unique=True,
        )
    )
    async def test_registrations_are_independent(self, names: list[str]):
        """无名称注册和多个命名注册互不影响，各自返回独立实例。"""
        c = Container()
        proto = _make_protocol()
        if default_dependency_name(proto) in names:
            return

        # 无名称注册
        default_provider, default_sentinel = _make_provider()
        c.register(proto, default_provider)

        # 多个命名注册
        named_sentinels: dict[str, object] = {}
        for n in names:
            provider, sentinel = _make_provider()
            c.register(proto, provider, name=n)
            named_sentinels[n] = sentinel

        # 验证无名称解析
        assert await c.resolve(proto) is default_sentinel

        # 验证各命名解析独立
        for n in names:
            assert await c.resolve(proto, name=n) is named_sentinels[n]

        # 验证所有实例互不相同
        all_sentinels = [default_sentinel, *list(named_sentinels.values())]
        assert len({id(s) for s in all_sentinels}) == len(all_sentinels)


# ── Property 4: 重复命名注册覆盖 ──


class TestProperty4DuplicateOverride:
    """Feature: container-named-dependency, Property 4: 重复命名注册覆盖。

    验证以相同的 (type, name) 组合注册两次时，第二次注册覆盖第一次，
    后续解析返回第二个 Provider 创建的实例。
    """

    @settings(max_examples=100)
    @given(name=st.text(min_size=1, max_size=20))
    async def test_duplicate_named_registration_overrides(self, name: str):
        """相同 (type, name) 注册两次，解析返回第二个 Provider 的实例。"""
        c = Container()
        proto = _make_protocol()

        provider1, sentinel1 = _make_provider()
        provider2, sentinel2 = _make_provider()

        c.register(proto, provider1, name=name)
        c.register(proto, provider2, name=name)

        result = await c.resolve(proto, name=name)
        assert result is sentinel2
        assert result is not sentinel1


# ── Property 5: 命名依赖的 Scope 行为 ──


class TestProperty5ScopeBehavior:
    """Feature: container-named-dependency, Property 5: 命名依赖的 Scope 行为。

    验证 Singleton 命名依赖多次解析返回同一实例，
    Transient 命名依赖每次解析返回不同实例。
    """

    @settings(max_examples=100)
    @given(name=st.text(min_size=1, max_size=20))
    async def test_singleton_named_returns_same_instance(self, name: str):
        """Singleton 命名依赖多次解析返回同一实例。"""
        c = Container()
        proto = _make_protocol()

        c.register(proto, lambda: object(), Scope.SINGLETON, name=name)

        r1 = await c.resolve(proto, name=name)
        r2 = await c.resolve(proto, name=name)
        assert r1 is r2

    @settings(max_examples=100)
    @given(name=st.text(min_size=1, max_size=20))
    async def test_transient_named_returns_different_instances(self, name: str):
        """Transient 命名依赖每次解析返回不同实例。"""
        c = Container()
        proto = _make_protocol()

        c.register(proto, lambda: object(), Scope.TRANSIENT, name=name)

        r1 = await c.resolve(proto, name=name)
        r2 = await c.resolve(proto, name=name)
        assert r1 is not r2


# ── Property 6: 未注册命名依赖的错误信息质量 ──


class TestProperty6ErrorMessageQuality:
    """Feature: container-named-dependency, Property 6: 未注册命名依赖的错误信息质量。

    验证解析未注册的命名依赖时，错误消息包含请求的类型名称、
    依赖名称和所有已注册名称列表。
    """

    @settings(max_examples=100)
    @given(
        registered_names=st.lists(
            st.text(
                alphabet=st.characters(whitelist_categories=("L", "N", "P", "S")),
                min_size=1,
                max_size=20,
            ),
            min_size=1,
            max_size=5,
            unique=True,
        ),
        missing_name=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N", "P", "S")),
            min_size=1,
            max_size=20,
        ),
    )
    async def test_error_message_contains_all_info(
        self, registered_names: list[str], missing_name: str
    ):
        """未注册命名依赖的错误消息包含类型名、请求名称和已注册名称列表。"""
        # 确保 missing_name 不在 registered_names 中
        if missing_name in registered_names:
            return  # 跳过此组合

        c = Container()
        proto = _make_protocol("ErrorProto")

        for n in registered_names:
            c.register(proto, lambda: object(), name=n)

        with pytest.raises(DependencyNotRegisteredError) as exc_info:
            await c.resolve(proto, name=missing_name)

        err = exc_info.value
        msg = str(err)
        # 验证错误消息包含类型名和请求的名称
        assert proto.__name__ in msg
        assert missing_name in msg
        # 验证异常对象的 registered_names 属性包含所有已注册名称
        assert err.registered_names is not None
        assert set(err.registered_names) == set(registered_names)


# ── Property 7: FastAPI 集成层正确传递名称 ──


class TestProperty7FastAPIIntegration:
    """Feature: container-named-dependency, Property 7: FastAPI 集成层正确传递名称。

    验证 get_dependency(type, name=name) 返回的异步函数调用后等价于
    直接调用 resolve(type, name=name) 的结果；inject(type, name=name)
    等价于 container.get_dependency(type, name=name)。
    """

    @settings(max_examples=100)
    @given(name=st.one_of(st.none(), st.text(min_size=1, max_size=20)))
    async def test_get_dependency_passes_name(self, name: str | None):
        """get_dependency 返回的函数解析结果与直接 resolve 一致。"""
        c = Container()
        proto = _make_protocol()
        provider, sentinel = _make_provider()

        c.register(proto, provider, name=name)

        dep_fn = c.get_dependency(proto, name=name)
        result = await dep_fn()

        assert result is sentinel

    @settings(max_examples=100)
    @given(name=st.one_of(st.none(), st.text(min_size=1, max_size=20)))
    async def test_inject_passes_name(self, name: str | None):
        """inject 返回的函数解析结果与直接 resolve 一致。"""
        proto = _make_protocol()
        provider, sentinel = _make_provider()

        # 使用全局 container 实例
        original_state = container.capture_state()
        try:
            container.register(proto, provider, name=name)
            dep_fn = inject(proto, name=name)
            result = await dep_fn()
            assert result is sentinel
        finally:
            container.restore_state(original_state)


# ── 单元测试：边界条件和集成验证 ──


class TestBoundaryConditions:
    """边界条件和集成验证单元测试。

    覆盖循环依赖检测、异步 Provider、空字符串名称、
    异步资源生命周期管理等边界情况。
    """

    async def test_circular_dependency_detected_for_named(self):
        """循环依赖检测对命名依赖生效。"""
        c = Container()
        proto_a = _make_protocol("A")
        proto_b = _make_protocol("B")

        async def provider_a() -> object:
            return await c.resolve(proto_b, name="b")

        async def provider_b() -> object:
            return await c.resolve(proto_a, name="a")

        c.register(proto_a, provider_a, name="a")
        c.register(proto_b, provider_b, name="b")

        with pytest.raises(CircularDependencyError) as exc_info:
            await c.resolve(proto_a, name="a")

        # 验证链路中包含命名依赖的格式
        msg = str(exc_info.value)
        assert "(" in msg  # TypeName(name) 格式

    async def test_async_provider_with_named_dependency(self):
        """异步 Provider 与命名依赖配合正常工作。"""
        c = Container()
        proto = _make_protocol()
        sentinel = object()

        async def async_provider() -> object:
            return sentinel

        c.register(proto, async_provider, name="async_dep")
        result = await c.resolve(proto, name="async_dep")

        assert result is sentinel

    async def test_empty_string_name_is_valid(self):
        """空字符串名称作为合法名称处理。"""
        c = Container()
        proto = _make_protocol()
        provider_empty, sentinel_empty = _make_provider()
        provider_default, sentinel_default = _make_provider()

        c.register(proto, provider_empty, name="")
        c.register(proto, provider_default)

        result_empty = await c.resolve(proto, name="")
        result_default = await c.resolve(proto)

        assert result_empty is sentinel_empty
        assert result_default is sentinel_default
        assert result_empty is not result_default

    async def test_async_resource_lifecycle_unaffected(self):
        """异步资源生命周期管理不受命名依赖影响。"""
        c = Container()
        proto = _make_protocol()
        provider, sentinel = _make_provider()
        c.register(proto, provider, name="named")

        init_called = False
        cleanup_called = False

        async def init() -> None:
            nonlocal init_called
            init_called = True

        async def cleanup() -> None:
            nonlocal cleanup_called
            cleanup_called = True

        c.register_async_resource("test_resource", init, cleanup)

        await c.start()
        assert init_called

        # 命名依赖解析在 start 后正常工作
        result = await c.resolve(proto, name="named")
        assert result is sentinel

        await c.stop()
        assert cleanup_called


# ── 多实例集合解析与注入 ──


class TestMultipleDependencyInjection:
    """验证同一 Port 的全部实现可以按注册顺序集合注入。"""

    async def test_resolve_all_returns_unnamed_and_named_registrations_in_order(self):
        """集合解析包含默认实例和所有命名实例，并保持注册顺序。"""
        c = Container()
        proto = _make_protocol()
        first = object()
        second = object()
        third = object()

        c.register(proto, lambda: first, name="first")
        c.register(proto, lambda: second)
        c.register(proto, lambda: third, name="third")

        assert await c.resolve_all(proto) == [first, second, third]

    async def test_resolve_all_returns_empty_list_when_type_is_not_registered(self):
        """未注册任何实现时，集合解析返回空列表。"""
        c = Container()
        proto = _make_protocol()

        assert await c.resolve_all(proto) == []

    async def test_resolve_all_preserves_each_registration_scope(self):
        """集合解析复用单实例，并在每次解析时重建瞬时实例。"""
        c = Container()
        proto = _make_protocol()
        c.register(proto, object, Scope.SINGLETON, name="singleton")
        c.register(proto, object, Scope.TRANSIENT, name="transient")

        first = await c.resolve_all(proto)
        second = await c.resolve_all(proto)

        assert first[0] is second[0]
        assert first[1] is not second[1]

    async def test_overridden_registration_appears_only_once(self):
        """同名覆盖不会在集合结果中产生重复项。"""
        c = Container()
        proto = _make_protocol()
        old = object()
        new = object()
        c.register(proto, lambda: old, name="same")
        c.register(proto, lambda: new, name="same")

        assert await c.resolve_all(proto) == [new]

    async def test_get_all_dependency_resolves_all_instances(self):
        """容器级 FastAPI dependency 返回同类型的全部实例。"""
        c = Container()
        proto = _make_protocol()
        first = object()
        second = object()
        c.register(proto, lambda: first, name="first")
        c.register(proto, lambda: second, name="second")

        dependency = c.get_all_dependency(proto)

        assert await dependency() == [first, second]

    async def test_inject_all_uses_global_container(self):
        """inject_all 快捷方式通过全局容器执行集合解析。"""
        proto = _make_protocol()
        first = object()
        second = object()
        original_state = container.capture_state()
        try:
            container.register(proto, lambda: first, name="first")
            container.register(proto, lambda: second, name="second")

            dependency = inject_all(proto)

            assert await dependency() == [first, second]
        finally:
            container.restore_state(original_state)
