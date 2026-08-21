"""面向任务的 Agent 适配器模块。

实现 TaskAgentPort 协议，将 Task 值对象转换为 ConversationContext + AgentConfig，
委托现有 AgentPort 执行 Agent Loop，将 AgentResult 转换为 TaskResult 返回。

本模块属于基础设施层，复用已有的 AgentPort、ToolRegistry、ModelRegistryPort、
SessionContextStorePort 和 ContextCompactionPort，不重复实现 Agent Loop 逻辑。
"""

import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol, cast

from opentelemetry import trace as _otel_trace

from domain.agent.exceptions import (
    ApprovalConsumedError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
)
from domain.agent.ports import AgentPort, ApprovalStateStorePort
from domain.agent.segmented_execution import (
    SegmentBudgetUsage,
    SegmentExecutionPolicy,
    SegmentProgressSnapshot,
    SegmentRunMetadata,
)
from domain.agent.tools import ToolRegistry
from domain.agent.value_objects import (
    AgentConfig,
    AgentResult,
    ApprovalDecision,
    ApprovalInterrupt,
)
from domain.chat.context import (
    AssistantMessage,
    BaseMessage,
    ConversationContext,
    SystemMessage,
    ToolMessage,
)
from domain.chat.exceptions import ContinuationUnavailableError
from domain.chat.ports import ContextCompactionPort, SessionContextStorePort
from domain.model_access.ports import ModelRegistryPort
from domain.prompt.ports import PromptRegistryPort
from domain.task.policy import ApprovalResumePrecondition
from domain.task.result_mapping import TaskResultMapper
from domain.task.value_objects import (
    Task,
    TaskApprovalResumeRequest,
    TaskContinueRequest,
    TaskResult,
    TaskStatus,
    TraceEntry,
)
from infrastructure.agent.segmented_orchestration import decide_next_segment
from infrastructure.agent.segmented_progress import (
    normalized_tool_call_digest,
    total_tokens_from_usage,
)
from infrastructure.chat.usage import merge_usage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _TaskRunPlan:
    """TaskApplicationService 消费的结构化执行计划。"""

    config: AgentConfig
    system_prompt: str
    allowed_tool_names: list[str] | None = None


_PrepareExecuteTaskCallable = Callable[[Task, ConversationContext], _TaskRunPlan]
_PrepareResumeTaskCallable = Callable[[str, ConversationContext, str | None], _TaskRunPlan]
_RunTaskAgentCallable = Callable[[ConversationContext, AgentConfig], Awaitable[AgentResult]]
_ResumeTaskAgentCallable = Callable[
    [ConversationContext, AgentConfig, ApprovalInterrupt, tuple[ApprovalDecision, ...]],
    Awaitable[AgentResult],
]
_CanContinueTaskCallable = Callable[[ConversationContext], bool]


class _TaskApplicationServiceProtocol(Protocol):
    """TaskAgentAdapter 依赖的任务应用服务结构协议。"""

    async def execute_task(
        self,
        task: Task,
        *,
        prepare: _PrepareExecuteTaskCallable,
        prepare_resume: _PrepareResumeTaskCallable,
        run_agent: _RunTaskAgentCallable,
        can_continue: _CanContinueTaskCallable,
    ) -> TaskResult:
        """执行任务。"""
        ...

    async def continue_task(
        self,
        request: TaskContinueRequest,
        *,
        prepare: _PrepareResumeTaskCallable,
        run_agent: _RunTaskAgentCallable,
        can_continue: _CanContinueTaskCallable,
    ) -> TaskResult:
        """继续任务。"""
        ...

    async def resume_approval(
        self,
        request: TaskApprovalResumeRequest,
        *,
        prepare: _PrepareResumeTaskCallable,
        resume_agent: _ResumeTaskAgentCallable,
        can_continue: _CanContinueTaskCallable,
    ) -> TaskResult:
        """恢复审批。"""
        ...


