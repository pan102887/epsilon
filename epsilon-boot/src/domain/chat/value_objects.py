"""聊天对话值对象定义。

定义聊天对话场景中使用的值对象（Value Object），包括聊天请求和聊天响应。
值对象为不可变对象（frozen dataclass），在构造时进行字段验证，
确保在各层之间传递的对话数据始终合法。
"""

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from domain.agent.segmented_execution import SegmentRunMetadata
from domain.agent.value_objects import (
    AgentTerminationReason,
    ApprovalDecision,
    PendingActionRequest,
)

if TYPE_CHECKING:
    from domain.chat.context import BaseMessage

_PROMPT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9\-]*@v[1-9]\d*$")
"""合法 ``prompt_id`` 正则：小写+连字符的 name + ``@`` + ``v<正整数>``。

用于 :class:`ChatResponseVO` 的 ``__post_init__`` 校验，保持与
``domain/prompt/value_objects.py`` / ``domain/agent/value_objects.py`` 中
同名常量语义一致。
"""


ChatResponseStatus = Literal["completed", "approval_required", "paused"]
"""聊天响应状态。"""


@dataclass(frozen=True, slots=True)
class SessionMetadata:
    """会话发现与恢复使用的轻量元数据。

    该值对象只保存会话列表和恢复校验所需的索引信息，不承载完整消息正文。
    `updated_at_epoch_ms` 使用保存上下文时生成的毫秒时间戳，避免恢复语义依赖
    文件系统 `mtime` 或 Redis key 的剩余 TTL。

    Attributes:
        session_id: 会话唯一标识符，不可为空。
        updated_at_epoch_ms: 最近一次成功保存上下文时的 Unix epoch 毫秒时间戳。
        message_count: 最近一次保存后的消息数量，必须为非负整数。
        preview: 用于 `/sessions` 展示的摘要或最后一条消息预览，不可为空。
        created_at_epoch_ms: 会话首次进入索引时的 Unix epoch 毫秒时间戳。
        model: 最近一次保存上下文时使用的模型名称，可为空。
    """

    session_id: str
    updated_at_epoch_ms: int
    message_count: int
    preview: str
    created_at_epoch_ms: int | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        """校验会话索引元数据字段。"""
        if not self.session_id:
            raise ValueError("session_id 不能为空")
        if type(self.updated_at_epoch_ms) is not int:
            raise ValueError("updated_at_epoch_ms 必须为 int")
        if self.updated_at_epoch_ms < 0:
            raise ValueError("updated_at_epoch_ms 必须为非负整数")
        if type(self.message_count) is not int:
            raise ValueError("message_count 必须为 int")
        if self.message_count < 0:
            raise ValueError("message_count 必须为非负整数")
        if not self.preview:
            raise ValueError("preview 不能为空")
        if self.created_at_epoch_ms is not None and type(self.created_at_epoch_ms) is not int:
            raise ValueError("created_at_epoch_ms 必须为 int 或 None")
        if self.created_at_epoch_ms is not None and self.created_at_epoch_ms < 0:
            raise ValueError("created_at_epoch_ms 必须为非负整数")


@dataclass(frozen=True)
class ChatRequestVO:
    """聊天请求值对象。

    封装用户发送的对话消息和会话标识，用于在 Router → Service 之间传递请求数据。
    构造时自动验证字段合法性，不合法时抛出 ValueError。

    Attributes:
        session_id: 会话唯一标识符，用于关联同一用户的多轮对话上下文，不可为空。
        message: 用户消息内容，不可为空且不可为纯空白字符。
        stream: 是否使用流式响应（SSE），默认为 False（同步响应）。
        model: 可选的模型名称，未指定时使用系统默认模型。

    Raises:
        ValueError: 当 session_id 为空，或 message 为空/纯空白字符时抛出。
    """

    session_id: str
    message: str
    stream: bool = False
    model: str | None = None

    def __post_init__(self) -> None:
        """验证请求字段的合法性。

        校验规则：
        - session_id 不可为空字符串
        - message 不可为空字符串，且不可仅包含空白字符（空格、制表符、换行符等）
        - model 可选，为 None 时使用系统默认模型
        """
        if not self.session_id:
            raise ValueError("session_id 不能为空")
        if not self.message or not self.message.strip():
            raise ValueError("message 不能为空或纯空白字符")


