"""Task Agent 运行时配置模块。

基于 pydantic-settings 从 ``config.properties``、环境变量和 ``.env`` 文件
加载以 ``TASK_AGENT_`` 为前缀的配置项，供应用组合根装配任务 Agent 时使用。
"""

from typing import Any

from pydantic import model_validator
from pydantic_settings import SettingsConfigDict

from common.configuration import ConfigurationError, PropertiesBaseSettings, create_config
from domain.agent.segmented_execution import SegmentExecutionPolicy

UNLIMITED_MAX_ROUNDS_SENTINEL = 1_000_000
"""``max_rounds`` 配置为 0 或负数（"不限制"语义）时归一化到的哨兵值。

与 ``infrastructure.chat.chat_config.UNLIMITED_MAX_TOOL_ROUNDS_SENTINEL`` 语义一致：
领域层 :class:`~domain.agent.value_objects.AgentConfig` 要求 ``max_rounds > 0``，
故"无限"在配置边界归一化为实际不可达的大数，失控兜底由 token 预算与工具超时承担。
"""


class TaskAgentConfig(PropertiesBaseSettings):
    """Task Agent 配置，对应环境变量前缀 ``TASK_AGENT_``。

    Attributes:
        max_rounds: 任务 Agent Loop 最大迭代轮次，对应
            ``TASK_AGENT_MAX_ROUNDS``。配置值 ≤ 0 时表示"不限制轮次"，
            归一化为 ``UNLIMITED_MAX_ROUNDS_SENTINEL``。
        segment_*: 长任务分段续跑策略配置；默认关闭自动续跑，token/duration 为 0 时表示无限制。
    """

    model_config = SettingsConfigDict(env_prefix="TASK_AGENT_")

    max_rounds: int = 10

    @model_validator(mode="before")
    @classmethod
    def _normalize_max_rounds(cls, values: dict[str, Any]) -> dict[str, Any]:
        """当 ``max_rounds`` 配置为 0 或负数时，归一化为"不限制"哨兵值。

        领域层 ``AgentConfig`` 要求 ``max_rounds > 0``，故"不限制"语义在配置边界
        映射为 ``UNLIMITED_MAX_ROUNDS_SENTINEL``（实际不可达的大数），
        与 Chat 侧 ``max_tool_rounds`` 的归一化保持一致。
        """
        raw = values.get("max_rounds")
        if raw is not None:
            try:
                if int(raw) <= 0:
                    values["max_rounds"] = UNLIMITED_MAX_ROUNDS_SENTINEL
            except (TypeError, ValueError):
                pass
        return values
    segment_auto_continue_enabled: bool = False
    segment_max_continuations: int = 3
    segment_max_total_tokens: int = 0
    segment_max_duration_seconds: float = 0.0
    segment_max_consecutive_paused: int = 2
    segment_max_no_progress_segments: int = 2
    segment_max_repeated_tool_calls: int = 2

    @model_validator(mode="after")
    def _validate_segment_config(self) -> "TaskAgentConfig":
        """校验分段续跑配置，非法时拒绝启动。"""
        if self.segment_max_continuations < 0:
            raise ConfigurationError("TASK_AGENT_SEGMENT_MAX_CONTINUATIONS 必须大于等于 0")
        if self.segment_max_total_tokens < 0:
            raise ConfigurationError("TASK_AGENT_SEGMENT_MAX_TOTAL_TOKENS 必须大于等于 0")
        if self.segment_max_duration_seconds < 0:
            raise ConfigurationError("TASK_AGENT_SEGMENT_MAX_DURATION_SECONDS 必须大于等于 0")
        if self.segment_max_consecutive_paused <= 0:
            raise ConfigurationError("TASK_AGENT_SEGMENT_MAX_CONSECUTIVE_PAUSED 必须为正整数")
        if self.segment_max_no_progress_segments <= 0:
            raise ConfigurationError("TASK_AGENT_SEGMENT_MAX_NO_PROGRESS_SEGMENTS 必须为正整数")
        if self.segment_max_repeated_tool_calls <= 0:
            raise ConfigurationError("TASK_AGENT_SEGMENT_MAX_REPEATED_TOOL_CALLS 必须为正整数")
        return self

    def to_segment_policy(self) -> SegmentExecutionPolicy:
        """将外部 Task Agent 配置转换为领域层分段执行策略。"""
        return SegmentExecutionPolicy(
            auto_continue_enabled=self.segment_auto_continue_enabled,
            max_continuations=self.segment_max_continuations,
            max_total_tokens=(
                self.segment_max_total_tokens if self.segment_max_total_tokens > 0 else None
            ),
            max_duration_seconds=(
                self.segment_max_duration_seconds if self.segment_max_duration_seconds > 0 else None
            ),
            max_consecutive_paused=self.segment_max_consecutive_paused,
            max_no_progress_segments=self.segment_max_no_progress_segments,
            max_repeated_tool_calls=self.segment_max_repeated_tool_calls,
        )


task_agent_config = create_config(TaskAgentConfig)
"""全局 Task Agent 配置实例，通过项目配置工厂创建。"""
