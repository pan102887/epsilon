"""会话上下文管理模块。

定义会话管理限界上下文中的核心值对象，包括对话消息类型层次结构和对话上下文（ConversationContext）。

消息类型层次结构：
- BaseMessage：所有消息类型的抽象基类，定义公共属性（content、metadata）和序列化接口
- SystemMessage：系统提示词消息，role 固定为 "system"
- UserMessage：用户输入消息，role 固定为 "user"
- AssistantMessage：AI 助手回复消息，role 固定为 "assistant"
- ToolMessage：工具调用结果消息，role 固定为 "tool"，额外携带 tool_name

ConversationContext 负责管理对话消息列表，作为纯粹的消息容器，不包含任何裁剪或压缩逻辑。
消息的裁剪/压缩职责由 ContextCompactionPort 的实现承担，在编排层（ChatServiceAdapter）中执行。
所有值对象均支持序列化/反序列化，以便于持久化存储和网络传输。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, cast

from domain.model_access.exceptions import InvalidToolCallIdError
from domain.model_access.value_objects import ToolCallRequest

_VALID_HISTORY_RESTORE_STRATEGIES = {"filter", "raise"}
history_restore_strategy = "filter"
"""历史会话恢复策略。

领域层保持纯内存默认值，不直接读取运行时配置文件或 pydantic settings。
应用组合根可在启动期通过 ``configure_history_restore_strategy`` 注入策略。
"""


def normalize_history_restore_strategy(raw: str | None) -> str:
    """规范化历史会话恢复策略。

    Args:
        raw: 配置来源给出的策略值。

    Returns:
        ``"filter"`` / ``"raise"``；非法或缺失时回退 ``"filter"``。
    """
    return raw if raw in ("filter", "raise") else "filter"


def configure_history_restore_strategy(raw: str | None) -> None:
    """由应用组合根配置历史会话恢复策略。

    本函数是领域层对外暴露的纯 Python 配置入口，用于避免领域层直接依赖
    ``common.configuration`` 或任何 settings 框架。
    """

    global history_restore_strategy
    history_restore_strategy = normalize_history_restore_strategy(raw)


@dataclass(kw_only=True)
class BaseMessage(ABC):
    """所有消息类型的抽象基类。

    定义消息的公共属性和序列化/反序列化接口。通过 ABC 机制禁止直接实例化，
    所有消息必须通过具体子类（SystemMessage、UserMessage、AssistantMessage、ToolMessage）创建。

    子类通过实现 role 抽象属性来固定消息角色，消除运行时字符串比较，
    支持通过 isinstance 进行类型安全的消息判断。

    使用 kw_only=True 确保所有字段为关键字参数，避免子类（如 ToolMessage）
    新增必填字段时因 dataclass 字段排序规则导致的 TypeError。

    Attributes:
        content: 消息文本内容
        metadata: 扩展元数据字典，默认为空字典
    """

    content: str
    metadata: dict[str, Any] = field(default_factory=dict[str, Any])

    @property
    @abstractmethod
    def role(self) -> str:
        """返回消息角色标识。

        由子类固定实现，返回对应的角色字符串。
        SystemMessage 返回 "system"，UserMessage 返回 "user"，
        AssistantMessage 返回 "assistant"，ToolMessage 返回 "tool"。

        Returns:
            消息角色标识字符串
        """
        ...

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。

        输出格式与旧 Message.to_dict() 完全一致，确保序列化数据的向后兼容。
        metadata 仅在非空时包含在输出字典中。

        Returns:
            包含 role、content 字段的字典，metadata 仅在非空时包含
        """
        data: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.metadata:
            data["metadata"] = self.metadata
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BaseMessage":
        """工厂方法：根据字典中的 role 字段分派到对应子类进行反序列化。

        支持解析旧格式数据（由旧 Message.to_dict() 生成），确保向后兼容。
        根据 role 值分派到 SystemMessage、UserMessage、AssistantMessage 或 ToolMessage。

        Args:
            data: 包含消息字段的字典，必须包含 role 和 content 键

        Returns:
            反序列化后的具体消息子类实例

        Raises:
            ValueError: 当 role 值不属于已知角色集合时
            KeyError: 当字典缺少 role 或 content 键时
        """
        role = data["role"]
        content = data["content"]
        metadata = data.get("metadata", {})
        metadata = cast(dict[str, Any], metadata)

        if role == "system":
            return SystemMessage(content=content, metadata=metadata)
        elif role == "user":
            return UserMessage(content=content, metadata=metadata)
        elif role == "assistant":
            raw_tool_calls = data.get("tool_calls", [])
            tool_calls: list[ToolCallRequest] = []
            skipped: list[dict[str, Any]] = []
            for index, tc in enumerate(raw_tool_calls):
                tool_call = cast(dict[str, Any], tc) if isinstance(tc, dict) else None
                tc_id = tool_call.get("id") if tool_call is not None else None
                tc_name = tool_call.get("name") if tool_call is not None else None
                tc_args = tool_call.get("arguments") if tool_call is not None else None
                if not isinstance(tc_id, str) or not tc_id:
                    skipped.append(
                        {
                            "index": index,
                            "name": tc_name,
                            "raw_id_value": tc_id,
                        }
                    )
                    continue
                if not isinstance(tc_name, str) or not isinstance(tc_args, str):
                    skipped.append(
                        {
                            "index": index,
                            "name": tc_name,
                            "raw_id_value": tc_id,
                        }
                    )
                    continue
                tool_calls.append(ToolCallRequest(id=tc_id, name=tc_name, arguments=tc_args))
            if skipped:
                first = skipped[0]
                details: dict[str, Any] = {
                    "source": "history_restore",
                    "provider": None,
                    "model": None,
                    "tool_name": first.get("name"),
                    "tool_call_index": first.get("index"),
                    "raw_id_value": first.get("raw_id_value"),
                    "skipped_count": len(skipped),
                    "session_id": metadata.get("session_id"),
                }
                if history_restore_strategy == "raise":
                    raise InvalidToolCallIdError(
                        source="history_restore",
                        raw_id_value=first.get("raw_id_value"),
                        tool_name=first.get("name"),
                        tool_call_index=first.get("index"),
                        extra={
                            "skipped_count": len(skipped),
                            "session_id": details["session_id"],
                        },
                    )
            return AssistantMessage(content=content, metadata=metadata, tool_calls=tool_calls)
        elif role == "tool":
            tool_name = data["tool_name"]
            tool_call_id = data.get("tool_call_id", "")
            return ToolMessage(
                content=content, tool_name=tool_name, tool_call_id=tool_call_id, metadata=metadata
            )
        else:
            raise ValueError(f"未知的消息角色: {role!r}")