def _tool_call_digest_from_trace_detail(detail: str) -> str | None:
    """从任务轨迹详情中提取工具调用摘要。"""
    open_paren_index = detail.find("(")
    if open_paren_index <= 0 or not detail.endswith(")"):
        return None
    tool_name = detail[:open_paren_index]
    arguments = detail[open_paren_index + 1 : -1]
    return normalized_tool_call_digest(tool_name, arguments)


class TaskAgentAdapter:
    """面向任务的 Agent 适配器，实现 TaskAgentPort 协议。

    将 Task 转换为 ConversationContext + AgentConfig，委托现有 AgentPort 执行，
    将 AgentResult 转换为 TaskResult。复用已有的 Agent Loop 基础设施，
    不包含自身的推理→行动→观察循环逻辑。

    Attributes:
        _agent: AgentPort 实例，委托执行 Agent Loop
        _tool_registry: 工具注册表，获取工具 schema
        _model_registry: 模型注册中心，解析 ModelAccessPort
        _compaction: 上下文压缩端口，传递给 AgentConfig 使用
        _session_store: 会话上下文存储端口，加载/保存对话上下文
        _max_rounds: Agent Loop 最大迭代轮次
    """

    def __init__(
        self,
        agent: AgentPort,
        tool_registry: ToolRegistry,
        model_registry: ModelRegistryPort,
        compaction: ContextCompactionPort,
        session_store: SessionContextStorePort,
        prompt_registry: "PromptRegistryPort",
        approval_store: ApprovalStateStorePort | None = None,
        max_rounds: int = 10,
        segment_policy: SegmentExecutionPolicy | None = None,
        task_application_service: _TaskApplicationServiceProtocol | None = None,
        task_template_prompt_id: str | None = None,
    ) -> None:
        """初始化面向任务的 Agent 适配器。

        构造期调用 ``prompt_registry.get('task-template')`` 一次性获取
        ``LoadedPrompt``，缓存 ``prompt_id``；运行期不再查注册表。
        ``loaded.content`` 不用于运行期（保留纯函数 ``build_system_prompt``）。

        Args:
            agent: AgentPort 实例，用于委托执行 Agent Loop
            tool_registry: 工具注册表实例，用于获取工具 schema
            model_registry: 模型注册中心实例，用于根据模型名称解析 ModelAccessPort
            compaction: 上下文压缩端口实例，传递给 AgentConfig 使用
            session_store: 会话上下文存储端口实例，用于加载/保存对话上下文
            prompt_registry: Prompt 注册表端口实例，用于加载 task-template Prompt
            approval_store: 审批中断状态存储端口，用于恢复任务审批
            max_rounds: Agent Loop 最大迭代轮次，默认 10
        """

        self._agent = agent
        self._tool_registry = tool_registry
        self._model_registry = model_registry
        self._compaction = compaction
        self._session_store = session_store
        self._approval_store = approval_store
        self._max_rounds = max_rounds
        self._segment_policy = segment_policy or SegmentExecutionPolicy()
        self._task_application_service = task_application_service

        if task_template_prompt_id is None:
            loaded = prompt_registry.get("task-template")
            self._task_template_prompt_id = loaded.prompt_id
        else:
            self._task_template_prompt_id = task_template_prompt_id

    @staticmethod
    def build_system_prompt(task: Task) -> str:
        """根据 Task 构造系统提示词（纯函数）。

        将 Task 的结构化字段转换为清晰的系统提示词文本。
        相同的 Task 输入始终产生相同的输出（确定性）。

        生成规则：
        - goal 作为核心指令部分
        - input_data 非空时序列化为 JSON 嵌入 "Input Data" 段落
        - constraints 非空时作为编号列表嵌入 "Constraints" 段落
        - output_format 不为 None 时嵌入 "Expected Output Format" 段落

        Args:
            task: 任务值对象

        Returns:
            生成的系统提示词字符串
        """
        sections: list[str] = [task.goal]

        if task.input_data:
            input_json = json.dumps(task.input_data, ensure_ascii=False, indent=2)
            sections.append(f"## Input Data\n{input_json}")

        if task.constraints:
            constraint_lines = [f"{i}. {c}" for i, c in enumerate(task.constraints, 1)]
            sections.append("## Constraints\n" + "\n".join(constraint_lines))

        if task.output_format is not None:
            sections.append(f"## Expected Output Format\n{task.output_format}")

        return "\n\n".join(sections)

    def _extract_trace(
        self,
        messages: list[BaseMessage],
        start_index: int,
        *,
        event_timestamps: dict[int, int] | None = None,
    ) -> list[TraceEntry]:
        """从 ConversationContext 新增消息中提取执行轨迹。"""
        stamps = event_timestamps or {}
        trace: list[TraceEntry] = []
        step = 1

        for offset, msg in enumerate(messages[start_index:]):
            absolute_index = start_index + offset
            event_ts = stamps.get(absolute_index)
            if isinstance(msg, AssistantMessage) and msg.tool_calls:
                for tool_call in msg.tool_calls:
                    trace.append(
                        TraceEntry(
                            step=step,
                            action="tool_call",
                            detail=f"{tool_call.name}({tool_call.arguments})",
                            timestamp_ms=(
                                event_ts if event_ts is not None else int(time.time() * 1000)
                            ),
                        )
                    )
                    step += 1
            elif isinstance(msg, ToolMessage):
                trace.append(
                    TraceEntry(
                        step=step,
                        action="tool_result",
                        detail=msg.content,
                        timestamp_ms=(
                            event_ts if event_ts is not None else int(time.time() * 1000)
                        ),
                    )
                )
                step += 1

        return trace

    def extract_trace(
        self,
        messages: list[BaseMessage],
        start_index: int,
        event_timestamps: dict[int, int] | None = None,
    ) -> list[TraceEntry]:
        """从消息序列提取任务执行轨迹。"""
        return self._extract_trace(
            messages,
            start_index,
            event_timestamps=event_timestamps,
        )

    def _make_agent_config(
        self,
        *,
        system_prompt: str,
        tool_schemas: list[dict[str, Any]],
        model_name: str,
    ) -> AgentConfig:
        """构造任务 Agent 单段执行配置。"""
        return AgentConfig(
            system_prompt=system_prompt,
            tool_schemas=tool_schemas,
            model=model_name,
            max_rounds=self._max_rounds,
            prompt_id=self._task_template_prompt_id,
        )

    def _prepare_execute_task(
        self,
        task: Task,
        context: ConversationContext,
    ) -> _TaskRunPlan:
        """为任务首段准备 prompt、工具 schema 与 AgentConfig。"""
        if task.tool_names is not None:
            allowed_tool_names = sorted(task.tool_names)
            tool_schemas = self._tool_registry.get_schemas(tool_names=task.tool_names)
        else:
            allowed_tool_names = None
            tool_schemas = self._tool_registry.get_schemas()

        system_prompt = self.build_system_prompt(task)
        model_name = task.model or self._model_registry.get_default_model()
        return _TaskRunPlan(
            config=self._make_agent_config(
                system_prompt=system_prompt,
                tool_schemas=tool_schemas,
                model_name=model_name,
            ),
            system_prompt=system_prompt,
            allowed_tool_names=allowed_tool_names,
        )

    def _prepare_resume_task(
        self,
        session_id: str,
        context: ConversationContext,
        model: str | None,
    ) -> _TaskRunPlan:
        """为任务继续/审批恢复准备工具边界与 AgentConfig。"""
        system_message, tool_schemas = self._restore_task_resume_context(
            session_id=session_id,
            context=context,
        )
        model_name = model or self._model_registry.get_default_model()
        return _TaskRunPlan(
            config=self._make_agent_config(
                system_prompt=system_message.content,
                tool_schemas=tool_schemas,
                model_name=model_name,
            ),
            system_prompt=system_message.content,
            allowed_tool_names=system_message.metadata.get("task_allowed_tool_names"),
        )

    async def _run_task_agent(
        self,
        context: ConversationContext,
        config: AgentConfig,
    ) -> AgentResult:
        """执行任务 Agent 单段。"""
        model_name = config.model or self._model_registry.get_default_model()
        model_access = self._model_registry.get_adapter_for_model(model_name)
        return await self._agent.run(context, config, model_access)

    async def _resume_task_agent(
        self,
        context: ConversationContext,
        config: AgentConfig,
        interrupt: ApprovalInterrupt,
        decisions: tuple[ApprovalDecision, ...],
    ) -> AgentResult:
        """恢复任务 Agent 审批。"""
        model_name = config.model or self._model_registry.get_default_model()
        model_access = self._model_registry.get_adapter_for_model(model_name)
        return await self._agent.resume(
            context,
            config,
            model_access,
            interrupt,
            decisions,
        )

    def _can_continue_from_context(self, context: ConversationContext) -> bool:
        """判断上下文是否满足任务继续执行前置条件。"""
        messages = context.get_messages()
        if not messages or not isinstance(messages[-1], ToolMessage):
            return False

        system_message = next(
            (message for message in messages if isinstance(message, SystemMessage)),
            None,
        )
        if system_message is None:
            return False
        if "task_allowed_tool_names" not in system_message.metadata:
            return False

        try:
            self._tool_schemas_for_boundary(system_message.metadata["task_allowed_tool_names"])
        except ContinuationUnavailableError:
            return False
        return True

    @staticmethod
    def _schema_names(tool_schemas: list[dict[str, Any]]) -> frozenset[str]:
        """从工具 schema 中提取工具名称集合。"""
        return frozenset(
            schema["function"]["name"]
            for schema in tool_schemas
            if isinstance(schema.get("function"), dict)
            and isinstance(schema["function"].get("name"), str)
        )

    def _to_task_result(
        self,
        *,
        agent_result: AgentResult,
        trace: list[TraceEntry],
        context: ConversationContext,
    ) -> TaskResult:
        """把 AgentResult 翻译为任务结果值对象。"""
        return TaskResultMapper.to_task_result(
            agent_result=agent_result,
            trace=trace,
            context_can_continue=self._can_continue_from_context(context),
            prompt_id=self._task_template_prompt_id,
        )

    @staticmethod
    def _segment_risk_gate_required(
        *,
        context: ConversationContext,
        pre_message_count: int,
        approval_required: bool = False,
        approval_metadata: dict[str, Any] | None = None,
    ) -> tuple[bool, str | None]:
        """根据本段新增稳定 metadata 判断是否需要风险门禁。"""
        guardrail_reason: str | None = None
        for message in context.get_messages()[pre_message_count:]:
            if not isinstance(message, ToolMessage):
                continue
            metadata = message.metadata or {}
            if metadata.get("risk_gate_required") is True:
                return True, metadata.get("guardrail_reason")
            if guardrail_reason is None:
                reason = metadata.get("guardrail_reason")
                if isinstance(reason, str) and reason:
                    guardrail_reason = reason

        approval_data = approval_metadata or {}
        if approval_required and approval_data.get("source") == "guardrail":
            return True, approval_data.get("guardrail_reason") or guardrail_reason
        return False, guardrail_reason

    @staticmethod
    def _with_segment_risk_metadata(
        result: TaskResult,
        *,
        risk_gate_required: bool,
        guardrail_reason: str | None,
    ) -> TaskResult:
        """把单段 guardrail 风险门禁信息附加到任务结果元数据。"""
        return replace(
            result,
            segment_metadata=replace(
                result.segment_metadata,
                risk_gate_required=risk_gate_required,
                guardrail_reason=guardrail_reason,
            ),
        )

    def _tool_schemas_for_boundary(
        self,
        allowed_tool_names: object,
    ) -> list[dict[str, Any]]:
        """按持久化的工具访问边界重建工具 schema。"""
        if allowed_tool_names is None:
            return self._tool_registry.get_schemas()
        if not isinstance(allowed_tool_names, list):
            raise ContinuationUnavailableError(
                "",
                "缺少可继续的工具访问边界",
            )

        candidate_names = cast(list[object], allowed_tool_names)
        if not all(isinstance(name, str) for name in candidate_names):
            raise ContinuationUnavailableError(
                "",
                "缺少可继续的工具访问边界",
            )
        requested_names = frozenset(
            name for name in candidate_names if isinstance(name, str)
        )
        tool_schemas = self._tool_registry.get_schemas(tool_names=set(requested_names))
        if self._schema_names(tool_schemas) != requested_names:
            raise ContinuationUnavailableError(
                "",
                "缺少可继续的工具访问边界",
            )
        return tool_schemas

    def _restore_task_resume_context(
        self,
        *,
        session_id: str,
        context: ConversationContext,
    ) -> tuple[SystemMessage, list[dict[str, Any]]]:
        """校验并恢复任务继续/审批恢复所需的上下文与工具边界。"""
        messages = context.get_messages()
        if not messages:
            raise ContinuationUnavailableError(
                session_id,
                "缺少可继续的任务上下文",
            )

        system_message = next(
            (message for message in messages if isinstance(message, SystemMessage)),
            None,
        )
        if system_message is None:
            raise ContinuationUnavailableError(
                session_id,
                "缺少可继续的任务上下文",
            )
        if "task_allowed_tool_names" not in system_message.metadata:
            raise ContinuationUnavailableError(
                session_id,
                "缺少可继续的工具访问边界",
            )

        try:
            tool_schemas = self._tool_schemas_for_boundary(
                system_message.metadata["task_allowed_tool_names"]
            )
        except ContinuationUnavailableError as exc:
            raise ContinuationUnavailableError(session_id, exc.reason) from exc
        return system_message, tool_schemas

    async def _load_consumed_interrupt(
        self,
        request: TaskApprovalResumeRequest,
    ) -> ApprovalInterrupt:
        """加载、校验并原子消费任务审批中断状态。"""
        if self._approval_store is None:
            raise ApprovalNotFoundError(request.session_id, request.approval_id)

        interrupt = await self._approval_store.load(request.session_id, request.approval_id)
        if interrupt is None:
            raise ApprovalNotFoundError(request.session_id, request.approval_id)
        if interrupt.is_expired(time.time()):
            raise ApprovalExpiredError(request.session_id, request.approval_id)
        ApprovalResumePrecondition.check(interrupt.actions, request.decisions)

        consumed = await self._approval_store.consume(
            request.session_id,
            request.approval_id,
        )
        if consumed is None:
            raise ApprovalConsumedError(request.session_id, request.approval_id)
        return consumed

    async def _execute_single_task_segment(self, task: Task) -> TaskResult:
        """执行任务。

        完整流程：
        1. 根据 session_id 加载或创建 ConversationContext
        2. 调用 build_system_prompt 生成系统提示词，添加为系统消息
        3. 将 goal 添加为用户消息
        4. 从 ToolRegistry 获取工具 schema，构造 AgentConfig
        5. 通过 ModelRegistryPort 解析 ModelAccessPort
        6. 记录执行前消息数量
        7. 委托 AgentPort.run() 执行
        8. 从上下文新增消息提取执行轨迹
        9. 将 AgentResult 转换为 TaskResult(status=SUCCESS)
        10. 若有 session_id，保存更新后的上下文

        异常处理：捕获 AgentPort.run() 的所有异常，
        转换为 TaskResult(status=FAILED, content=str(e))。

        Args:
            task: 任务值对象

        Returns:
            TaskResult，包含执行结果、状态和执行轨迹
        """
        _otel_trace.get_current_span().set_attribute("prompt.id", self._task_template_prompt_id)

        try:
            # 1. 加载或创建 ConversationContext
            if task.session_id is not None:
                context = await self._session_store.load(task.session_id)
                context.session_id = task.session_id
            else:
                context = ConversationContext()

            if task.tool_names is not None:
                allowed_tool_names = sorted(task.tool_names)
                tool_schemas = self._tool_registry.get_schemas(tool_names=task.tool_names)
            else:
                allowed_tool_names = None
                tool_schemas = self._tool_registry.get_schemas()

            # 2. 构造系统提示词并幂等注入到上下文
            #    会话复用时上下文中可能已存在历史 SystemMessage，重复追加会
            #    导致 system 消息累积；此处仅在上下文中无任何 system 消息时
            #    追加，保持"每会话至多一条 SystemMessage"不变量。
            system_prompt = self.build_system_prompt(task)
            existing_system_messages = [m for m in context.get_messages() if m.role == "system"]
            if not existing_system_messages:
                context.append_message(
                    SystemMessage(
                        content=system_prompt,
                        metadata={"task_allowed_tool_names": allowed_tool_names},
                    )
                )
            else:
                system_message = existing_system_messages[0]
                if "task_allowed_tool_names" not in system_message.metadata:
                    system_message.metadata["task_allowed_tool_names"] = allowed_tool_names
                if system_message.content != system_prompt:
                    logger.info(
                        "复用既有 system 消息（与本次 build_system_prompt 不一致）",
                        extra={"prompt_id": self._task_template_prompt_id},
                    )

            # 3. 将 goal 添加为用户消息
            context.add_user_message(task.goal)

            model_name = task.model or self._model_registry.get_default_model()
            config = self._make_agent_config(
                system_prompt=system_prompt,
                tool_schemas=tool_schemas,
                model_name=model_name,
            )

            # 5. 解析 ModelAccessPort
            model_access = self._model_registry.get_adapter_for_model(model_name)

            # 6. 记录执行前消息数量
            pre_message_count = context.message_count

            # 7. 委托 AgentPort.run() 执行
            logger.info(
                "开始执行任务，目标: %s，模型: %s",
                task.goal,
                model_name,
                extra={"prompt_id": self._task_template_prompt_id},
            )
            agent_result = await self._agent.run(context, config, model_access)

            # 8. 提取执行轨迹（时间戳取 ConversationContext.event_timestamps 正式字段;
            #    v2 已升级为参与 to_dict / from_dict 的可选字段，无需 getattr 兜底）
            trace = self._extract_trace(
                context.get_messages(),
                pre_message_count,
                event_timestamps=context.event_timestamps,
            )

            # 9. 构造 TaskResult
            result = self._to_task_result(
                agent_result=agent_result,
                trace=trace,
                context=context,
            )
            risk_gate_required, guardrail_reason = self._segment_risk_gate_required(
                context=context,
                pre_message_count=pre_message_count,
                approval_required=agent_result.status == "approval_required",
                approval_metadata=(
                    agent_result.approval.metadata if agent_result.approval is not None else None
                ),
            )
            result = self._with_segment_risk_metadata(
                result,
                risk_gate_required=risk_gate_required,
                guardrail_reason=guardrail_reason,
            )

            # 10. 有 session_id 时保存上下文
            if task.session_id is not None:
                await self._session_store.save(task.session_id, context)

            logger.info(
                "任务执行成功，模型: %s",
                agent_result.model,
                extra={"prompt_id": self._task_template_prompt_id},
            )
            return result

        except Exception as e:
            logger.exception(
                "任务执行失败: %s",
                e,
                extra={"prompt_id": self._task_template_prompt_id},
            )
            model_name = task.model or "unknown"
            return TaskResult(
                content=str(e),
                status=TaskStatus.FAILED,
                model=model_name,
                prompt_id=self._task_template_prompt_id,
            )

    async def _run_segmented_task_result(
        self,
        first_result: TaskResult,
        *,
        continue_factory: Callable[[], Awaitable[TaskResult]],
        allow_auto_continue: bool,
    ) -> TaskResult:
        """对 TaskResult 单段结果执行分段续跑决策与累计。"""
        budget_usage = SegmentBudgetUsage()
        cumulative_usage: dict[str, int] = {}
        all_trace: list[TraceEntry] = []
        total_latency_ms = 0.0
        auto_continue_attempted = False
        result = first_result
        previous_tool_call_digest: str | None = None

        while True:
            token_delta = total_tokens_from_usage(result.usage)
            last_tool_call_detail = next(
                (entry.detail for entry in reversed(result.trace) if entry.action == "tool_call"),
                None,
            )
            last_tool_call_digest = (
                _tool_call_digest_from_trace_detail(last_tool_call_detail)
                if last_tool_call_detail is not None
                else None
            )
            repeated_tool_call = (
                last_tool_call_digest is not None
                and last_tool_call_digest == previous_tool_call_digest
            )
            if last_tool_call_digest is not None:
                previous_tool_call_digest = last_tool_call_digest
            progress = SegmentProgressSnapshot(
                pre_message_count=0,
                post_message_count=len(result.trace),
                new_trace_count=len(result.trace),
                token_delta=token_delta,
                final_content_present=bool(result.content),
                repeated_tool_call=repeated_tool_call,
            )
            cumulative_usage = merge_usage(cumulative_usage, result.usage)
            all_trace.extend(result.trace)
            total_latency_ms += result.latency_ms
            budget_usage = budget_usage.plus_segment(
                total_tokens_delta=token_delta,
                elapsed_ms_delta=result.latency_ms,
                paused=result.status == TaskStatus.PAUSED,
                has_progress=progress.has_progress,
                repeated_tool_call=progress.repeated_tool_call,
            )
            status = "completed"
            if result.status == TaskStatus.PAUSED:
                status = "paused"
            elif result.status == TaskStatus.HUMAN_INTERVENTION_REQUIRED:
                status = "approval_required"
            approval_required = result.status == TaskStatus.HUMAN_INTERVENTION_REQUIRED
            tool_boundary_available = result.can_continue or result.status != TaskStatus.PAUSED
            decision = decide_next_segment(
                policy=self._segment_policy,
                usage=budget_usage,
                status=status,
                can_continue=allow_auto_continue and result.status == TaskStatus.PAUSED,
                progress=progress,
                approval_required=approval_required,
                tool_boundary_available=tool_boundary_available,
                risk_gate_required=result.segment_metadata.risk_gate_required,
            )
            if decision.should_continue:
                auto_continue_attempted = True
                result = await continue_factory()
                continue

            metadata = SegmentRunMetadata(
                segment_index=budget_usage.segment_count,
                segment_count=budget_usage.segment_count,
                auto_continue_attempted=auto_continue_attempted,
                segment_stop_reason=decision.stop_reason,
                budget_usage=budget_usage,
                risk_gate_required=result.segment_metadata.risk_gate_required,
                guardrail_reason=result.segment_metadata.guardrail_reason,
            )
            return replace(
                result,
                usage=cumulative_usage,
                trace=all_trace,
                latency_ms=total_latency_ms,
                segment_metadata=metadata,
            )

    async def _run_segmented_task(self, task: Task) -> TaskResult:
        """执行任务首段及必要的自动续跑。"""
        first_result = await self._execute_single_task_segment(task)
        if task.session_id is None:
            return first_result

        async def continue_factory() -> TaskResult:
            return await self._continue_single_task_segment(
                TaskContinueRequest(session_id=task.session_id or "", model=task.model)
            )

        return await self._run_segmented_task_result(
            first_result,
            continue_factory=continue_factory,
            allow_auto_continue=True,
        )

    async def _continue_segmented_task(self, request: TaskContinueRequest) -> TaskResult:
        """继续任务首段及必要的自动续跑。"""
        first_result = await self._continue_single_task_segment(request)

        async def continue_factory() -> TaskResult:
            return await self._continue_single_task_segment(request)

        return await self._run_segmented_task_result(
            first_result,
            continue_factory=continue_factory,
            allow_auto_continue=True,
        )

    async def execute(self, task: Task) -> TaskResult:
        """执行任务，支持请求内分段自动续跑。"""
        if self._task_application_service is not None:
            _otel_trace.get_current_span().set_attribute(
                "prompt.id",
                self._task_template_prompt_id,
            )
            return await self._task_application_service.execute_task(
                task,
                prepare=self._prepare_execute_task,
                prepare_resume=self._prepare_resume_task,
                run_agent=self._run_task_agent,
                can_continue=self._can_continue_from_context,
            )
        return await self._run_segmented_task(task)

    async def continue_task(self, request: TaskContinueRequest) -> TaskResult:
        """基于已有任务会话上下文继续执行，支持分段自动续跑。"""
        if self._task_application_service is not None:
            return await self._task_application_service.continue_task(
                request,
                prepare=self._prepare_resume_task,
                run_agent=self._run_task_agent,
                can_continue=self._can_continue_from_context,
            )
        return await self._continue_segmented_task(request)

    async def _continue_single_task_segment(self, request: TaskContinueRequest) -> TaskResult:
        """基于已有任务会话上下文继续执行。"""
        context = await self._session_store.load(request.session_id)
        context.session_id = request.session_id
        messages = context.get_messages()
        if not messages:
            raise ContinuationUnavailableError(
                request.session_id,
                "缺少可继续的任务上下文",
            )
        if not isinstance(messages[-1], ToolMessage):
            raise ContinuationUnavailableError(
                request.session_id,
                "最新消息不是工具结果",
            )

        system_message, tool_schemas = self._restore_task_resume_context(
            session_id=request.session_id,
            context=context,
        )

        model_name = request.model or self._model_registry.get_default_model()
        model_access = self._model_registry.get_adapter_for_model(model_name)
        pre_message_count = context.message_count
        agent_result = await self._agent.run(
            context,
            self._make_agent_config(
                system_prompt=system_message.content,
                tool_schemas=tool_schemas,
                model_name=model_name,
            ),
            model_access,
        )
        trace = self._extract_trace(
            context.get_messages(),
            pre_message_count,
            event_timestamps=context.event_timestamps,
        )
        result = self._to_task_result(
            agent_result=agent_result,
            trace=trace,
            context=context,
        )
        risk_gate_required, guardrail_reason = self._segment_risk_gate_required(
            context=context,
            pre_message_count=pre_message_count,
            approval_required=agent_result.status == "approval_required",
            approval_metadata=(
                agent_result.approval.metadata if agent_result.approval is not None else None
            ),
        )
        result = self._with_segment_risk_metadata(
            result,
            risk_gate_required=risk_gate_required,
            guardrail_reason=guardrail_reason,
        )
        await self._session_store.save(request.session_id, context)
        return result

    async def resume_approval(self, request: TaskApprovalResumeRequest) -> TaskResult:
        """提交审批决策并恢复任务 Agent 执行。"""
        if self._task_application_service is not None:
            return await self._task_application_service.resume_approval(
                request,
                prepare=self._prepare_resume_task,
                resume_agent=self._resume_task_agent,
                can_continue=self._can_continue_from_context,
            )
        consumed = await self._load_consumed_interrupt(request)
        context = ConversationContext.from_dict(consumed.context_snapshot)
        context.session_id = request.session_id
        system_message, tool_schemas = self._restore_task_resume_context(
            session_id=request.session_id,
            context=context,
        )

        model_name = request.model or consumed.model
        model_access = self._model_registry.get_adapter_for_model(model_name)
        pre_message_count = context.message_count
        agent_result = await self._agent.resume(
            context,
            self._make_agent_config(
                system_prompt=system_message.content,
                tool_schemas=tool_schemas,
                model_name=model_name,
            ),
            model_access,
            consumed,
            request.decisions,
        )
        trace = self._extract_trace(
            context.get_messages(),
            pre_message_count,
            event_timestamps=context.event_timestamps,
        )
        result = self._to_task_result(
            agent_result=agent_result,
            trace=trace,
            context=context,
        )
        risk_gate_required, guardrail_reason = self._segment_risk_gate_required(
            context=context,
            pre_message_count=pre_message_count,
            approval_required=agent_result.status == "approval_required",
            approval_metadata=(
                agent_result.approval.metadata if agent_result.approval is not None else None
            ),
        )
        result = self._with_segment_risk_metadata(
            result,
            risk_gate_required=risk_gate_required,
            guardrail_reason=guardrail_reason,
        )
        if agent_result.status != "approval_required":
            await self._session_store.save(request.session_id, context)
        return result

    async def restore_checkpoint_context(
        self,
        session_id: str,
        context_snapshot: dict[str, Any],
    ) -> None:
        """把 checkpoint 快照恢复为该任务会话的当前上下文。"""

        context = ConversationContext.from_dict(context_snapshot)
        context.session_id = session_id
        await self._session_store.save(session_id, context)
