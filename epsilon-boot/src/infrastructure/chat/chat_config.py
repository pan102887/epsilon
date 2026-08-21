"""聊天服务配置模块。

基于 pydantic-settings，从 config.properties 和环境变量加载以 ``CHAT_`` 为前缀的配置项。
包含聊天服务运行所需的配置参数，如 Agent Loop 迭代轮次、上下文压缩窗口、
function calling 开关等。

注：``system_prompt`` 字段已迁移至 ``prompts/chat-default/v<N>.md`` 资产文件，
通过 ``PROMPT_CHAT_DEFAULT_VERSION`` 选择版本，详见
``docs/spec/prompt-version-registry/`` 与 ``docs/prompts.md``。
"""

from typing import Any

from pydantic import model_validator
from pydantic_settings import SettingsConfigDict

from common.configuration import ConfigurationError, PropertiesBaseSettings, create_config
from domain.agent.segmented_execution import SegmentExecutionPolicy

_DEFAULT_MAX_TOOL_ROUNDS = 10
"""Agent Loop 最大迭代轮次的默认字段值（未显式配置时使用）。"""

UNLIMITED_MAX_TOOL_ROUNDS_SENTINEL = 1_000_000
"""``max_tool_rounds`` 配置为 0 或负数（"不限制"语义）时归一化到的哨兵值。

领域层 :class:`~domain.agent.value_objects.AgentConfig` 要求 ``max_rounds > 0``，
且流式/事件路径依赖"预先已知的终止轮次"进行 ``max_rounds - 1`` 等边界运算，
因此"无限"不以 0 直接下传，而是在配置边界归一化为一个实际不可达的大数哨兵。
真正的失控兜底由 token 预算（``CHAT_SEGMENT_MAX_TOTAL_TOKENS`` 等）与工具超时承担。
"""

WORKSPACE_PATH_GUIDANCE: str = (
    "\n\nUse workspace-relative POSIX paths for all file operations. Separate path "
    "components with /."
)
"""Workspace 路径规范说明常量。

供 :mod:`infrastructure.prompt.workspace_guidance` 通过 re-export 使用，
由 ``ChatServiceAdapter`` 在构造期幂等追加到从 PromptRegistry 加载的
``LoadedPrompt.content`` 末尾。本常量保留在此模块仅为兼容 re-export 链路；
不再由本模块的 model_validator 进行追加。
"""


class ChatConfig(PropertiesBaseSettings):
    """聊天服务配置，对应环境变量前缀 ``CHAT_``。

    Attributes:
        max_messages: 滑动窗口压缩策略中非 system 消息的最大保留数量，
            对应 ``CHAT_MAX_MESSAGES``，默认 ``50``。
            该值保留为 SlidingWindowCompactionAdapter 降级策略配置。
        compaction_trigger_tokens: LLM 摘要压缩触发阈值，对应
            ``CHAT_COMPACTION_TRIGGER_TOKENS``，默认 ``8000``。
        compaction_keep_recent_messages: 摘要压缩后保留的最近非 system 消息数，
            对应 ``CHAT_COMPACTION_KEEP_RECENT_MESSAGES``，默认 ``20``。
        compaction_encoding: token 估算使用的 tiktoken encoding 名称，对应
            ``CHAT_COMPACTION_ENCODING``，默认 ``cl100k_base``。
        max_tool_rounds: Agent Loop 最大迭代轮次，对应 ``CHAT_MAX_TOOL_ROUNDS``，
            默认 ``10``。当配置值 ≤ 0 时表示"不限制轮次"，归一化为
            ``UNLIMITED_MAX_TOOL_ROUNDS_SENTINEL``（实际不可达的大数），
            失控兜底由 token 预算与工具超时承担。
        tool_calling_enabled: 是否启用 function calling 功能，
            对应 ``CHAT_TOOL_CALLING_ENABLED``，默认 ``True``。
            设为 False 时 ChatServiceAdapter 不向 LLM 传递 tools 参数，退化为普通对话模式。
        segment_*: 长任务分段续跑策略配置；默认关闭自动续跑，token/duration 为 0 时表示无限制。
    """

    model_config = SettingsConfigDict(env_prefix="CHAT_")

    max_messages: int = 50
    max_tool_rounds: int = _DEFAULT_MAX_TOOL_ROUNDS
    tool_calling_enabled: bool = True
    compaction_trigger_tokens: int = 8000
    compaction_keep_recent_messages: int = 20
    compaction_encoding: str = "cl100k_base"
    segment_auto_continue_enabled: bool = False
    segment_max_continuations: int = 3
    segment_max_total_tokens: int = 0
    segment_max_duration_seconds: float = 0.0
    segment_max_consecutive_paused: int = 2
    segment_max_no_progress_segments: int = 2
    segment_max_repeated_tool_calls: int = 2

    @model_validator(mode="before")
    @classmethod
    def _normalize_max_tool_rounds(cls, values: dict[str, Any]) -> dict[str, Any]:
        """当 ``max_tool_rounds`` 配置为 0 或负数时，归一化为"不限制"哨兵值。

        领域层 ``AgentConfig`` 要求 ``max_rounds > 0``，故"不限制"语义在配置边界
        映射为 ``UNLIMITED_MAX_TOOL_ROUNDS_SENTINEL``（实际不可达的大数），
        既不破坏领域不变量，也不改动流式路径的 ``max_rounds - 1`` 边界运算。
        """
        raw = values.get("max_tool_rounds")
        if raw is not None:
            try:
                if int(raw) <= 0:
                    values["max_tool_rounds"] = UNLIMITED_MAX_TOOL_ROUNDS_SENTINEL
            except (TypeError, ValueError):
                pass
        return values

    @model_validator(mode="after")
    def _validate_chat_config(self) -> "ChatConfig":
        """校验摘要压缩与分段续跑配置，非法时拒绝启动。"""
        if self.compaction_trigger_tokens <= 0:
            raise ConfigurationError("CHAT_COMPACTION_TRIGGER_TOKENS 必须为正整数")
        if self.compaction_keep_recent_messages <= 0:
            raise ConfigurationError("CHAT_COMPACTION_KEEP_RECENT_MESSAGES 必须为正整数")
        if self.segment_max_continuations < 0:
            raise ConfigurationError("CHAT_SEGMENT_MAX_CONTINUATIONS 必须大于等于 0")
        if self.segment_max_total_tokens < 0:
            raise ConfigurationError("CHAT_SEGMENT_MAX_TOTAL_TOKENS 必须大于等于 0")
        if self.segment_max_duration_seconds < 0:
            raise ConfigurationError("CHAT_SEGMENT_MAX_DURATION_SECONDS 必须大于等于 0")
        if self.segment_max_consecutive_paused <= 0:
            raise ConfigurationError("CHAT_SEGMENT_MAX_CONSECUTIVE_PAUSED 必须为正整数")
        if self.segment_max_no_progress_segments <= 0:
            raise ConfigurationError("CHAT_SEGMENT_MAX_NO_PROGRESS_SEGMENTS 必须为正整数")
        if self.segment_max_repeated_tool_calls <= 0:
            raise ConfigurationError("CHAT_SEGMENT_MAX_REPEATED_TOOL_CALLS 必须为正整数")
        return self

    def to_segment_policy(self) -> SegmentExecutionPolicy:
        """将外部 Chat 配置转换为领域层分段执行策略。"""
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


chat_config = create_config(ChatConfig)
"""全局聊天配置实例，通过工厂函数创建。"""
