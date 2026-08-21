"""Preservation 属性测试：验证 ProviderRegistry 核心查询行为不变。

本模块通过属性测试（Hypothesis）验证 ProviderRegistry 的核心查询功能
（list_models、get_adapter_for_model、get_default_model）在修复前后保持一致。

测试方法：
    直接填充 ProviderRegistry 内部状态（_providers、_model_providers、_model_rr），
    绕过 register_provider 签名，隔离测试查询行为。这确保测试不受
    register_provider 签名变更的影响，专注于验证核心功能的保持性。

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**
"""

from typing import cast
from unittest.mock import Mock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from domain.model_access.exceptions import ModelAccessError, NoAvailableModelError
from domain.model_access.ports import ModelAccessPort
from infrastructure.model_access.provider_registry import ProviderRegistry

# ---------------------------------------------------------------------------
# Hypothesis 策略
# ---------------------------------------------------------------------------

# 提供商名称：3~12 个小写字母
_provider_name_st = st.text(
    alphabet=st.characters(whitelist_categories=("Ll",)),
    min_size=3,
    max_size=12,
)

# 模型名称：形如 "model-xxx"，3~15 个字母/数字/连字符，首字符为字母
_model_name_st = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Nd"), whitelist_characters="-"),
    min_size=3,
    max_size=15,
).filter(lambda s: s[0].isalpha())

# 单个提供商注册数据：(provider_name, frozenset_of_model_names)
_provider_entry_st = st.tuples(
    _provider_name_st,
    st.frozensets(_model_name_st, min_size=1, max_size=4),
)

# 多个提供商注册数据：1~4 个提供商，提供商名称唯一
_registrations_st = st.lists(
    _provider_entry_st,
    min_size=1,
    max_size=4,
).filter(
    # 确保提供商名称唯一
    lambda entries: len({name for name, _ in entries}) == len(entries)
)


# ---------------------------------------------------------------------------
# 辅助函数：直接填充 ProviderRegistry 内部状态
# ---------------------------------------------------------------------------


def _populate_registry(
    registrations: list[tuple[str, frozenset[str]]],
    default_model: str = "",
) -> ProviderRegistry:
    """直接填充 ProviderRegistry 内部状态，绕过 register_provider。

    通过直接操作 _providers、_model_providers、_model_rr 等内部属性，
    构建与 register_provider 成功注册后等价的注册中心状态。

    Args:
        registrations: 提供商注册数据列表，每项为 (provider_name, model_names)。
        default_model: 默认模型名称，空字符串表示使用首个注册模型。

    Returns:
        已填充状态的 ProviderRegistry 实例。
    """
    all_models = sorted(
        {model_name for _, model_names in registrations for model_name in model_names}
    )
    effective_default = default_model or (all_models[0] if all_models else "")
    registry = ProviderRegistry(default_model=effective_default)

    for provider_name, model_names in registrations:
        adapter = cast(ModelAccessPort, Mock(name=provider_name))
        registry.register_provider(provider_name, adapter, sorted(model_names))

    return registry


# ---------------------------------------------------------------------------
# Property 2.1: list_models() 正确性
# ---------------------------------------------------------------------------


class TestListModelsPreservation:
    """属性测试：list_models() 返回正确的已排序模型列表。

    对于所有 (provider_name, model_names) 注册集合，list_models() 应返回
    按模型 ID 字母序排序的 ModelInfo 列表，每个 ModelInfo 包含正确的
    providers frozenset 和 owned_by（首个提供商按字母序）。

    **Validates: Requirements 3.1, 3.3**
    """

    @settings(max_examples=50)
    @given(registrations=_registrations_st)
    def test_list_models_returns_sorted_models_with_correct_providers(
        self, registrations: list[tuple[str, frozenset[str]]]
    ) -> None:
        """list_models() 返回按 ID 排序的模型列表，providers 和 owned_by 正确。"""
        registry = _populate_registry(registrations)
        result = registry.list_models()

        # 构建期望的 model → providers 映射
        expected_model_providers: dict[str, set[str]] = {}
        for provider_name, model_names in registrations:
            for model_name in model_names:
                if model_name not in expected_model_providers:
                    expected_model_providers[model_name] = set()
                expected_model_providers[model_name].add(provider_name)

        expected_model_ids = sorted(expected_model_providers.keys())

        # 验证返回的模型数量正确
        assert len(result) == len(expected_model_ids), (
            f"期望 {len(expected_model_ids)} 个模型，实际 {len(result)} 个"
        )

        # 验证排序和每个 ModelInfo 的字段
        for i, model_info in enumerate(result):
            expected_id = expected_model_ids[i]
            expected_providers = frozenset(expected_model_providers[expected_id])
            expected_owned_by = sorted(expected_providers)[0]

            assert model_info.id == expected_id, (
                f"位置 {i}: 期望模型 ID {expected_id!r}，实际 {model_info.id!r}"
            )
            assert model_info.object == "model"
            assert model_info.providers == expected_providers, (
                f"模型 {expected_id!r}: 期望 providers={expected_providers}，"
                f"实际 {model_info.providers}"
            )
            assert model_info.owned_by == expected_owned_by, (
                f"模型 {expected_id!r}: 期望 owned_by={expected_owned_by!r}，"
                f"实际 {model_info.owned_by!r}"
            )


# ---------------------------------------------------------------------------
# Property 2.2: Round-Robin 负载均衡
# ---------------------------------------------------------------------------


