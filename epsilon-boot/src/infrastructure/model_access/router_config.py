"""模型路由配置模块。

管理路由器级别的全局配置，包括默认提供商选择和路由策略。
与具体提供商的配置解耦，仅关注路由决策所需的参数。

模块级实例 ``router_config`` 通过 ``create_config`` 工厂函数创建，
启用热更新后会自动感知配置文件变更并重新加载。
"""

from typing import ClassVar

from pydantic_settings import SettingsConfigDict

from common.configuration import PropertiesBaseSettings, create_config


class RouterConfig(PropertiesBaseSettings):
    """模型路由配置，对应环境变量前缀 ``MODEL_ROUTER_``。

    通过 ``hot_reload = True`` 启用配置热更新，配置文件变更后自动重新加载。

    Attributes:
        default_provider: 默认模型提供商名称（openai, zhipu, claude 等），
            当请求未显式指定提供商且无法通过模型名称推断时使用。
        routing_strategy: 路由策略，决定如何根据请求参数选择提供商。
            - ``model_prefix``：根据模型名称前缀自动推断提供商
            - ``explicit``：仅使用显式指定的提供商
        default_model: 默认模型名称，当请求未指定模型时使用。
            留空时使用首个注册成功的提供商的首个模型。
    """

    hot_reload: ClassVar[bool] = True

    model_config = SettingsConfigDict(env_prefix="MODEL_ROUTER_")

    default_provider: str = "openai"
    routing_strategy: str = "model_prefix"
    default_model: str = ""


# 模块级单例
router_config = create_config(RouterConfig)
"""全局路由配置实例，通过工厂函数创建，支持热更新。"""
