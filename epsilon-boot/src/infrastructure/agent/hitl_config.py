"""HITL 工具审批配置模块。

从 ``config.properties`` 和环境变量加载 ``HITL_`` 前缀配置，控制
human-in-the-loop 工具审批是否开启、工具中断策略覆盖和审批状态 TTL。
"""

from typing import Any

from pydantic import model_validator
from pydantic_settings import SettingsConfigDict

from common.configuration import PropertiesBaseSettings, create_config

DEFAULT_HITL_STATE_TTL_SECONDS = 3600
"""审批状态默认 TTL，单位秒。"""


class HitlConfig(PropertiesBaseSettings):
    """HITL 工具审批配置。

    Attributes:
        enabled: 是否开启 HITL 工具审批，默认关闭。
        interrupt_on: JSON 字符串形式的工具审批策略覆盖。
        state_ttl_seconds: 审批状态 TTL，非正数回退为默认 3600。
    """

    model_config = SettingsConfigDict(env_prefix="HITL_")

    enabled: bool = False
    interrupt_on: str = ""
    state_ttl_seconds: int = DEFAULT_HITL_STATE_TTL_SECONDS

    @model_validator(mode="before")
    @classmethod
    def _clamp_state_ttl_seconds(cls, values: dict[str, Any]) -> dict[str, Any]:
        """当 state_ttl_seconds 小于等于 0 时回退为默认 TTL。"""
        raw = values.get("state_ttl_seconds")
        if raw is not None:
            try:
                if int(raw) <= 0:
                    values["state_ttl_seconds"] = DEFAULT_HITL_STATE_TTL_SECONDS
            except (TypeError, ValueError):
                pass
        return values


hitl_config = create_config(HitlConfig)
"""全局 HITL 配置实例，通过工厂函数创建。"""
