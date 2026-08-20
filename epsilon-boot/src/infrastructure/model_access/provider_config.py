"""模型提供商配置模块。

提供 ``ProviderConfig`` 模板类和 ``create_provider_config`` 工厂函数，
支持通过不同的 ``env_prefix`` 动态创建多个独立的配置实例。

每个模型提供商（如 cliproxy、zhipu）使用独立的配置实例，
通过 ``create_config`` 工厂函数创建，启用热更新后会自动感知配置文件变更并重新加载。

典型用法::

    cliproxy_config = create_provider_config("MODEL_CLIPROXY_")
    zhipu_config    = create_provider_config("MODEL_ZHIPU_")
"""

from typing import ClassVar

from pydantic_settings import SettingsConfigDict

from common.configuration import PropertiesBaseSettings, create_config
from common.configuration.configuration_utils import _ENV_FILE


class ProviderConfig(PropertiesBaseSettings):
    """模型提供商配置模板类。

    不直接实例化，而是通过 ``create_provider_config()`` 工厂函数
    创建携带特定 ``env_prefix`` 的动态子类实例。

    Attributes:
        enabled: 是否启用该提供商，默认 True。
        provider_name: 提供商注册名称，用于路由键标识（如 "cliproxy"、"zhipu"）。
        api_base: API 端点 URL（OpenAI 兼容协议）。
        api_key: API 密钥（应通过环境变量注入，不应硬编码）。
        default_model: 当请求未指定模型时使用的默认模型名称。
        temperature: 默认温度参数（0.0-2.0），控制输出随机性。
        max_tokens: 默认最大 token 数，限制响应长度。
        timeout: 请求超时时间（秒）。
        max_retries: 最大重试次数（不含首次请求）。
        max_connections: HTTP 连接池最大连接数。
        max_keepalive_connections: HTTP 连接池最大保活连接数。
        models: 该提供商支持的模型名称列表，逗号分隔。
            例如 "glm-4-plus,glm-4-flash"。
            留空时仅注册 default_model。
        safety_identifier: OpenAI ``user`` 参数值，用于辅助平台安全审计。
            非空时传入 API 请求的 ``user`` 字段。留空不传递。
        stream_tool_call_id_strategy: 流式工具调用缺失 ``tool_call.id`` 时的
            处理策略。允许值为 ``"recover"`` 与 ``"raise"``；配置类只负责
            承载原始策略字符串，具体校验由模型适配器在使用点 fail-fast。
    """

    hot_reload: ClassVar[bool] = True

    enabled: bool = True
    provider_name: str = ""
    api_base: str = ""
    api_key: str = ""
    default_model: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096
    timeout: int = 30
    max_retries: int = 2
    max_connections: int = 100
    max_keepalive_connections: int = 20
    models: str = ""
    safety_identifier: str = ""
    stream_tool_call_id_strategy: str = "recover"

    def get_model_list(self) -> list[str]:
        """解析 models 字段，返回支持的模型名称列表。

        Returns:
            模型名称列表，不含空字符串。若 models 为空，返回仅含 default_model 的列表。
        """
        if self.models.strip():
            return [m.strip() for m in self.models.split(",") if m.strip()]
        if self.default_model:
            return [self.default_model]
        return []


def create_provider_config(env_prefix: str) -> ProviderConfig:
    """根据指定的 env_prefix 创建 ProviderConfig 实例。

    内部通过 ``type()`` 动态创建 ``ProviderConfig`` 的子类，
    注入携带特定 ``env_prefix`` 的 ``SettingsConfigDict``，
    再通过 ``create_config()`` 创建支持热更新的代理实例。

    Args:
        env_prefix: 环境变量前缀，如 ``"MODEL_ZHIPU_"``、``"MODEL_CLIPROXY_"``。

    Returns:
        带热更新能力的 ``ProviderConfig`` 代理实例（实际为 ``ConfigProxy``）。
    """
    dynamic_class = type(
        f"ProviderConfig_{env_prefix.strip('_')}",
        (ProviderConfig,),
        {
            "model_config": SettingsConfigDict(
                env_prefix=env_prefix,
                env_file=str(_ENV_FILE),
                env_file_encoding="utf-8",
                extra="ignore",
                frozen=True,
            ),
        },
    )
    return create_config(dynamic_class)
