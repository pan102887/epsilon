"""轻量级依赖注入容器。

本模块提供一个支持异步资源生命周期管理的依赖注入容器，专为与 FastAPI 集成而设计。

FastAPI 生命周期中的资源管理原理
================================

FastAPI 通过 ``lifespan`` 参数接受一个异步上下文管理器，在应用启动时执行
``yield`` 之前的代码，在应用关闭时执行 ``yield`` 之后的代码。本容器的
``Container.lifespan`` 方法正是利用这一机制，将异步资源的初始化和清理嵌入
FastAPI 的生命周期。

整体流程分为三个阶段：

1. **注册阶段**（应用创建前，同步执行）：
   - 调用 ``configure_container()``，通过 ``register_async_resource()`` 注册
     需要异步初始化/清理的外部资源（如 Redis 连接池），通过 ``register()``
     注册 Port → Adapter 的类型映射。
   - 此阶段仅记录元数据，不执行任何 I/O 操作。

2. **启动阶段**（FastAPI lifespan 进入时，``container.start()``）：
   - 按注册顺序依次 ``await`` 每个异步资源的 ``initializer`` 回调。
   - 采用 fail-fast 语义：任一资源初始化失败，立即逆序回滚已成功初始化的资源，
     然后抛出异常阻止应用启动。
   - 启动完成后，后续的 ``resolve()`` 调用可以安全地使用这些已就绪的资源
     （例如 Redis 客户端已连接并通过 ping 验证）。

3. **关闭阶段**（FastAPI lifespan 退出时，``container.stop()``）：
   - 按注册逆序依次 ``await`` 每个异步资源的 ``cleanup`` 回调。
   - 采用 best-effort 语义：即使某个资源清理失败，也会继续清理剩余资源，
     确保不会因单个失败而泄漏其他资源。

典型使用示例::

    # 1. 注册阶段（container_config.py）
    container.register_async_resource("redis", _init_redis, _cleanup_redis)
    container.register(SessionContextStorePort, _create_session_store)

    # 2. 将容器 lifespan 绑定到 FastAPI（server_app.py）
    app = FastAPI(lifespan=container.lifespan)

    # 3. 在路由中通过 Depends 注入依赖
    @router.get("/")
    async def index(
        store: SessionContextStorePort = Depends(inject(SessionContextStorePort)),
    ):
        ...

这种设计将资源生命周期管理与依赖解析解耦：``register_async_resource`` 负责
"何时初始化、何时清理"，``register`` 负责"要什么类型、给什么实例"，两者通过
注册顺序的约定协作——异步资源先于依赖解析就绪，确保 provider 在被调用时
所依赖的外部连接已经可用。
"""

import inspect
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, TypeVar

from fastapi import FastAPI

