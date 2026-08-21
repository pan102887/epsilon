"""Agent 结构化追踪值对象模块。

定义 Agent 执行过程中每个步骤的结构化记录，用于本地持久化和后续查询。
与 OpenTelemetry span 互补：OTel 负责分布式链路，本模块负责领域级行为追踪。

值对象类型：
- ModelCallTrace：模型调用记录
- ToolCallTrace：工具调用记录
- ApprovalTrace：审批中断记录
- ErrorTrace：异常记录
- SessionTrace：会话级聚合容器
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# --- 截断常量（adapter 层使用） ---
ARGUMENTS_SUMMARY_MAX_LEN = 128
"""工具参数摘要最大长度。"""

RESULT_SUMMARY_MAX_LEN = 256
"""工具结果摘要最大长度。"""

ERROR_MESSAGE_MAX_LEN = 512
"""错误消息最大长度。"""

ARTIFACT_SUMMARY_MAX_LEN = 256
"""产物内容摘要最大长度。"""

ARTIFACT_LOGICAL_PATH_MAX_LEN = 512
"""产物逻辑路径最大长度。"""


def _metadata_dict() -> dict[str, Any]:
    return {}


def _trace_steps() -> list[AgentStepTrace]:
    return []


@dataclass(frozen=True)
class ModelCallTrace:
    """模型调用记录。

    在每轮 ReAct 循环中 LLM 响应返回后记录，包含 token 用量和延迟。
    """

    round_num: int
    model: str
    prompt_id: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    timestamp_epoch: float
    kind: Literal["model_call"] = field(default="model_call", init=False)


@dataclass(frozen=True)
class ToolCallTrace:
    """工具调用记录。

    在每个工具执行完成后记录，包含参数/结果摘要、耗时、成功/失败状态
    和工具类型特有的结构化元数据。
    """

    round_num: int
    tool_name: str
    tool_call_id: str
    arguments_summary: str
    result_summary: str
    success: bool
    latency_ms: float
    timestamp_epoch: float
    error_class: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=_metadata_dict)
    kind: Literal["tool_call"] = field(default="tool_call", init=False)


@dataclass(frozen=True)
class ApprovalTrace:
    """审批中断记录。

    在 HITL 审批中断产生时记录，包含待审批工具名称列表。
    """

    round_num: int
    approval_id: str
    actions_summary: list[str]
    timestamp_epoch: float
    kind: Literal["approval"] = field(default="approval", init=False)


@dataclass(frozen=True)
class ErrorTrace:
    """Agent 异常记录。

    在 Agent Loop 内部捕获非工具异常时记录。
    """

    round_num: int
    error_class: str
    error_message: str
    timestamp_epoch: float
    kind: Literal["error"] = field(default="error", init=False)


AgentStepTrace = ModelCallTrace | ToolCallTrace | ApprovalTrace | ErrorTrace
"""Agent 步骤追踪联合类型，通过 kind 字段判别具体类型。"""


@dataclass(frozen=True)
class SessionTrace:
    """会话级追踪聚合容器。

    包含一个 session 内所有步骤的记录列表和元数据。
    """

    session_id: str
    started_at_epoch: float
    steps: list[AgentStepTrace] = field(default_factory=_trace_steps)
    metadata: dict[str, Any] = field(default_factory=_metadata_dict)


@dataclass(frozen=True)
class ArtifactTrace:
    """任务产物追踪记录。

    记录任务产物、命令输出摘要或生成文件清单的元数据，由写入方在产物生成后
    追加到 Artifacts_Dir。**不记录完整敏感文件内容**：大字段（如内容摘要、逻辑
    路径）须由写入方按对应截断常量（``ARTIFACT_SUMMARY_MAX_LEN`` /
    ``ARTIFACT_LOGICAL_PATH_MAX_LEN``）截断后再传入，避免敏感明文落盘。

    本值对象独立于 ``AgentStepTrace`` 联合类型，不进入既有 trace 序列化路径。

    Attributes:
        session_id: 关联会话唯一标识符。
        logical_path: 产物逻辑路径（相对工作区），最长 ARTIFACT_LOGICAL_PATH_MAX_LEN。
        artifact_type: 产物类型（如 "file"/"command_output"/"file_list"）。
        timestamp_epoch: 记录时间（Unix epoch 秒）。
        size_bytes: 产物字节大小；无法确定时为 None。
        content_summary: 产物内容摘要，最长 ARTIFACT_SUMMARY_MAX_LEN；无摘要为 None。
        source_tool: 产生该产物的来源工具名称；无则为 None。
        kind: 判别字段，固定为 "artifact"。
    """

    session_id: str
    logical_path: str
    artifact_type: str
    timestamp_epoch: float
    size_bytes: int | None = None
    content_summary: str | None = None
    source_tool: str | None = None
    kind: Literal["artifact"] = field(default="artifact", init=False)
