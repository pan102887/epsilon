"""统一供应商注册中心模块。

实现 ``ModelRegistryPort`` 协议，负责：
- 管理模型提供商的注册与生命周期
- 接受由配置文件驱动的模型列表，直接完成提供商注册（无需 HTTP 发现）
- 维护 ``model_name → Set[provider_name]`` 的映射关系
- 提供基于 Round-Robin 的负载均衡路由
- 提供可用模型列表查询能力

注册流程：
    调用方从配置文件（如 ``config.properties``）中读取各提供商的模型列表，
    通过 ``register_provider(provider_name, adapter, models)`` 传入，
    注册中心直接使用该列表完成注册，无需发起任何网络请求。
"""

import itertools
import logging
from dataclasses import dataclass, field

from domain.model_access.exceptions import ModelAccessError, NoAvailableModelError
from domain.model_access.ports import ModelAccessPort, ModelRegistryPort
from domain.model_access.value_objects import ModelInfo
from infrastructure.model_access.provider_health_policy import ProviderHealthPolicy

logger = logging.getLogger(__name__)


@dataclass
class _ProviderRecord:
    """已注册提供商的内部记录。

    Attributes:
        name: 提供商名称。
        adapter: 提供商适配器实例。
        models: 该提供商支持的模型名称集合。
    """

    name: str
    adapter: ModelAccessPort
    models: set[str] = field(default_factory=lambda: set[str]())


