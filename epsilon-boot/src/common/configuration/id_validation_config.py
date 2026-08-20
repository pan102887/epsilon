"""ID 校验相关运行期配置模块。

承载历史会话恢复策略等 ID 校验链路的可调开关。所有配置项遵循
``config.properties`` 的 ``UPPER_SNAKE_CASE`` 命名约定，前缀
``ID_VALIDATION_``。

落点理由：本模块由 ``domain/chat/context.py`` 在 ``BaseMessage.from_dict``
反序列化历史会话快照时读取，``docs/steering/ddd-architecture.md`` 明确禁止
``domain/`` 直接 import ``infrastructure/`` 与 ``pydantic_settings``；将
settings 类放在 ``common/configuration/`` 下，由 ``common/`` 内部封装
``PropertiesBaseSettings`` 依赖，既复用既有 settings 加载链路（env >
config.properties > .env > 默认值），又不破坏 ``domain/`` 的依赖方向。
"""

from pydantic_settings import SettingsConfigDict

from common.configuration import PropertiesBaseSettings, create_config


class IdValidationConfig(PropertiesBaseSettings):
    """ID 校验相关运行期配置，对应环境变量前缀 ``ID_VALIDATION_``。

    Attributes:
        history_restore_strategy: 历史会话恢复时遇到 ``tool_call.id`` 缺失/
            空时的兼容策略。``"filter"`` 过滤违约项并通过 WARN 日志暴露
            脏数据；``"raise"`` 抛 ``InvalidToolCallIdError``，由
            application 层降级（仅在脏数据预期为 0 时启用）。
            对应 ``ID_VALIDATION_HISTORY_RESTORE_STRATEGY``，默认
            ``"filter"``。配置取值非法时的兜底逻辑由
            ``domain/chat/context._load_history_restore_strategy()``
            承担（仅允许 ``"filter"`` / ``"raise"``，否则回退
            ``"filter"``）。
    """

    model_config = SettingsConfigDict(env_prefix="ID_VALIDATION_")

    history_restore_strategy: str = "filter"


id_validation_config = create_config(IdValidationConfig)
"""全局 ID 校验配置实例，通过工厂函数创建（支持热更新）。

由 ``ConfigProxy`` 包装，与 ``chat_config`` / ``hitl_config`` 等
单例一致：属性访问触发文件 mtime 探测，按需懒加载最新配置值。
"""