@dataclass
class SystemMessage(BaseMessage):
    """系统提示词消息。

    role 固定为 "system"，用于向 AI 模型传递系统级指令和上下文设定。
    构造时仅需提供 content 和可选的 metadata，无需手动指定 role。

    Attributes:
        content: 系统提示词文本内容
        metadata: 扩展元数据字典，默认为空字典
    """

    @property
    def role(self) -> str:
        """返回固定角色标识 "system"。"""
        return "system"


@dataclass
class UserMessage(BaseMessage):
    """用户输入消息。

    role 固定为 "user"，表示用户发送的对话消息。
    构造时仅需提供 content 和可选的 metadata，无需手动指定 role。

    Attributes:
        content: 用户输入的文本内容
        metadata: 扩展元数据字典，默认为空字典
    """

    @property
    def role(self) -> str:
        """返回固定角色标识 "user"。"""
        return "user"


@dataclass
class AssistantMessage(BaseMessage):
    """AI 助手回复消息。

    role 固定为 "assistant"，表示 AI 助手生成的回复消息。
    可选携带 tool_calls 字段，记录 LLM 返回的工具调用请求列表。
    当 tool_calls 非空时，表示助手请求执行一个或多个工具调用。

    Attributes:
        content: 助手回复的文本内容
        metadata: 扩展元数据字典，默认为空字典
        tool_calls: LLM 返回的工具调用请求列表，默认为空列表
    """

    tool_calls: list[ToolCallRequest] = field(default_factory=list[ToolCallRequest])

    @property
    def role(self) -> str:
        """返回固定角色标识 "assistant"。"""
        return "assistant"

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典，当 tool_calls 非空时包含该字段。

        在基类 to_dict() 输出的基础上，当 tool_calls 列表非空时添加 tool_calls 键，
        每个元素序列化为包含 id、name、arguments 的字典。
        当 tool_calls 为空列表时不包含该键，确保与现有序列化格式向后兼容。

        Returns:
            包含 role、content 字段的字典，tool_calls 仅在非空时包含，
            metadata 仅在非空时包含
        """
        data = super().to_dict()
        if self.tool_calls:
            data["tool_calls"] = [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in self.tool_calls
            ]
        return data


@dataclass
class ToolMessage(BaseMessage):
    """工具调用结果消息。

    role 固定为 "tool"，表示工具执行后返回的结果消息。
    除了基类的 content 和 metadata 外，额外携带 tool_name 必填字段和
    tool_call_id 字段（用于将工具执行结果与对应的调用请求关联）。
    重写 to_dict() 方法以在序列化输出中包含 tool_name 和 tool_call_id 字段。

    Attributes:
        content: 工具执行结果的文本内容
        tool_name: 工具名称，必填
        tool_call_id: 工具调用的唯一标识符，用于关联 LLM 的调用请求，默认为空字符串（向后兼容）
        metadata: 扩展元数据字典，默认为空字典
    """

    tool_name: str
    tool_call_id: str = ""

    @property
    def role(self) -> str:
        """返回固定角色标识 "tool"。"""
        return "tool"

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典，包含 tool_name 和 tool_call_id 字段。

        在基类 to_dict() 输出的基础上添加 tool_name 和 tool_call_id 字段，
        与旧 Message.to_dict() 中 tool_name 非 None 时的输出格式一致，
        同时包含 tool_call_id 以支持 function calling 流程。

        Returns:
            包含 role、content、tool_name、tool_call_id 字段的字典，
            metadata 仅在非空时包含
        """
        data = super().to_dict()
        data["tool_name"] = self.tool_name
        data["tool_call_id"] = self.tool_call_id
        return data