class ProviderRegistry(ModelRegistryPort):
    """统一供应商注册中心。

    实现 ``ModelRegistryPort`` 协议，核心数据结构：
    - ``_providers``: ``provider_name → _ProviderRecord``，存储提供商实例和其支持的模型
    - ``_model_providers``: ``model_name → set[provider_name]``，模型到提供商的反向索引
    - ``_model_rr``: ``model_name → itertools.cycle``，每个模型的 Round-Robin 迭代器

    负载均衡策略：
        使用 Round-Robin 算法，为每个模型维护一个独立的循环迭代器。
        当某个模型有多个提供商时，依次轮询选择提供商，实现请求的均匀分布。
    """

    def __init__(
        self,
        default_model: str = "",
        health_policy: ProviderHealthPolicy | None = None,
    ) -> None:
        """初始化注册中心。

        Args:
            default_model: 默认模型名称，当请求未指定模型时使用。
                若为空，则使用首个注册成功的提供商的首个模型。
            health_policy: Provider 健康策略；未传入时使用默认阈值与冷却时间。
        """
        self._providers: dict[str, _ProviderRecord] = {}
        self._model_providers: dict[str, set[str]] = {}
        self._model_rr: dict[str, itertools.cycle[str]] = {}
        self._default_model: str = default_model
        self._health_policy = health_policy or ProviderHealthPolicy()

    @property
    def model_providers(self) -> dict[str, frozenset[str]]:
        """返回模型到提供商的只读快照。"""
        return {
            model_name: frozenset(provider_names)
            for model_name, provider_names in self._model_providers.items()
        }

    def register_provider(
        self,
        provider_name: str,
        adapter: ModelAccessPort,
        models: list[str],
    ) -> bool:
        """注册提供商，使用配置驱动的模型列表完成注册。

        注册流程：
        1. 接收由调用方从配置文件中读取的模型列表
        2. 将提供商及其模型列表注册到内部数据结构
        3. 模型列表为空时记录警告并返回 False

        Args:
            provider_name: 提供商唯一标识名称。
            adapter: 实现了 ``ModelAccessPort`` 的提供商适配器实例。
            models: 该提供商支持的模型名称列表，由配置文件提供。

        Returns:
            注册成功返回 ``True``，模型列表为空时返回 ``False``。
        """
        if not models:
            logger.warning(
                "提供商 %s 的模型列表为空，跳过注册",
                provider_name,
            )
            return False

        # 注册提供商记录
        record = _ProviderRecord(name=provider_name, adapter=adapter, models=set(models))
        self._providers[provider_name] = record

        # 更新模型 → 提供商反向索引
        for model_name in models:
            if model_name not in self._model_providers:
                self._model_providers[model_name] = set()
            self._model_providers[model_name].add(provider_name)

        # 重建受影响模型的 Round-Robin 迭代器
        for model_name in models:
            providers_for_model = sorted(self._model_providers[model_name])
            self._model_rr[model_name] = itertools.cycle(providers_for_model)

        # 如果尚未设置默认模型，使用首个注册的模型
        if not self._default_model and models:
            self._default_model = models[0]
            logger.info("默认模型设置为: %s", self._default_model)

        logger.info(
            "提供商 %s 注册成功，共 %d 个模型: %s",
            provider_name,
            len(models),
            models,
        )
        return True

    def list_models(self) -> list[ModelInfo]:
        """查询所有可用模型列表。

        遍历 ``_model_providers`` 映射，为每个模型构建 ``ModelInfo`` 值对象。
        同一模型由多个提供商提供时，``owned_by`` 取首个提供商名称（按字母序）。

        Returns:
            可用模型信息列表，按模型 ID 字母序排序。
        """
        result: list[ModelInfo] = []
        for model_name in sorted(self._model_providers.keys()):
            providers = self._model_providers[model_name]
            owned_by = sorted(providers)[0] if providers else ""
            result.append(
                ModelInfo(
                    id=model_name,
                    object="model",
                    owned_by=owned_by,
                    providers=frozenset(providers),
                )
            )
        return result

    def get_adapter_for_model(self, model: str) -> ModelAccessPort:
        """根据模型名称通过 Round-Robin 负载均衡选择提供商适配器。

        当多个提供商支持同一模型时，使用 Round-Robin 算法轮询选择，
        实现请求在提供商之间的均匀分布。

        Args:
            model: 模型名称。

        Returns:
            选中的提供商适配器实例。

        Raises:
            ModelAccessError: 模型未注册或对应提供商不可用。
        """
        if model not in self._model_providers or not self._model_providers[model]:
            raise ModelAccessError(
                message=f"模型 {model} 未在任何提供商中注册",
                details={
                    "requested_model": model,
                    "available_models": list(self._model_providers.keys()),
                },
            )

        # Round-Robin 选择提供商。最多尝试当前模型的 provider 数量，避免所有
        # provider 均处于冷却状态时无限跳过。
        rr = self._model_rr.get(model)
        if rr is None:
            # 兜底：重建迭代器
            providers_for_model = sorted(self._model_providers[model])
            rr = itertools.cycle(providers_for_model)
            self._model_rr[model] = rr

        unavailable_providers: list[str] = []
        stale_providers: list[str] = []
        provider_count = len(self._model_providers[model])

        for _ in range(provider_count):
            provider_name = next(rr)
            record = self._providers.get(provider_name)

            if record is None:
                stale_providers.append(provider_name)
                continue

            if not self._health_policy.is_available(provider_name):
                unavailable_providers.append(provider_name)
                continue

            logger.debug(
                "模型 %s 路由到提供商 %s（Round-Robin）",
                model,
                provider_name,
            )
            return record.adapter

        for provider_name in stale_providers:
            self._model_providers[model].discard(provider_name)

        active_providers = sorted(
            provider_name
            for provider_name in self._model_providers[model]
            if provider_name in self._providers
        )
        self._model_providers[model] = set(active_providers)
        self._model_rr[model] = itertools.cycle(active_providers)

        if not active_providers:
            raise ModelAccessError(
                message=f"模型 {model} 无可用提供商",
                details={"model": model, "reason": "no_registered_provider"},
            )

        raise ModelAccessError(
            message=f"模型 {model} 的所有提供商暂时不可用",
            details={
                "model": model,
                "provider_count": len(active_providers),
                "unavailable_providers": sorted(set(unavailable_providers)),
                "reason": "all_providers_in_cooldown",
            },
        )

    def get_default_model(self) -> str:
        """获取默认模型名称。

        Returns:
            默认模型名称。

        Raises:
            NoAvailableModelError: 注册中心中没有任何可用模型。
        """
        if not self._default_model:
            raise NoAvailableModelError()
        return self._default_model

    @property
    def registered_providers(self) -> list[str]:
        """返回已注册的提供商名称列表。

        Returns:
            已注册的提供商名称列表。
        """
        return list(self._providers.keys())

    def get_provider_adapter(self, provider_name: str) -> ModelAccessPort:
        """Return the adapter registered under a provider name.

        Raises:
            KeyError: If the provider is not registered.
        """
        return self._providers[provider_name].adapter