@dataclass(frozen=True, kw_only=True)
class ChatResponseVO:
    """聊天响应值对象。

    封装模型返回的回复内容和元数据，用于在 Service → Router 之间传递响应数据。

    **决策 #1**：使用 ``kw_only=True`` 让新增的无默认值必填字段
    ``prompt_id`` 与既有字段并存，所有字段仅支持关键字参数调用；
    ``prompt_id`` 未显式传入即构造失败（``TypeError``）。

    Attributes:
        session_id: 会话唯一标识符，与请求中的 session_id 一致。
        reply: 模型回复的文本内容。
        model: 实际使用的模型名称（如 "gpt-4o"、"claude-3-sonnet" 等）。
        usage:
            token 用量信息，格式为
            {"prompt_tokens": X, "completion_tokens": Y, "total_tokens": Z}。
        prompt_id: 本次对话使用的 Prompt 标识符（形如 ``chat-default@v3``）；
            来源于 ``ChatServiceAdapter._loaded_prompt.prompt_id``，
            供前端 / 调用方用于回放。
        status: 响应状态，默认 completed 以兼容既有调用方。
        approval_id: 等待审批时的审批批次 ID。
        action_requests: 等待审批时的待审批动作列表。
        terminated_reason: Agent 运行终止原因，默认 completed。
        can_continue: 是否可基于当前会话上下文继续执行。
    """

    session_id: str
    reply: str
    model: str
    usage: dict[str, int]
    prompt_id: str
    status: ChatResponseStatus = "completed"
    approval_id: str | None = None
    action_requests: tuple[PendingActionRequest, ...] = field(default_factory=tuple)
    terminated_reason: AgentTerminationReason = "completed"
    can_continue: bool = False
    segment_metadata: SegmentRunMetadata = field(default_factory=SegmentRunMetadata)

    def __post_init__(self) -> None:
        """校验 prompt_id 字段格式合法。

        Raises:
            ValueError: 当 prompt_id 不符合 ``^[a-z][a-z0-9\\-]*@v[1-9]\\d*$``
                正则时抛出。
        """
        if not _PROMPT_ID_PATTERN.match(self.prompt_id):
            raise ValueError(f"prompt_id 非法，期望形如 'name@v<N>'，当前值: {self.prompt_id!r}")


@dataclass(frozen=True)
class ChatContinueRequestVO:
    """聊天继续请求值对象。

    封装客户端基于既有会话上下文继续 Agent 执行的请求。继续请求不携带
    新用户消息，只允许选择是否使用流式响应和可选模型覆盖。

    Attributes:
        session_id: 会话唯一标识符，不可为空。
        stream: 是否使用流式响应，默认 False。
        model: 可选模型名称，未指定时使用系统默认或上下文恢复模型。

    Raises:
        ValueError: 当 session_id 为空时抛出。
    """

    session_id: str
    stream: bool = False
    model: str | None = None

    def __post_init__(self) -> None:
        """校验继续请求标识字段。"""
        if not self.session_id:
            raise ValueError("session_id 不能为空")


@dataclass(frozen=True)
class ContextCompactionResult:
    """上下文压缩结果值对象。

    表达压缩后的模型输入消息、摘要模型调用的 usage，以及本次是否生成了
    语义摘要。完整历史保存不属于该值对象职责。
    """

    messages: list["BaseMessage"]
    usage: dict[str, int] = field(default_factory=dict)
    summary_created: bool = False

    def __post_init__(self) -> None:
        """校验压缩结果结构合法。"""
        if not isinstance(self.messages, list):
            raise ValueError("messages 必须为 list")
        for key, value in self.usage.items():
            if not isinstance(value, int):
                raise ValueError(f"usage[{key!r}] 必须为 int")
            if value < 0:
                raise ValueError(f"usage[{key!r}] 必须为非负整数")


@dataclass(frozen=True)
class ContextBuilderResult:
    """上下文构建结果值对象。

    表达单次模型调用可直接使用的领域消息列表、上下文构建阶段 usage、
    摘要生成标记、环境上下文注入标记，以及轻量观测元数据。``messages``
    字段承载 ``BaseMessage`` 子类实例列表，不再承载任何具体协议字典；
    具体协议转换由 ``ModelAccessPort`` 的具体 adapter 在 SDK 调用前
    自行完成。
    """

    messages: list["BaseMessage"]
    usage: dict[str, int] = field(default_factory=dict)
    summary_created: bool = False
    environment_injected: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验上下文构建结果结构合法。

        校验规则：

        - ``messages`` 必须为 ``list``；
        - ``messages`` 不能为空；
        - 所有元素必须为 ``BaseMessage`` 子类实例（不再校验是否为含
          ``role`` / ``content`` 键的 ``dict``）；
        - ``usage`` 的 key 必须为 ``str``，value 必须为非负 ``int``；
        - ``metadata`` 必须为 ``dict``。
        """
        # import 本地化避免与 ``domain.chat.context`` 之间形成循环依赖
        # （context.py 引用本模块的部分类型，反向依赖须延迟解析）。
        from domain.chat.context import BaseMessage

        if not isinstance(self.messages, list):
            raise ValueError("messages 必须为 list")
        if not self.messages:
            raise ValueError("messages 不能为空")
        for index, message in enumerate(self.messages):
            if not isinstance(message, BaseMessage):
                raise ValueError(
                    f"messages[{index}] 必须为 BaseMessage 子类实例，"
                    f"当前类型: {type(message).__name__}"
                )
        for key, value in self.usage.items():
            if not isinstance(key, str):
                raise ValueError("usage key 必须为 str")
            if type(value) is not int:
                raise ValueError(f"usage[{key!r}] 必须为 int")
            if value < 0:
                raise ValueError(f"usage[{key!r}] 必须为非负整数")
        if not isinstance(self.metadata, dict):
            raise ValueError("metadata 必须为 dict")


@dataclass(frozen=True)
class ApprovalResumeRequestVO:
    """审批恢复请求值对象。

    封装客户端提交的审批恢复请求。session_id 与 approval_id 必须非空，
    decisions 顺序必须由后续应用/Agent 编排层按待审批动作逐项校验。
    """

    session_id: str
    approval_id: str
    decisions: tuple[ApprovalDecision, ...]
    model: str | None = None

    def __post_init__(self) -> None:
        """校验审批恢复请求标识字段。"""
        if not self.session_id:
            raise ValueError("session_id 不能为空")
        if not self.approval_id:
            raise ValueError("approval_id 不能为空")