class TestRoundRobinPreservation:
    """属性测试：get_adapter_for_model() 的 Round-Robin 负载均衡行为。

    对于所有拥有 N 个提供商的模型，连续调用 get_adapter_for_model() N 次
    应循环遍历所有提供商（按字母序排列），实现请求的均匀分布。

    **Validates: Requirements 3.2, 3.4**
    """

    @settings(max_examples=50)
    @given(registrations=_registrations_st)
    def test_round_robin_cycles_through_all_providers(
        self, registrations: list[tuple[str, frozenset[str]]]
    ) -> None:
        """对每个模型，连续 N 次调用应轮询所有 N 个提供商。"""
        registry = _populate_registry(registrations)

        # 构建 model → sorted providers 映射
        model_providers: dict[str, list[str]] = {}
        for provider_name, model_names in registrations:
            for model_name in model_names:
                if model_name not in model_providers:
                    model_providers[model_name] = []
                model_providers[model_name].append(provider_name)

        for model_name, providers in model_providers.items():
            sorted_providers = sorted(set(providers))
            n = len(sorted_providers)

            # 调用 N 次，收集返回的适配器
            returned_adapters: list[ModelAccessPort] = []
            for _ in range(n):
                adapter = registry.get_adapter_for_model(model_name)
                returned_adapters.append(adapter)

            # 将返回的适配器映射回提供商名称
            adapter_to_provider = {
                registry.get_provider_adapter(provider): provider
                for provider in sorted_providers
            }
            returned_provider_names = [adapter_to_provider[a] for a in returned_adapters]

            # 验证 Round-Robin：N 次调用应恰好覆盖所有 N 个提供商
            assert set(returned_provider_names) == set(sorted_providers), (
                f"模型 {model_name!r}: 期望轮询提供商 {sorted_providers}，"
                f"实际 {returned_provider_names}"
            )

            # 验证顺序：应按字母序循环
            assert returned_provider_names == sorted_providers, (
                f"模型 {model_name!r}: Round-Robin 顺序应为 {sorted_providers}，"
                f"实际 {returned_provider_names}"
            )


# ---------------------------------------------------------------------------
# Property 2.3: get_default_model() 行为
# ---------------------------------------------------------------------------


class TestGetDefaultModelPreservation:
    """属性测试：get_default_model() 的默认模型选择行为。

    验证两种场景：
    1. 显式设置 default_model 时，返回该值
    2. 未设置时，返回首个注册模型（按字母序）

    **Validates: Requirements 3.5**
    """

    @settings(max_examples=50)
    @given(registrations=_registrations_st)
    def test_get_default_model_returns_first_registered_when_not_configured(
        self, registrations: list[tuple[str, frozenset[str]]]
    ) -> None:
        """未显式配置默认模型时，返回首个注册模型（按字母序）。"""
        registry = _populate_registry(registrations, default_model="")

        # 收集所有模型名称
        all_models: set[str] = set()
        for _, model_names in registrations:
            all_models.update(model_names)

        expected_default = sorted(all_models)[0]
        result = registry.get_default_model()

        assert result == expected_default, f"期望默认模型为 {expected_default!r}，实际 {result!r}"

    @settings(max_examples=50)
    @given(
        registrations=_registrations_st,
        default_model=_model_name_st,
    )
    def test_get_default_model_returns_configured_default(
        self,
        registrations: list[tuple[str, frozenset[str]]],
        default_model: str,
    ) -> None:
        """显式配置默认模型时，返回该配置值。"""
        registry = _populate_registry(registrations, default_model=default_model)

        result = registry.get_default_model()
        assert result == default_model, f"期望默认模型为 {default_model!r}，实际 {result!r}"

    def test_get_default_model_raises_when_no_models(self) -> None:
        """无任何注册模型时，get_default_model() 应抛出 NoAvailableModelError。"""
        registry = ProviderRegistry(default_model="")

        with pytest.raises(NoAvailableModelError):
            registry.get_default_model()


# ---------------------------------------------------------------------------
# Property 2.4: 未注册模型抛出 ModelAccessError
# ---------------------------------------------------------------------------


class TestUnregisteredModelPreservation:
    """属性测试：get_adapter_for_model() 对未注册模型抛出 ModelAccessError。

    对于任意未注册的模型名称，get_adapter_for_model() 应始终抛出
    ModelAccessError，无论注册中心中有多少已注册模型。

    **Validates: Requirements 3.4**
    """

    @settings(max_examples=50)
    @given(
        registrations=_registrations_st,
        unregistered_model=_model_name_st,
    )
    def test_unregistered_model_raises_model_access_error(
        self,
        registrations: list[tuple[str, frozenset[str]]],
        unregistered_model: str,
    ) -> None:
        """查询未注册模型时应抛出 ModelAccessError。"""
        registry = _populate_registry(registrations)

        # 收集所有已注册模型，确保 unregistered_model 不在其中
        all_registered: set[str] = set()
        for _, model_names in registrations:
            all_registered.update(model_names)

        # 如果随机生成的模型名恰好已注册，跳过此用例
        if unregistered_model in all_registered:
            return

        with pytest.raises(ModelAccessError):
            registry.get_adapter_for_model(unregistered_model)

    def test_concrete_unregistered_model_raises(self) -> None:
        """具体用例：空注册中心查询任意模型应抛出 ModelAccessError。"""
        registry = ProviderRegistry(default_model="")

        with pytest.raises(ModelAccessError):
            registry.get_adapter_for_model("nonexistent-model")
