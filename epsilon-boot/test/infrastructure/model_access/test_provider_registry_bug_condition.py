"""Bug Condition 探索性测试：验证 ProviderRegistry 的 HTTP 模型发现反模式。

本模块通过属性测试（Hypothesis）验证 ProviderRegistry.register_provider() 当前
依赖 HTTP `/v1/models` 端点发现模型列表的设计缺陷。

Bug Condition:
    register_provider() 当前签名要求 api_base、api_key、timeout、max_retries 参数，
    并通过 _discover_models() 发起 HTTP 请求。期望的新签名应接受 models: list[str]
    参数，直接使用配置驱动的模型列表完成注册，无需 HTTP 请求。

预期行为:
    在未修复代码上运行时，测试 MUST FAIL（TypeError: unexpected keyword argument 'models'），
    确认 bug 存在。修复后测试应 PASS，验证新签名正确工作。

**Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 2.1, 2.2, 2.3, 2.4, 2.5**
"""

from unittest.mock import Mock

from hypothesis import given, settings
from hypothesis import strategies as st

from infrastructure.model_access.provider_registry import ProviderRegistry

# ---------------------------------------------------------------------------
# Hypothesis 策略：生成随机提供商名称和模型列表
# ---------------------------------------------------------------------------

# 提供商名称：3~15 个小写字母组成的字符串
_provider_name_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("Ll",)),
    min_size=3,
    max_size=15,
)

# 模型名称：形如 "model-xxx" 的字符串，3~20 个字母/数字/连字符
_model_name_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Nd"), whitelist_characters="-"),
    min_size=3,
    max_size=20,
).filter(lambda s: s[0].isalpha())

# 非空模型列表：1~5 个唯一模型名称
_model_list_strategy = st.lists(
    _model_name_strategy,
    min_size=1,
    max_size=5,
    unique=True,
)


# ---------------------------------------------------------------------------
# Property 1: Bug Condition — 配置驱动的模型列表注册
# ---------------------------------------------------------------------------


class TestBugConditionConfigDrivenRegistration:
    """属性测试：配置驱动的模型列表注册（Bug Condition 探索）。

    Property 1: Bug Condition - HTTP 模型发现导致注册失败

    对于任意提供商名称和非空模型列表，调用
    register_provider(provider_name, adapter, models=model_list) 应返回 True，
    且所有模型都应出现在 _model_providers 映射中。

    在未修复代码上，此测试将因 TypeError（unexpected keyword argument 'models'）
    而失败，确认当前签名不支持配置驱动注册。

    **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 2.1, 2.2, 2.3, 2.4, 2.5**
    """

    @settings(max_examples=50)
    @given(
        provider_name=_provider_name_strategy,
        model_list=_model_list_strategy,
    )
    def test_register_provider_with_models_list(
        self, provider_name: str, model_list: list[str]
    ) -> None:
        """使用 models 参数注册提供商应成功，且所有模型被正确注册。

        在未修复代码上预期抛出 TypeError，因为当前 register_provider
        签名不接受 models 关键字参数。
        """
        # 构造 ProviderRegistry，不传入 http_client（配置驱动不需要）
        registry = ProviderRegistry(default_model="")

        # 创建 mock 适配器
        adapter = Mock()

        # 使用期望的新签名调用 register_provider
        # 未修复代码：TypeError: register_provider() got an unexpected keyword argument 'models'
        result = registry.register_provider(
            provider_name=provider_name,
            adapter=adapter,
            models=model_list,
        )

        # 验证注册成功
        assert result is True, (
            f"register_provider 应返回 True，实际返回 {result}，"
            f"provider_name={provider_name!r}, models={model_list!r}"
        )

        # 验证所有模型都已注册到 _model_providers 映射中
        for model_name in model_list:
            assert model_name in registry.model_providers, (
                f"模型 {model_name!r} 未出现在 _model_providers 映射中"
            )
            assert provider_name in registry.model_providers[model_name], (
                f"提供商 {provider_name!r} 未关联到模型 {model_name!r}"
            )


class TestBugConditionConcreteCase:
    """具体失败用例：验证 Bug Condition 的确定性测试。

    使用固定输入值验证 bug 存在，不依赖 Hypothesis 随机生成。
    在未修复代码上预期抛出 TypeError。

    **Validates: Requirements 1.3, 1.4, 2.3, 2.4**
    """

    def test_concrete_register_with_models_kwarg(self) -> None:
        """具体用例：register_provider(models=["model-a"]) 在未修复代码上应失败。

        构造 ProviderRegistry（不传 http_client），使用 models= 关键字参数
        调用 register_provider。未修复代码将抛出 TypeError。
        """
        registry = ProviderRegistry(default_model="")
        adapter = Mock()

        # 期望的新签名调用
        result = registry.register_provider(
            provider_name="claude",
            adapter=adapter,
            models=["claude-3-5-sonnet-20241022"],
        )

        assert result is True
        assert "claude-3-5-sonnet-20241022" in registry.model_providers
        assert "claude" in registry.model_providers["claude-3-5-sonnet-20241022"]

    def test_no_http_client_required(self) -> None:
        """验证 ProviderRegistry 初始化不再需要 http_client 参数。

        修复后 ProviderRegistry 不应依赖 http_client，因为模型列表
        来自配置而非 HTTP 发现。

        **Validates: Requirements 1.4, 2.4**
        """
        # 不传 http_client 构造 ProviderRegistry
        registry = ProviderRegistry(default_model="")
        adapter = Mock()

        # 使用新签名注册，不应因缺少 http_client 而失败
        result = registry.register_provider(
            provider_name="test-provider",
            adapter=adapter,
            models=["model-a", "model-b"],
        )

        assert result is True