from common.container_errors import (
    CircularDependencyError,
    DependencyNotRegisteredError,
    ProviderError,
)
from common.container_models import (
    AsyncResourceEntry,
    Registration,
    RegistryKey,
    Scope,
    make_registry_key,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


class Container:
    """轻量级依赖注入容器。

    支持 Singleton / Transient 两种 Scope，同步/异步 Provider，
    循环依赖检测，以及异步资源生命周期管理。

    容器内部维护两套独立的注册表：

    - ``_registry``：类型 → Provider 的映射，由 ``register()`` 写入，
      ``resolve()`` 读取。这是依赖注入的核心数据结构。
    - ``_async_resources``：异步资源生命周期回调列表，由
      ``register_async_resource()`` 写入，``start()`` / ``stop()`` 消费。
      这是资源生命周期管理的核心数据结构。

    两套注册表通过隐式的时序约定协作：在 FastAPI lifespan 中，
    ``start()`` 先于任何请求处理执行，确保异步资源（如 Redis 连接池）
    在第一次 ``resolve()`` 被调用前已经就绪。``stop()`` 在所有请求处理
    结束后执行，确保资源在不再被使用后才被清理。
    """

    def __init__(self) -> None:
        self._registry: dict[RegistryKey, Registration] = {}
        self._singletons: dict[RegistryKey, Any] = {}
        self._async_resources: list[AsyncResourceEntry] = []
        self._initialized_resources: list[AsyncResourceEntry] = []
        self._started: bool = False
        self._resolving: set[RegistryKey] = set()

    # ── 注册 API ──

    def register(
        self,
        abstract_type: type,
        provider: Callable[..., Any],
        scope: Scope = Scope.SINGLETON,
        *,
        name: str | None = None,
    ) -> None:
        """注册依赖。同一类型（和名称）重复注册时覆盖。

        Args:
            abstract_type: 抽象类型（Port 接口）。
            provider: 创建依赖实例的工厂回调函数。
            scope: 依赖的生命周期范围，默认为 Singleton。
            name: 可选的依赖名称。为 None 时以类型名称的 Spring 风格
                lowerCamel 名称作为默认实例名。
        """
        key = make_registry_key(abstract_type, name)
        is_async = inspect.iscoroutinefunction(provider)
        self._registry[key] = Registration(
            provider=provider,
            scope=scope,
            is_async=is_async,
        )

    # ── 解析 API ──

    async def resolve(self, abstract_type: type[T], *, name: str | None = None) -> T:
        """根据抽象类型和可选名称解析依赖实例。

        - Singleton: 首次解析时创建并缓存，后续返回同一实例
        - Transient: 每次解析创建新实例
        - 支持循环依赖检测

        Args:
            abstract_type: 抽象类型（Port 接口）。
            name: 可选的依赖名称。为 None 时按类型名称的 Spring 风格
                lowerCamel 默认实例名查找。

        Returns:
            解析得到的依赖实例。

        Raises:
            DependencyNotRegisteredError: 未找到匹配的注册。
            CircularDependencyError: 检测到循环依赖。
        """
        key = make_registry_key(abstract_type, name)

        if key not in self._registry:
            registered_names = (
                self._get_registered_names(abstract_type) if name is not None else None
            )
            raise DependencyNotRegisteredError(
                abstract_type,
                [k if isinstance(k, type) else k[0] for k in self._registry],
                name=name,
                registered_names=registered_names,
            )

        # 循环依赖检测
        if key in self._resolving:
            chain = [*list(self._resolving), key]
            raise CircularDependencyError(chain)

        registration = self._registry[key]

        # Singleton 缓存命中
        if registration.scope is Scope.SINGLETON and key in self._singletons:
            return self._singletons[key]  # type: ignore[return-value]

        self._resolving.add(key)
        try:
            instance = await self._invoke_provider(abstract_type, registration)
        finally:
            self._resolving.discard(key)

        # Singleton 缓存写入
        if registration.scope is Scope.SINGLETON:
            self._singletons[key] = instance

        return instance  # type: ignore[return-value]

    async def resolve_all(self, abstract_type: type[T]) -> list[T]:
        """解析指定抽象类型的全部已注册实例。

        同时包含无名称注册和命名注册，返回顺序与注册表中的注册顺序一致。
        每个注册仍通过 :meth:`resolve` 独立解析，因此保留各自的 Scope、
        Singleton 缓存、异步 Provider 和循环依赖检测语义。未找到任何注册时
        返回空列表，便于像 Spring 的集合注入一样声明可选的多实现依赖。

        Args:
            abstract_type: 要批量解析的抽象类型（Port 接口）。

        Returns:
            该类型下全部无名称和命名依赖实例；无匹配注册时返回空列表。
        """
        keys = [
            key
            for key in self._registry
            if key is abstract_type or (isinstance(key, tuple) and key[0] is abstract_type)
        ]

        instances: list[T] = []
        for key in keys:
            name = key[1] if isinstance(key, tuple) else None
            instances.append(await self.resolve(abstract_type, name=name))
        return instances

    async def _invoke_provider(self, abstract_type: type, registration: Registration) -> Any:
        """调用 Provider 并包装异常。ContainerError 子类直接透传。"""
        from common.container_errors import ContainerError

        try:
            if registration.is_async:
                return await registration.provider()
            else:
                return registration.provider()
        except ContainerError:
            raise
        except Exception as exc:
            raise ProviderError(abstract_type, registration.provider, exc) from exc

    def _get_registered_names(self, abstract_type: type) -> list[str]:
        """收集指定类型下所有已注册的命名依赖名称。

        遍历 ``_registry``，找出所有以 ``(abstract_type, name)`` 元组为键的注册，
        返回其中的 name 列表。

        Args:
            abstract_type: 要查询的抽象类型。

        Returns:
            该类型下所有已注册的名称列表。
        """
        names: list[str] = []
        for key in self._registry:
            if isinstance(key, tuple) and key[0] is abstract_type:
                names.append(key[1])
        return names

    # ── 异步资源注册 API ──

    def has_async_resource(self, name: str) -> bool:
        """判断指定名称的异步资源是否已注册。

        ``_create_readiness_aggregator`` 通过本方法按实际装配的中间件动态
        组装健康检查列表（需求 6.3.3）。仅读取 ``_async_resources`` 中的
        条目名称，不改变容器生命周期状态。

        Args:
            name: 资源名称（与 ``register_async_resource`` 传入的 ``name``
                保持一致）。

        Returns:
            当且仅当容器内存在同名已注册异步资源时返回 ``True``。
        """
        return any(entry.name == name for entry in self._async_resources)

    def register_async_resource(
        self,
        name: str,
        initializer: Callable[[], Any],
        cleanup: Callable[[], Any] | None = None,
    ) -> None:
        """注册异步资源的生命周期回调。

        异步资源是指需要在应用启动时执行异步初始化、在应用关闭时执行异步清理的
        外部依赖（如 Redis 连接池、数据库连接、消息队列客户端等）。

        与 ``register()`` 不同，此方法不参与类型解析，仅管理资源的生命周期。
        注册的回调会在 ``start()`` 和 ``stop()`` 中被调用，而 ``start()`` /
        ``stop()`` 又通过 ``lifespan()`` 嵌入 FastAPI 的启动/关闭流程。

        注册顺序决定了初始化顺序（FIFO）和清理顺序（LIFO）。如果资源 B 依赖
        资源 A，应先注册 A 再注册 B，这样启动时 A 先初始化，关闭时 B 先清理。

        Args:
            name: 资源名称，用于日志输出，便于排查启动/关闭问题。
            initializer: 异步初始化回调，在 ``start()`` 中被 ``await`` 调用。
                通常用于建立连接、验证连通性等。
            cleanup: 异步清理回调（可选），在 ``stop()`` 中被 ``await`` 调用。
                通常用于关闭连接、释放资源。为 None 时跳过清理。
        """
        self._async_resources = [entry for entry in self._async_resources if entry.name != name]
        self._async_resources.append(
            AsyncResourceEntry(name=name, initializer=initializer, cleanup=cleanup)
        )

    # ── 生命周期 API ──

    async def start(self) -> None:
        """按注册顺序初始化所有异步资源。采用 fail-fast 语义。

        此方法在 FastAPI lifespan 进入时由 ``lifespan()`` 调用，在应用开始
        接受请求之前完成所有外部资源的初始化。

        初始化流程：
        1. 按 ``register_async_resource()`` 的注册顺序，依次 await 每个资源
           的 initializer 回调。
        2. 每个成功初始化的资源被记录到 ``_initialized_resources``，用于后续
           清理或回滚。
        3. 如果任一资源初始化失败，立即调用 ``_rollback_initialized()`` 逆序
           清理已成功的资源，然后重新抛出异常，阻止 FastAPI 应用启动。

        Raises:
            Exception: 任一资源初始化失败时，回滚后重新抛出原始异常。
        """
        logger.info(
            "Container starting: initializing %d async resources", len(self._async_resources)
        )
        for entry in self._async_resources:
            try:
                logger.info("Initializing async resource: %s", entry.name)
                await entry.initializer()
                self._initialized_resources.append(entry)
            except Exception:
                logger.exception("Failed to initialize async resource: %s", entry.name)
                await self._rollback_initialized()
                raise
        self._started = True
        logger.info("Container started successfully")

    async def stop(self) -> None:
        """按注册逆序清理所有已初始化的异步资源。采用 best-effort 语义。

        此方法在 FastAPI lifespan 退出时由 ``lifespan()`` 调用，在应用停止
        接受请求之后执行资源清理。

        清理流程：
        1. 按注册逆序（LIFO）遍历 ``_initialized_resources``，依次 await
           每个资源的 cleanup 回调。逆序确保后注册的资源（可能依赖先注册的
           资源）先被清理。
        2. 如果某个资源的 cleanup 为 None，跳过该资源。
        3. 如果某个资源清理失败，记录异常日志但继续清理剩余资源，确保不会
           因单个失败而泄漏其他资源。

        与 ``start()`` 的 fail-fast 不同，``stop()`` 保证尽最大努力清理所有
        资源，因为此时应用已经在关闭，部分清理失败不应阻止其他资源的释放。
        """
        logger.info(
            "Container stopping: cleaning up %d async resources", len(self._initialized_resources)
        )
        for entry in reversed(self._initialized_resources):
            if entry.cleanup is None:
                continue
            try:
                logger.info("Cleaning up async resource: %s", entry.name)
                await entry.cleanup()
            except Exception:
                logger.exception("Failed to clean up async resource: %s", entry.name)
        self._initialized_resources.clear()
        self._started = False
        logger.info("Container stopped")

    async def _rollback_initialized(self) -> None:
        """逆序清理已成功初始化的资源（用于 start fail-fast 回滚）。"""
        for entry in reversed(self._initialized_resources):
            if entry.cleanup is None:
                continue
            try:
                await entry.cleanup()
            except Exception:
                logger.exception("Failed to clean up resource during rollback: %s", entry.name)
        self._initialized_resources.clear()

    # ── FastAPI 集成 ──

    def get_dependency(
        self, abstract_type: type[T], *, name: str | None = None
    ) -> Callable[..., Any]:
        """返回可用于 FastAPI Depends 的依赖提供函数。

        Args:
            abstract_type: 抽象类型（Port 接口）。
            name: 可选的依赖名称。为 None 时按类型名称的 Spring 风格
                lowerCamel 默认实例名解析。

        Returns:
            异步函数，调用后返回解析得到的依赖实例。
        """

        async def _dependency() -> T:
            return await self.resolve(abstract_type, name=name)

        return _dependency

    def get_all_dependency(self, abstract_type: type[T]) -> Callable[..., Any]:
        """返回可用于 FastAPI Depends 的集合依赖提供函数。

        Args:
            abstract_type: 要批量解析的抽象类型（Port 接口）。

        Returns:
            异步函数，调用后返回该类型的全部已注册实例。
        """

        async def _dependency() -> list[T]:
            return await self.resolve_all(abstract_type)

        return _dependency

    @asynccontextmanager
    async def lifespan(self, app: FastAPI) -> AsyncIterator[None]:
        """可用作 FastAPI lifespan 的异步上下文管理器。

        这是容器与 FastAPI 生命周期集成的入口。通过将此方法传递给
        ``FastAPI(lifespan=container.lifespan)``，FastAPI 会在以下时机
        自动调用容器的生命周期方法：

        - 应用启动、开始接受请求之前：执行 ``await self.start()``，
          按注册顺序初始化所有异步资源。
        - 应用关闭、停止接受请求之后：执行 ``await self.stop()``，
          按注册逆序清理所有异步资源。

        ``stop()`` 放在 ``finally`` 块中，确保即使应用运行期间发生未捕获
        异常，资源清理也一定会被执行。

        Args:
            app: FastAPI 应用实例（由 FastAPI 框架自动传入，容器内部不使用）。

        Yields:
            None: yield 期间 FastAPI 正常处理请求，所有异步资源处于就绪状态。
        """

        await self.start()
        try:
            yield
        finally:
            await self.stop()


container = Container()
"""全局容器实例，模块级单例。"""


def inject(abstract_type: type[T], *, name: str | None = None) -> Callable[..., Any]:
    """FastAPI Depends 快捷方式，支持按名称解析。

    Usage::

        @router.get("/")
        async def index(
            store: SessionContextStorePort = Depends(inject(SessionContextStorePort)),
        ):
            ...

        # 命名依赖
        @router.post("/chat")
        async def chat(
            primary: OpenAIProviderPort = Depends(inject(OpenAIProviderPort, name="primary")),
        ):
            ...

    Args:
        abstract_type: 抽象类型（Port 接口）。
        name: 可选的依赖名称。为 None 时按类型名称的 Spring 风格
            lowerCamel 默认实例名解析。

    Returns:
        可用于 FastAPI ``Depends`` 的依赖提供函数。
    """
    return container.get_dependency(abstract_type, name=name)


def inject_all(abstract_type: type[T]) -> Callable[..., Any]:
    """FastAPI 多实例集合注入快捷方式。

    Usage::

        @router.get("/")
        async def index(
            registries: list[ModelRegistryPort] = Depends(
                inject_all(ModelRegistryPort)
            ),
        ):
            ...

    Args:
        abstract_type: 要批量解析的抽象类型（Port 接口）。

    Returns:
        可用于 FastAPI ``Depends`` 的集合依赖提供函数。
    """
    return container.get_all_dependency(abstract_type)
