"""聊天服务适配器模块（编排层）。

实现 ChatServicePort，作为对话流程的编排层：加载上下文 → 注入系统提示词 →
追加用户消息 → 根据配置选择执行路径（Agent 委托或直接 LLM 调用）→
保存完整上下文 → 返回结果。支持同步对话和流式对话两种模式。

本模块属于基础设施层，协调 SessionContextStorePort、ContextBuilderPort、
ModelRegistryPort 和 AgentPort 四个端口完成完整的对话编排。
当 tool_calling_enabled 为 True 且有已注册工具时，将 Agent Loop 执行委托给
AgentPort；否则保持直接 LLM 调用行为。

编排层不再直接包含 Agent Loop 执行逻辑（_run_agent_loop / _run_agent_loop_streaming），
这些逻辑已迁移到 ReActAgentAdapter 中。上下文压缩、环境上下文注入和
消息序列化由 ContextBuilderPort 统一装配。

上下文构建通过 ContextBuilderPort 注入，在直接 LLM 调用路径中生成模型输入，
而保存到 SessionContextStorePort 的始终是完整会话历史，确保对话历史完整性。
Agent 委托路径不在本适配器内调用 builder，由 AgentPort 实现内部处理。
"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Literal, Protocol

from opentelemetry import trace

from domain.agent.ports import AgentPort, ApprovalStateStorePort
from domain.agent.segmented_execution import SegmentExecutionPolicy, SegmentRunMetadata
from domain.agent.value_objects import AgentConfig, AgentResult, AgentStreamEvent
from domain.chat.context import ConversationContext, ToolMessage
from domain.chat.exceptions import ContinuationUnavailableError
from domain.chat.ports import (
    ChatServicePort,
    ContextBuilderPort,
    SessionContextStorePort,
    SessionIndexPort,
)
from domain.chat.value_objects import (
    ApprovalResumeRequestVO,
    ChatContinueRequestVO,
    ChatRequestVO,
    ChatResponseVO,
)
from domain.model_access.ports import ModelAccessPort, ModelRegistryPort
from domain.model_access.value_objects import ChatRequest, StreamingChunk
from domain.prompt.ports import PromptRegistryPort
from infrastructure.agent.approval_serialization import approval_payload_to_metadata
from infrastructure.agent.segment_serialization import segment_run_metadata_to_http_dict
from infrastructure.chat.usage import merge_usage

logger = logging.getLogger(__name__)

_RunAgentCallable = Callable[[ConversationContext, str | None], Awaitable[AgentResult]]
_RunChatCallable = Callable[[ConversationContext, str | None], Awaitable[ChatResponseVO]]


class _SegmentStreamFrameProtocol(Protocol):
    """Chat 分段流应用层业务帧结构。"""

    kind: Literal["forward", "segment_done", "final_done"]
    event: AgentStreamEvent | None
    usage: dict[str, int] | None
    segment_metadata: SegmentRunMetadata | None


class _SessionWorkflowProtocol(Protocol):
    """ChatServiceAdapter 依赖的会话 workflow 结构协议。"""

    @property
    def prompt_id(self) -> str:
        """返回当前 Prompt 标识符。"""
        ...

    async def load_for_chat(self, request: ChatRequestVO) -> ConversationContext:
        """加载新聊天上下文并追加用户消息。"""
        ...

    async def load_for_continue(self, request: ChatContinueRequestVO) -> ConversationContext:
        """加载继续执行上下文。"""
        ...

    def ensure_system_prompt(self, context: ConversationContext) -> None:
        """幂等注入系统提示词。"""
        ...

    async def save_context_and_index(
        self,
        session_id: str,
        context: ConversationContext,
        *,
        model: str | None = None,
    ) -> None:
        """保存上下文并刷新会话索引。"""
        ...


class _ChatApplicationServiceProtocol(Protocol):
    """ChatServiceAdapter 依赖的聊天应用服务结构协议。"""

    async def continue_chat(
        self,
        request: ChatContinueRequestVO,
        *,
        run_agent: _RunAgentCallable | None = None,
        run_chat: _RunChatCallable | None = None,
    ) -> ChatResponseVO:
        """基于已有上下文继续执行聊天 Agent。"""
        ...

    async def resume_approval_to_agent_result(
        self,
        request: ApprovalResumeRequestVO,
    ) -> tuple[ConversationContext, AgentResult]:
        """审批恢复后返回上下文与 AgentResult。"""
        ...

    async def run_segmented_chat_on_context(
        self,
        *,
        session_id: str,
        context: ConversationContext,
        model: str | None,
        run_agent: _RunAgentCallable,
    ) -> ChatResponseVO:
        """在既有上下文上执行同步分段 Agent 聊天。"""
        ...

    def stream_segmented_chat_on_context(
        self,
        *,
        session_id: str,
        context: ConversationContext,
        model: str | None,
        run_events: Callable[
            [ConversationContext, str | None],
            AsyncIterator[AgentStreamEvent],
        ],
    ) -> AsyncIterator[_SegmentStreamFrameProtocol]:
        """在既有上下文上执行分段 Agent 事件流业务编排。"""
        ...


class ChatServiceAdapter(ChatServicePort):
    """聊天服务适配器（编排层），实现 ChatServicePort。

    作为对话流程的编排层，负责上下文管理、系统提示词注入、执行路径选择和
    上下文持久化。不再直接包含 Agent Loop 执行逻辑，而是通过 AgentPort
    委托给具体的 Agent 实现。

    执行路径选择：
    - 当 tool_calling_enabled 为 True 且有已注册工具时：构造 AgentConfig，
      委托 AgentPort 执行 Agent Loop（推理→行动→观察循环）
    - 当 tool_calling_enabled 为 False 或无已注册工具时：压缩上下文后直接调用 LLM

    两条路径最终汇合：追加最终助手回复 → 保存完整未压缩上下文 → 返回响应。

    Attributes:
        _session_store: 会话上下文存储端口，用于加载和保存对话历史。
        _model_registry: 模型注册中心端口，用于根据请求动态获取对应的模型适配器。
        _system_prompt: 默认系统提示词，在上下文无 system 消息时自动注入。
        _context_builder: 上下文构建端口，用于直接 LLM 调用路径中生成模型输入。
        _agent: Agent 端口实例，用于 tool_calling_enabled 时委托 Agent Loop 执行。
        _tool_calling_enabled: 是否启用 function calling 功能，为 False 时退化为普通对话模式。
        _max_tool_rounds: Agent Loop 最大迭代轮次，用于构造 AgentConfig。
        _tool_schemas: 工具 schema 列表，用于判断是否有已注册工具及构造 AgentConfig。
    """

    def __init__(
        self,
        session_store: SessionContextStorePort,
        model_registry: ModelRegistryPort,
        prompt_registry: "PromptRegistryPort",
        context_builder: ContextBuilderPort,
        agent: AgentPort,
        tool_calling_enabled: bool,
        max_tool_rounds: int,
        tool_schemas: list[dict[str, Any]],
        approval_store: ApprovalStateStorePort | None = None,
        session_index: SessionIndexPort | None = None,
        segment_policy: SegmentExecutionPolicy | None = None,
        session_workflow: _SessionWorkflowProtocol | None = None,
        chat_application_service: _ChatApplicationServiceProtocol | None = None,
    ) -> None:
        """初始化聊天服务适配器（编排层）。

        构造期调用 ``prompt_registry.get('chat-default')`` 一次性加载 Prompt；
        运行期不再查注册表。``_WORKSPACE_PATH_GUIDANCE`` 追加仅在构造期做一次。

        Args:
            session_store: 会话上下文存储端口实例。
            model_registry: 模型注册中心端口实例，用于根据请求动态获取对应的模型适配器。
            prompt_registry: Prompt 注册表端口实例，用于加载 chat-default Prompt。
            context_builder: 上下文构建端口实例，用于直接 LLM 调用路径中生成模型输入。
            agent: Agent 端口实例，用于 tool_calling_enabled 时委托 Agent Loop 执行。
            tool_calling_enabled: 是否启用 function calling 功能，为 False 时不委托 Agent。
            max_tool_rounds: Agent Loop 最大迭代轮次，用于构造 AgentConfig。
            tool_schemas: 工具 schema 列表，由 ToolRegistry.get_schemas() 生成，
                用于判断是否有已注册工具及构造 AgentConfig。
            session_index: 会话元数据索引端口实例，用于列出和恢复历史会话。
            session_workflow: 由组合根显式注入的聊天会话 workflow。
            chat_application_service: 由组合根显式注入的聊天应用服务。
        """
        from infrastructure.chat.chat_default_prompt import resolve_chat_default_system_prompt

        self._session_store = session_store
        self._model_registry = model_registry
        self._context_builder = context_builder
        self._agent = agent
        self._tool_calling_enabled = tool_calling_enabled
        self._max_tool_rounds = max_tool_rounds
        self._tool_schemas = tool_schemas
        self._approval_store = approval_store
        self._session_index = session_index
        self._segment_policy = segment_policy or SegmentExecutionPolicy()

        # 构造期一次性加载 Prompt（单一来源，行为等价）
        resolved_prompt = resolve_chat_default_system_prompt(prompt_registry)
        self._system_prompt = resolved_prompt.system_prompt
        self._prompt_id = resolved_prompt.prompt_id
        self._session_workflow = session_workflow
        self._chat_application_service = chat_application_service

    def _require_session_workflow(self) -> _SessionWorkflowProtocol:
        """返回组合根注入的会话 workflow。"""

        if self._session_workflow is None:
            raise RuntimeError("ChatServiceAdapter requires injected session_workflow")
        return self._session_workflow

    def _require_chat_application_service(self) -> _ChatApplicationServiceProtocol:
        """返回组合根注入的聊天应用服务。"""

        if self._chat_application_service is None:
            raise RuntimeError("ChatServiceAdapter requires injected chat_application_service")
        return self._chat_application_service

    @property
    def prompt_id(self) -> str:
        """当前加载的 Prompt 标识符（``ChatServicePort.prompt_id``）。

        路由层在 SSE 流结束时通过本属性读取 ``prompt_id`` 并写入紧邻
        ``[DONE]`` 的事件载体（决策 #2）。
        """
        return self._prompt_id

    def _resolve_model_access(self, model: str | None) -> tuple[ModelAccessPort, str]:
        """根据请求中的 model 参数解析对应的模型适配器。

        当 model 不为 None 时，直接通过 ModelRegistryPort 获取指定模型的适配器；
        当 model 为 None 时，先获取默认模型名称，再获取对应的适配器。

        异常由 ModelRegistryPort 实现（ProviderRegistry）抛出，直接向上传播。

        Args:
            model: 请求指定的模型名称，为 None 时使用默认模型。

        Returns:
            (适配器实例, 实际使用的模型名称) 元组。

        Raises:
            ModelAccessError: 模型未注册或无可用提供商。
            NoAvailableModelError: 无可用模型（model 为 None 且注册中心无任何模型时）。
        """
        if model is not None:
            adapter = self._model_registry.get_adapter_for_model(model)
            return adapter, model
        default_model = self._model_registry.get_default_model()
        adapter = self._model_registry.get_adapter_for_model(default_model)
        return adapter, default_model

    def _make_agent_config(self, model: str | None) -> AgentConfig:
        """构造聊天 Agent 单段执行配置。"""
        return AgentConfig(
            system_prompt=self._system_prompt,
            tool_schemas=self._tool_schemas,
            model=model,
            max_rounds=self._max_tool_rounds,
            prompt_id=self._prompt_id,
        )

    @staticmethod
    def _can_continue_from_context(context: ConversationContext) -> bool:
        """判断上下文尾部是否满足继续执行前置条件。"""
        messages = context.get_messages()
        return bool(messages) and isinstance(messages[-1], ToolMessage)

    async def _save_context_and_index(
        self,
        session_id: str,
        context: ConversationContext,
        *,
        model: str | None = None,
    ) -> None:
        """保存完整上下文，并委托会话 workflow 同步刷新索引。"""
        await self._require_session_workflow().save_context_and_index(
            session_id,
            context,
            model=model,
        )

    @staticmethod
    def _segment_risk_gate_required(
        *,
        context: ConversationContext,
        pre_message_count: int,
        approval_required: bool = False,
        approval_metadata: dict[str, Any] | None = None,
    ) -> tuple[bool, str | None]:
        """根据本段新增稳定 metadata 判断是否需要风险门禁。

        优先读取本段新增 ``ToolMessage.metadata`` 中由 guardrail 写入的稳定标记；
        当本段直接进入 ``approval_required`` 且审批来源为 guardrail 时，再从
        审批事件 metadata 补充读取。observe 模式下虽然可能携带
        ``guardrail_action="observe"``，但 ``risk_gate_required`` 不会被置真。

        Args:
            context: 当前会话上下文。
            pre_message_count: 本段执行前的消息数量，用于只检查新增消息。
            approval_required: 本段是否以审批状态结束。
            approval_metadata: ``approval_required`` 事件或响应附带的元数据。

        Returns:
            ``(risk_gate_required, guardrail_reason)`` 元组。
        """
        guardrail_reason: str | None = None
        messages = context.get_messages()
        for message in messages[pre_message_count:]:
            if not isinstance(message, ToolMessage):
                continue
            metadata = message.metadata or {}
            if metadata.get("risk_gate_required") is True:
                return True, metadata.get("guardrail_reason")
            if guardrail_reason is None:
                guardrail_reason = metadata.get("guardrail_reason")

        approval_data = approval_metadata or {}
        if approval_required and approval_data.get("source") == "guardrail":
            return True, approval_data.get("guardrail_reason") or guardrail_reason
        return False, guardrail_reason

    def _to_chat_response(
        self,
        *,
        session_id: str,
        context: ConversationContext,
        agent_result: AgentResult,
    ) -> ChatResponseVO:
        """把 AgentResult 翻译为聊天响应值对象。"""
        if agent_result.status == "approval_required":
            approval = agent_result.approval
            assert approval is not None
            return ChatResponseVO(
                session_id=session_id,
                reply="",
                model=agent_result.model,
                usage=agent_result.usage,
                prompt_id=self._prompt_id,
                status="approval_required",
                approval_id=approval.approval_id,
                action_requests=approval.actions,
                terminated_reason="completed",
                can_continue=False,
            )

        terminated_reason = getattr(agent_result, "terminated_reason", "completed")
        if terminated_reason not in ("max_rounds", "token_budget_exceeded"):
            return ChatResponseVO(
                session_id=session_id,
                reply=agent_result.content,
                model=agent_result.model,
                usage=agent_result.usage,
                prompt_id=self._prompt_id,
                status="completed",
                terminated_reason="completed",
                can_continue=False,
            )

        return ChatResponseVO(
            session_id=session_id,
            reply="",
            model=agent_result.model,
            usage=agent_result.usage,
            prompt_id=self._prompt_id,
            status="paused",
            terminated_reason=agent_result.terminated_reason,
            can_continue=self._can_continue_from_context(context),
        )

    async def _save_context_for_agent_result(
        self,
        *,
        session_id: str,
        context: ConversationContext,
        agent_result: AgentResult,
    ) -> None:
        """按 Agent 终止语义保存聊天上下文。"""
        if agent_result.status == "approval_required":
            return
        terminated_reason = getattr(agent_result, "terminated_reason", "completed")
        if terminated_reason not in ("max_rounds", "token_budget_exceeded"):
            context.add_assistant_message(agent_result.content)
        await self._save_context_and_index(
            session_id,
            context,
            model=agent_result.model,
        )

    async def _run_segmented_agent_on_context(
        self,
        *,
        session_id: str,
        context: ConversationContext,
        model: str | None,
        model_access: ModelAccessPort,
    ) -> ChatResponseVO:
        """委托应用服务在既有上下文上执行一组分段 Agent_Run。"""

        async def _run_agent(
            current_context: ConversationContext,
            current_model: str | None,
        ) -> AgentResult:
            return await self._agent.run(
                current_context,
                self._make_agent_config(current_model),
                model_access,
            )

        return await self._require_chat_application_service().run_segmented_chat_on_context(
            session_id=session_id,
            context=context,
            model=model,
            run_agent=_run_agent,
        )

    async def _run_segmented_chat(
        self,
        request: ChatRequestVO,
        *,
        context: ConversationContext,
        model_access: ModelAccessPort,
    ) -> ChatResponseVO:
        """执行首段聊天及必要的自动续跑。"""
        return await self._run_segmented_agent_on_context(
            session_id=request.session_id,
            context=context,
            model=request.model,
            model_access=model_access,
        )

    async def chat(self, request: ChatRequestVO) -> ChatResponseVO:
        """同步对话，支持 Agent 委托和直接 LLM 调用两种路径。

        完整流程：加载上下文 → 注入系统提示词 → 追加用户消息 → 根据工具调用配置
        选择执行路径：
        - 启用 function calling 且有已注册工具时：构造 AgentConfig，委托 AgentPort.run()
          执行 Agent Loop，将 AgentResult 转换为 ChatResponseVO。Agent Loop 内部负责
          将中间轮次的 AssistantMessage（含 tool_calls）和 ToolMessage 追加到上下文。
        - 未启用 function calling 或无已注册工具时：保持原有行为，压缩上下文后直接调用 LLM。

        两条路径最终汇合：追加最终助手回复 → 保存完整未压缩上下文 → 返回响应。
        保存到 SessionContextStorePort 的始终是包含所有消息的完整上下文，确保对话历史完整性。

        Args:
            request: 聊天请求值对象，包含 session_id、message 和 stream 标志。

        Returns:
            聊天响应值对象，包含回复内容、模型名称和累计 token 用量。

        Raises:
            ModelAccessError: 模型调用失败时向上传播。
        """
        trace.get_current_span().set_attribute("prompt.id", self._prompt_id)
        logger.info(
            "ChatServiceAdapter.chat 开始",
            extra={"prompt_id": self._prompt_id, "session_id": request.session_id},
        )

        context = await self._require_session_workflow().load_for_chat(request)

        model_access, resolved_model = self._resolve_model_access(request.model)

        if self._tool_calling_enabled and self._tool_schemas:
            response = await self._run_segmented_chat(
                request,
                context=context,
                model_access=model_access,
            )
            logger.info(
                "ChatServiceAdapter.chat 完成",
                extra={"prompt_id": self._prompt_id, "session_id": request.session_id},
            )
            return response
        else:
            builder_result = await self._context_builder.build(
                context.get_messages(),
                model_access=model_access,
                model=resolved_model,
            )
            chat_request = ChatRequest(
                messages=builder_result.messages,
                model=resolved_model,
            )
            response = await model_access.chat(chat_request)
            response_content = response.content
            response_model = response.model
            response_usage = merge_usage(builder_result.usage, response.usage)

        context.add_assistant_message(response_content)
        await self._save_context_and_index(
            request.session_id,
            context,
            model=response_model,
        )

        logger.info(
            "ChatServiceAdapter.chat 完成",
            extra={"prompt_id": self._prompt_id, "session_id": request.session_id},
        )
        return ChatResponseVO(
            session_id=request.session_id,
            reply=response_content,
            model=response_model,
            usage=response_usage,
            prompt_id=self._prompt_id,
        )

    async def _resume_to_agent_result(
        self,
        request: ApprovalResumeRequestVO,
    ) -> tuple[ConversationContext, AgentResult]:
        """委托应用服务完成审批恢复核心编排。

        Args:
            request: 审批恢复请求值对象，包含会话、审批批次和有序决策。

        Returns:
            (恢复后的对话上下文, 恢复执行的 AgentResult) 元组。

        Raises:
            ApprovalNotFoundError: 审批状态存储未配置或批次不存在。
            ApprovalExpiredError: 审批批次已过期。
            ApprovalDecisionCountMismatchError: 决策数量与动作数不一致。
            ApprovalDecisionOrderMismatchError: 决策 tool_call_id 与动作不匹配。
            ApprovalDecisionNotAllowedError: 决策类型不在动作 allowed_decisions 内。
            ApprovalConsumedError: 审批批次已被消费（重复恢复）。
        """
        return await self._require_chat_application_service().resume_approval_to_agent_result(
            request
        )

    async def resume_approval(self, request: ApprovalResumeRequestVO) -> ChatResponseVO:
        """提交审批决策并恢复聊天执行。"""
        context, agent_result = await self._resume_to_agent_result(request)
        await self._save_context_for_agent_result(
            session_id=request.session_id,
            context=context,
            agent_result=agent_result,
        )
        return self._to_chat_response(
            session_id=request.session_id,
            context=context,
            agent_result=agent_result,
        )

    async def stream_resume_approval(
        self,
        request: ApprovalResumeRequestVO,
    ) -> AsyncIterator[AgentStreamEvent]:
        """提交审批决策并以结构化事件流恢复执行。

        复用 ``_resume_to_agent_result`` 内核完成决策校验、原子消费与
        ``agent.resume`` 执行，再把返回的 ``AgentResult`` 翻译为与
        ``stream_chat_events`` 同构的事件序列：恢复后再次进入审批中断时产出
        ``kind="approval_required"``；否则以单段 ``assistant_delta`` 加
        ``assistant_done`` 产出。paused（max_rounds / token_budget_exceeded）
        时 content 为空、仅产出 ``assistant_done``，本特性不新增 paused 续跑 UI。

        Args:
            request: 审批恢复请求值对象，包含会话、审批批次和有序决策。

        Yields:
            AgentStreamEvent：assistant_delta / assistant_done 或再次 approval_required。
        """
        context, agent_result = await self._resume_to_agent_result(request)
        await self._save_context_for_agent_result(
            session_id=request.session_id,
            context=context,
            agent_result=agent_result,
        )
        if agent_result.status == "approval_required":
            approval = agent_result.approval
            assert approval is not None
            yield AgentStreamEvent(
                kind="approval_required",
                content="当前请求等待人工审批，请通过审批恢复接口提交决策。",
                usage=agent_result.usage,
                metadata=approval_payload_to_metadata(approval),
            )
            return
        if agent_result.content:
            yield AgentStreamEvent(kind="assistant_delta", content=agent_result.content)
        yield AgentStreamEvent(
            kind="assistant_done",
            usage=agent_result.usage,
            metadata={"terminated_reason": agent_result.terminated_reason},
        )

    async def continue_chat(self, request: ChatContinueRequestVO) -> ChatResponseVO:
        """基于已有会话上下文继续聊天 Agent 执行。"""

        async def _run_chat(
            context: ConversationContext,
            model: str | None,
        ) -> ChatResponseVO:
            model_access, _ = self._resolve_model_access(model)
            return await self._run_segmented_agent_on_context(
                session_id=request.session_id,
                context=context,
                model=model,
                model_access=model_access,
            )

        return await self._require_chat_application_service().continue_chat(
            request,
            run_chat=_run_chat,
        )

    async def restore_checkpoint_context(
        self,
        session_id: str,
        context_snapshot: dict[str, Any],
    ) -> None:
        """把 checkpoint 快照恢复为该会话的当前上下文。"""

        context = ConversationContext.from_dict(context_snapshot)
        context.session_id = session_id
        await self._save_context_and_index(session_id, context)

    async def _stream_segmented_agent_events_on_context(
        self,
        *,
        session_id: str,
        context: ConversationContext,
        model: str | None,
        model_access: ModelAccessPort,
    ) -> AsyncIterator[AgentStreamEvent]:
        """把应用服务的分段流业务帧翻译为结构化事件。"""

        def _run_events(
            current_context: ConversationContext,
            current_model: str | None,
        ) -> AsyncIterator[AgentStreamEvent]:
            return self._agent.run_events(
                current_context,
                self._make_agent_config(current_model),
                model_access,
            )

        frame_source = (
            self._require_chat_application_service().stream_segmented_chat_on_context(
                session_id=session_id,
                context=context,
                model=model,
                run_events=_run_events,
            )
        )
        async for frame in frame_source:
            if frame.kind == "forward":
                assert frame.event is not None
                yield frame.event
                continue

            assert frame.segment_metadata is not None
            metadata_fields = segment_run_metadata_to_http_dict(frame.segment_metadata)
            if frame.kind == "segment_done":
                yield AgentStreamEvent(
                    kind="assistant_done",
                    usage=frame.usage or {},
                    metadata={
                        "event_type": "segment_done",
                        "finished": False,
                        **metadata_fields,
                    },
                )
                continue

            assert frame.event is not None
            yield AgentStreamEvent(
                kind=frame.event.kind,
                content=frame.event.content,
                tool_name=frame.event.tool_name,
                tool_call_id=frame.event.tool_call_id,
                arguments=frame.event.arguments,
                usage=frame.event.usage,
                metadata={**frame.event.metadata, **metadata_fields},
            )

    async def stream_segmented_chat_events(
        self,
        request: ChatRequestVO,
    ) -> AsyncIterator[AgentStreamEvent]:
        """执行分段聊天并产出结构化事件流。"""
        context = await self._require_session_workflow().load_for_chat(request)
        model_access, _ = self._resolve_model_access(request.model)
        async for event in self._stream_segmented_agent_events_on_context(
            session_id=request.session_id,
            context=context,
            model=request.model,
            model_access=model_access,
        ):
            yield event

    async def stream_segmented_continue_chat_events(
        self,
        request: ChatContinueRequestVO,
    ) -> AsyncIterator[AgentStreamEvent]:
        """基于已有会话继续分段聊天并产出结构化事件流。"""
        context = await self._require_session_workflow().load_for_continue(request)
        if not self._can_continue_from_context(context):
            reason = "缺少可继续的上下文"
            if context.get_messages():
                reason = "最新消息不是工具结果"
            raise ContinuationUnavailableError(request.session_id, reason)
        model_access, _ = self._resolve_model_access(request.model)
        async for event in self._stream_segmented_agent_events_on_context(
            session_id=request.session_id,
            context=context,
            model=request.model,
            model_access=model_access,
        ):
            yield event

    async def stream_chat(self, request: ChatRequestVO) -> AsyncIterator[StreamingChunk]:
        """流式对话，支持 Agent 委托和直接 LLM 调用两种路径。

        完整流程：加载上下文 → 注入系统提示词 → 追加用户消息 → 根据工具调用配置
        选择执行路径：
        - 启用 function calling 且有已注册工具时：构造 AgentConfig，委托
          AgentPort.run_streaming() 执行流式 Agent Loop。
        - 未启用 function calling 或无已注册工具时：保持原有行为，压缩上下文后直接流式调用 LLM。

        两条路径最终汇合：逐个产出分片 → 最后一个分片时拼接完整回复并保存完整未压缩上下文。
        保存到 SessionContextStorePort 的始终是包含所有消息的完整上下文，确保对话历史完整性。

        Args:
            request: 聊天请求值对象，包含 session_id、message 和 stream 标志。

        Yields:
            StreamingChunk 分片，最后一个分片的 finished 为 True。

        Raises:
            ModelAccessError: 模型调用失败时停止产出并向上传播。
        """
        trace.get_current_span().set_attribute("prompt.id", self._prompt_id)
        logger.info(
            "ChatServiceAdapter.stream_chat 开始",
            extra={"prompt_id": self._prompt_id, "session_id": request.session_id},
        )

        context = await self._require_session_workflow().load_for_chat(request)

        model_access, resolved_model = self._resolve_model_access(request.model)

        if self._tool_calling_enabled and self._tool_schemas:
            config = self._make_agent_config(request.model)
            chunk_source = self._agent.run_streaming(context, config, model_access)
            builder_usage: dict[str, int] | None = None
        else:
            builder_result = await self._context_builder.build(
                context.get_messages(),
                model_access=model_access,
                model=resolved_model,
            )
            chat_request = ChatRequest(
                messages=builder_result.messages,
                model=resolved_model,
            )
            chunk_source = model_access.stream(chat_request)
            builder_usage = builder_result.usage

        full_reply_parts: list[str] = []

        async for chunk in chunk_source:
            full_reply_parts.append(chunk.delta_content)
            if chunk.finished:
                terminated_reason = chunk.metadata.get("terminated_reason", "completed")
                is_paused = terminated_reason in ("max_rounds", "token_budget_exceeded")
                if is_paused:
                    metadata = {
                        **chunk.metadata,
                        "status": "paused",
                        "terminated_reason": terminated_reason,
                        "can_continue": self._can_continue_from_context(context),
                    }
                    await self._save_context_and_index(
                        request.session_id,
                        context,
                        model=resolved_model,
                    )
                    chunk = StreamingChunk(
                        delta_content=chunk.delta_content,
                        finished=chunk.finished,
                        usage=chunk.usage,
                        metadata=metadata,
                        tool_calls=chunk.tool_calls,
                    )
                elif chunk.metadata.get("status") != "approval_required":
                    full_reply = "".join(full_reply_parts)
                    context.add_assistant_message(full_reply)
                    await self._save_context_and_index(
                        request.session_id,
                        context,
                        model=resolved_model,
                    )
                if builder_usage is not None:
                    yield StreamingChunk(
                        delta_content=chunk.delta_content,
                        finished=chunk.finished,
                        usage=merge_usage(builder_usage, chunk.usage or {}),
                        metadata=chunk.metadata,
                    )
                else:
                    yield chunk
            else:
                yield chunk

    async def stream_chat_events(self, request: ChatRequestVO) -> AsyncIterator[AgentStreamEvent]:
        """为交互式客户端流式返回结构化聊天事件。"""
        trace.get_current_span().set_attribute("prompt.id", self._prompt_id)
        logger.info(
            "ChatServiceAdapter.stream_chat_events 开始",
            extra={"prompt_id": self._prompt_id, "session_id": request.session_id},
        )

        context = await self._require_session_workflow().load_for_chat(request)

        model_access, resolved_model = self._resolve_model_access(request.model)

        if self._tool_calling_enabled and self._tool_schemas:
            config = AgentConfig(
                system_prompt=self._system_prompt,
                tool_schemas=self._tool_schemas,
                model=request.model,
                max_rounds=self._max_tool_rounds,
                prompt_id=self._prompt_id,
            )
            event_source = self._agent.run_events(context, config, model_access)
        else:
            builder_result = await self._context_builder.build(
                context.get_messages(),
                model_access=model_access,
                model=resolved_model,
            )
            chat_request = ChatRequest(
                messages=builder_result.messages,
                model=resolved_model,
            )
            event_source = self._stream_model_events(
                model_access,
                chat_request,
                builder_usage=builder_result.usage,
            )

        full_reply_parts: list[str] = []

        async for event in event_source:
            if event.kind == "assistant_delta":
                full_reply_parts.append(event.content)
            if event.kind == "approval_required":
                yield event
                return
            if event.kind == "assistant_done":
                terminated_reason = event.metadata.get("terminated_reason", "completed")
                if terminated_reason in ("max_rounds", "token_budget_exceeded"):
                    event = AgentStreamEvent(
                        kind=event.kind,
                        content=event.content,
                        tool_name=event.tool_name,
                        tool_call_id=event.tool_call_id,
                        arguments=event.arguments,
                        usage=event.usage,
                        metadata={
                            **event.metadata,
                            "status": "paused",
                            "terminated_reason": terminated_reason,
                            "can_continue": self._can_continue_from_context(context),
                        },
                    )
                else:
                    full_reply = "".join(full_reply_parts)
                    context.add_assistant_message(full_reply)
                await self._save_context_and_index(
                    request.session_id,
                    context,
                    model=resolved_model,
                )
            yield event

        logger.info(
            "ChatServiceAdapter.stream_chat_events 完成",
            extra={
                "prompt_id": self._prompt_id,
                "session_id": request.session_id,
                "model": resolved_model,
            },
        )

    async def _stream_model_events(
        self,
        model_access: ModelAccessPort,
        request: ChatRequest,
        builder_usage: dict[str, int] | None = None,
    ) -> AsyncIterator[AgentStreamEvent]:
        """Wrap direct model streaming chunks as structured events."""
        async for chunk in model_access.stream(request):
            if chunk.delta_content:
                yield AgentStreamEvent(
                    kind="assistant_delta",
                    content=chunk.delta_content,
                )
            if chunk.finished:
                yield AgentStreamEvent(
                    kind="assistant_done",
                    usage=merge_usage(builder_usage, chunk.usage or {}),
                )

    async def stream_continue_chat_events(
        self,
        request: ChatContinueRequestVO,
    ) -> AsyncIterator[AgentStreamEvent]:
        """基于已有会话上下文继续执行并产出结构化事件流。"""
        context = await self._require_session_workflow().load_for_continue(request)
        if not self._can_continue_from_context(context):
            reason = "缺少可继续的上下文"
            if context.get_messages():
                reason = "最新消息不是工具结果"
            raise ContinuationUnavailableError(request.session_id, reason)

        model_access, _ = self._resolve_model_access(request.model)
        event_source = self._agent.run_events(
            context,
            self._make_agent_config(request.model),
            model_access,
        )
        full_reply_parts: list[str] = []

        async for event in event_source:
            if event.kind == "assistant_delta":
                full_reply_parts.append(event.content)
            if event.kind == "approval_required":
                yield event
                return
            if event.kind == "assistant_done":
                terminated_reason = event.metadata.get("terminated_reason", "completed")
                if terminated_reason in ("max_rounds", "token_budget_exceeded"):
                    event = AgentStreamEvent(
                        kind=event.kind,
                        content=event.content,
                        tool_name=event.tool_name,
                        tool_call_id=event.tool_call_id,
                        arguments=event.arguments,
                        usage=event.usage,
                        metadata={
                            **event.metadata,
                            "status": "paused",
                            "terminated_reason": terminated_reason,
                            "can_continue": self._can_continue_from_context(context),
                        },
                    )
                else:
                    context.add_assistant_message("".join(full_reply_parts))
                await self._save_context_and_index(
                    request.session_id,
                    context,
                    model=request.model,
                )
            yield event

    async def clear_session(self, session_id: str) -> None:
        """清除会话上下文。

        删除指定会话的全部对话历史，使用户可以开始新的对话。

        Args:
            session_id: 会话唯一标识符。
        """
        await self._session_store.delete(session_id)
        if self._session_index is not None:
            await self._session_index.delete(session_id)
        if self._approval_store is not None:
            await self._approval_store.delete_session(session_id)