# 向后兼容：保留 Message 名称作为 BaseMessage 的类型别名。
# 确保 `from domain.chat.context import Message` 的导入语句继续有效，
# 减少迁移成本。Message.from_dict(data) 也能正确分派到对应子类。
Message = BaseMessage


class ConversationContext:
    """对话上下文值对象。

    管理对话消息列表，作为纯粹的消息容器。仅负责消息的存储、访问和序列化/反序列化，
    不包含任何裁剪或压缩逻辑。消息的裁剪/压缩由 ContextCompactionPort 的实现在
    编排层（ChatServiceAdapter）中执行。

    Attributes:
        _messages: 内部消息列表。
        event_timestamps: 事件时间戳索引，``message_index → 事件发生时刻毫秒整数``。
            由 ``ReActAgentAdapter._stamp_event`` 在事件实际发生时写入，
            供 ``TaskAgentAdapter._extract_trace`` 读取真实时刻；参与
            ``to_dict`` / ``from_dict`` 序列化（默认值即空 dict 时序列化输出
            **省略**该键以保持紧凑），HITL resume 路径下通过
            ``ApprovalInterrupt.context_snapshot`` 自然回环恢复。
            字段类型仅使用 Python 标准库 ``dict[int, int]``，不引入 ORM /
            Pydantic / Redis 类型，符合 DDD 领域纯度约束。
        session_id: 该上下文所属的会话 ID。``ChatServiceAdapter`` 在
            ``chat`` / ``stream_chat`` / ``stream_chat_events`` /
            ``resume_approval`` 四个入口设置，供
            ``ReActAgentAdapter._save_interrupt`` 读取。可选字段，默认
            ``None``，为 ``None`` 时序列化输出省略。字段类型仅使用 Python
            标准库 ``str | None``，符合 DDD 领域纯度约束。
    """

    def __init__(self) -> None:
        """初始化对话上下文。

        创建一个空的消息容器，并把 ``event_timestamps`` 与 ``session_id``
        两个正式字段在所有实例上以"空 dict / None"的默认形态初始化，使
        基础设施层的写入路径无需再做"懒创建"或 ``hasattr`` 检查。
        """
        self._messages: list[BaseMessage] = []
        self.event_timestamps: dict[int, int] = {}
        self.session_id: str | None = None

    def add_system_message(self, content: str) -> None:
        """添加系统消息。

        Args:
            content: 消息内容
        """
        self._messages.append(SystemMessage(content=content))

    def add_user_message(self, content: str) -> None:
        """添加用户消息。

        Args:
            content: 消息内容
        """
        self._messages.append(UserMessage(content=content))

    def add_assistant_message(self, content: str) -> None:
        """添加助手消息。

        Args:
            content: 消息内容
        """
        self._messages.append(AssistantMessage(content=content))

    def add_assistant_message_with_tool_calls(
        self,
        content: str,
        tool_calls: list[ToolCallRequest],
    ) -> int:
        """追加一条携带工具调用的助手消息，返回新追加消息的索引。

        与 ``add_assistant_message`` 的差异：本方法把模型返回的 ``tool_calls``
        一并写入 ``AssistantMessage.tool_calls``，用于在 ReAct Loop 中表达
        "助手请求执行工具"的语义；``add_assistant_message`` 仅追加纯文本助手
        消息。基础设施层的 Agent 适配器禁止直接访问 ``_messages`` 列表，应通过
        本方法完成"携带 tool_calls 的助手消息"追加，避免破坏 ConversationContext
        的封装边界。

        Args:
            content: 助手回复的文本内容；可能为空字符串（模型仅返回 tool_calls
                而无伴随文本）。
            tool_calls: LLM 返回的工具调用请求列表，按模型返回顺序。本方法内部
                通过 ``list(tool_calls)`` 拷贝一次，避免外部对该列表的后续修改
                影响已追加的消息。

        Returns:
            新追加消息在 ``_messages`` 中的索引，即追加后 ``len(_messages) - 1``。
            供调用方（典型为 ``ReActAgentAdapter._record_assistant_with_tool_calls``）
            打戳事件时间戳使用，避免依赖 ``message_count - 1`` 的隐式约定。

        Notes:
            本方法不校验 ``tool_calls`` 是否为空——空列表的语义等价于
            ``add_assistant_message(content)``。但调用方仍应优先使用
            ``add_assistant_message`` 表达"无工具调用的助手回复"。
        """
        self._messages.append(AssistantMessage(content=content, tool_calls=list(tool_calls)))
        return len(self._messages) - 1

    def add_tool_result(self, tool_name: str, result: str, tool_call_id: str = "") -> int:
        """添加工具调用结果消息，返回新追加消息的索引。

        Args:
            tool_name: 工具名称
            result: 工具执行结果
            tool_call_id: 工具调用的唯一标识符，用于关联 LLM 的调用请求，默认为空字符串

        Returns:
            新追加消息在 ``_messages`` 中的索引，即追加后 ``len(_messages) - 1``。
            供 ``ReActAgentAdapter._execute_tool_call`` 回填 ``error=True`` 失败
            标记与 ``_stamp_event`` 打戳使用，避免依赖 ``message_count - 1``
            的隐式约定。
        """
        self._messages.append(
            ToolMessage(content=result, tool_name=tool_name, tool_call_id=tool_call_id)
        )
        return len(self._messages) - 1

    def get_messages(self) -> list[BaseMessage]:
        """获取完整的消息列表。

        返回所有已添加的消息，不执行任何裁剪或过滤操作。
        消息的裁剪/压缩由 ContextCompactionPort 的实现在编排层中执行。

        Returns:
            完整的 BaseMessage 对象列表
        """
        return list(self._messages)

    def replace_messages(self, messages: list[BaseMessage]) -> None:
        """Replace the conversation message sequence with a defensive copy."""
        self._messages = list(messages)

    def append_message(self, message: BaseMessage) -> int:
        """通用消息追加方法。

        与 ``add_system_message`` / ``add_user_message`` /
        ``add_assistant_message_with_tool_calls`` / ``add_tool_result``
        等"按角色专用追加方法"形成正交关系：本方法接受**任意**
        ``BaseMessage`` 子类实例，通常用于"上下文整体克隆 / 转交"等需要
        复用既有消息引用的场景，例如 ``DelegationAdapter.handoff(...)`` 把
        父 ``ConversationContext`` 消息列表整体转交给目标 Agent 的初始上下文。

        本方法**不**对消息内容做任何深拷贝；调用方负责保证传入的消息为
        不可变值对象（``BaseMessage`` 及其子类均为 ``dataclass``，对外契约
        是不可变）。

        Args:
            message: 待追加的消息实例（``SystemMessage`` /``UserMessage`` /
                ``AssistantMessage`` / ``ToolMessage`` 任一）。

        Returns:
            新追加消息在 ``_messages`` 中的索引。
        """
        self._messages.append(message)
        return len(self._messages) - 1

    def clear(self) -> None:
        """清空所有消息。"""
        self._messages.clear()

    @property
    def message_count(self) -> int:
        """当前消息总数。"""
        return len(self._messages)

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（紧凑策略）。

        默认仅输出 ``messages``；当 ``event_timestamps`` 非空时附加
        ``event_timestamps`` 键（写入时通过 ``dict(self.event_timestamps)``
        拷贝一份，避免外部修改污染序列化结果）；当 ``session_id`` 非
        ``None`` 时附加 ``session_id`` 键。

        紧凑策略保证：旧格式数据通过 ``from_dict`` 反序列化后立即再
        ``to_dict`` 时输出与原数据等价（仅含 ``messages``），不会因新增
        字段为默认值而引入"伪写入",符合 NFR-4 向后兼容序列化要求。

        关于 JSON 友好性：``event_timestamps`` 的键为 ``int`` 类型，
        ``json.dumps`` 会自动 stringify 为 ``str``；``from_dict`` 反序列化
        时显式 ``int(k): int(v)`` 还原回 ``dict[int, int]``，使
        ``_extract_trace`` 用 ``int`` 索引查表时不会全部 miss。

        Returns:
            紧凑序列化字典，默认值字段被省略。
        """
        data: dict[str, Any] = {"messages": [m.to_dict() for m in self._messages]}
        if self.event_timestamps:
            data["event_timestamps"] = dict(self.event_timestamps)
        if self.session_id is not None:
            data["session_id"] = self.session_id
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConversationContext":
        """从字典反序列化创建 ConversationContext 实例（向后兼容）。

        使用 BaseMessage.from_dict 进行反序列化，确保还原正确的消息子类型。
        本方法兼容三种历史/当前格式：

        - **v1 旧格式**：仅含 ``messages``，可能含被忽略的 ``max_messages``
          字段。该格式不含 ``event_timestamps`` 与 ``session_id``，反序列化
          后两字段分别取默认值 ``{}`` 与 ``None``。
        - **混合旧格式**：含 ``messages`` 与 ``event_timestamps`` /
          ``session_id`` 之一。缺失字段同样取默认值。
        - **新格式**：含 ``messages`` / ``event_timestamps`` / ``session_id``
          三者完整。

        关于键类型还原：JSON 不支持 ``int`` 键，``json.dumps({1: 1000})``
        会自动 stringify 为 ``{"1": 1000}``；本方法在 ``event_timestamps``
        非空且为 ``dict`` 时显式 ``int(k): int(v)`` 还原，避免下游用
        ``int`` 索引查表 miss。``session_id`` 字段值为 ``null`` /
        缺失均视为 ``None``。

        Args:
            data: 包含 messages 的字典，可选包含 ``event_timestamps`` /
                ``session_id`` / ``max_messages``（``max_messages`` 字段
                将被忽略，确保向后兼容已持久化的会话数据）。

        Returns:
            反序列化后的 ConversationContext 实例
        """
        ctx = cls()
        ctx._messages = [BaseMessage.from_dict(m) for m in data.get("messages", [])]
        raw_ts = data.get("event_timestamps")
        if isinstance(raw_ts, dict):
            raw_ts = cast(dict[Any, Any], raw_ts)
            ctx.event_timestamps = {int(k): int(v) for k, v in raw_ts.items()}
        ctx.session_id = data.get("session_id")
        return ctx
