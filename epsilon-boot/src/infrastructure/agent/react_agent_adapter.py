"""ReAct 模式 Agent 适配器模块。

实现 AgentPort 协议，封装"推理→行动→观察"循环逻辑。
从 ChatServiceAdapter 中提取 Agent Loop 执行逻辑（_run_agent_loop 和
_run_agent_loop_streaming），使 Agent 执行与聊天编排解耦。

本模块属于基础设施层，持有 ToolRegistry 和 ContextBuilderPort 作为长期依赖，
在每次调用时通过 AgentConfig 接收运行时配置（工具 schema、模型、最大轮次等）。
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Mapping
from typing import Any, cast

from opentelemetry import trace as _otel_trace
from opentelemetry.trace import Status, StatusCode

from domain.agent.agent_loop_orchestration import AgentLoopOrchestrator
from domain.agent.agent_loop_policy import (
    RoundOutcome,
    classify_tool_execution,
    compute_total_tokens,
    interpret_tool_guardrail_decision,
    outcome_to_agent_result,
)
from domain.agent.exceptions import (
    ApprovalEditInvalidArgumentsError,
    HandoffPerformed,
    ToolPermissionDeniedError,
)
from domain.agent.guardrails import (
    GuardrailAction,
    GuardrailEvaluationContext,
    GuardrailEvaluationStage,
    GuardrailObservation,
    GuardrailRuntimeStats,
    ToolRiskLevel,
)
from domain.agent.ports import (
    AgentPort,
    ApprovalPolicyPort,
    ApprovalStateStorePort,
    ModelRoundResult,
    RunGuardrailRecorderPort,
)
from domain.agent.tools import ToolExecutionResult, ToolRegistry
from domain.agent.value_objects import (
    AgentConfig,
    AgentResult,
    AgentStreamEvent,
    ApprovalDecision,
    ApprovalInterrupt,
    ApprovalInterruptSummary,
    ApprovalPolicy,
    ApprovalRequiredPayload,
    PendingActionRequest,
)
from domain.chat.context import ConversationContext, ToolMessage
from domain.chat.ports import ContextBuilderPort
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import (
    ChatRequest,
    LLMResponse,
    StreamingChunk,
    ToolCallRequest,
)
from domain.run.checkpoint_context import get_run_checkpoint_context
from domain.run.ports import RunEventStorePort
from domain.run.runtime_context import get_run_execution_context
from domain.run.value_objects import (
    ToolExecutionKey,
    ToolLedgerStatus,
    ToolReplayPolicy,
    ToolResultLedgerEntry,
    ToolSideEffectLevel,
)
from domain.run.workflow import WorkflowCapabilityAction, WorkflowCapabilityDecision
from infrastructure.agent.approval_serialization import approval_payload_to_metadata
from infrastructure.agent.guardrail_runtime_accumulator import (
    CURRENT_GUARDRAIL_RUNTIME as _CURRENT_GUARDRAIL_RUNTIME,
)
from infrastructure.agent.guardrail_runtime_accumulator import (
    CURRENT_TOOL_ABUSE_DETECTOR as _CURRENT_TOOL_ABUSE_DETECTOR,
)
from infrastructure.agent.guardrail_runtime_accumulator import (
    GuardrailRuntimeAccumulator as _GuardrailRuntimeAccumulator,
)
from infrastructure.agent.guardrail_serialization import (
    guardrail_runtime_stats_to_dict,
)
from infrastructure.agent.react_approval_checkpoint import ApprovalCheckpointStitcher
from infrastructure.agent.react_approval_resume_coordinator import (
    ReactApprovalResumeCoordinator,
)
from infrastructure.agent.react_final_round_streamer import ReactFinalRoundStreamer
from infrastructure.agent.react_tool_execution_coordinator import ReactToolExecutionCoordinator
from infrastructure.agent.react_trace_recorder import ReActTraceRecorder
from infrastructure.agent.round_stream_accumulator import (
    RoundStreamAccumulator as _RoundStreamAccumulator,
)
from infrastructure.agent.tool_abuse_detector import ToolAbuseDetector, ToolAbuseVerdict
from infrastructure.agent.workflow_capability_runtime import (
    enforce_workflow_capability_before_action,
)
from infrastructure.chat.usage import merge_usage

logger = logging.getLogger(__name__)

tracer = _otel_trace.get_tracer(__name__)
"""模块级 OpenTelemetry tracer。

用于在 ``_iter_rounds`` 中为每一轮 ReAct 循环创建 ``react_agent.round`` 子
span，使整条调用链在 OTel 后端（Jaeger / Tempo / Langfuse 等）形成
"HTTP 请求 → ChatService → ReAct Agent Loop（每轮一个 span） → LLM HTTP 调用 /
工具调用"的天然嵌套结构。

OTel 未启用（``OTEL_ENABLED=false``）时，``trace.get_tracer`` 返回的是默认
``NoOpTracer``，``start_as_current_span`` 调用零开销且不影响功能行为；
本适配器**不**引入 ``if otel_config.enabled`` 分支。
"""


class _NoApprovalPolicyProvider(ApprovalPolicyPort):
    """默认关闭的审批策略提供器，用于保持旧构造兼容。"""

    def policy_for(self, tool_name: str) -> ApprovalPolicy:
        """所有工具均不审批。"""
        return ApprovalPolicy(
            tool_name=tool_name,
            interrupt=False,
            allowed_decisions=frozenset(),
        )


class _NoopApprovalStateStore(ApprovalStateStorePort):
    """默认空审批状态存储，用于保持旧构造兼容。"""

    async def save(self, interrupt: ApprovalInterrupt) -> None:
        """忽略保存。"""
        return None

    async def load(self, session_id: str, approval_id: str) -> ApprovalInterrupt | None:
        """始终返回 None。"""
        return None

    async def consume(self, session_id: str, approval_id: str) -> ApprovalInterrupt | None:
        """始终返回 None。"""
        return None

    async def delete(self, session_id: str, approval_id: str) -> None:
        """忽略删除。"""
        return None

    async def delete_session(self, session_id: str) -> None:
        """忽略会话删除。"""
        return None

    async def list_pending_by_session(
        self,
        session_id: str,
    ) -> list[ApprovalInterruptSummary]:
        """始终返回空审批摘要列表。"""
        return []


class _GuardrailApprovalRequired(Exception):
    """封装 guardrail 触发的审批中断载荷。"""

    def __init__(self, payload: ApprovalRequiredPayload) -> None:
        """保存 guardrail 触发的审批载荷。"""

        super().__init__(payload.approval_id)
        self.payload = payload


def _agent_name_from_arguments(arguments: str | None) -> str | None:
    """从工具参数 JSON 中提取单个 agent_name。"""

    raw = "{}" if arguments is None else str(arguments)
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    payload = cast(dict[str, object], parsed) if isinstance(parsed, dict) else {}
    value = payload.get("agent_name")
    return str(value).strip() if isinstance(value, str) and value.strip() else None


def _delegation_targets_from_arguments(arguments: str | None) -> tuple[str | None, ...]:
    """从单条或并行委派工具参数中提取 capability 判定目标集合。"""

    raw = "{}" if arguments is None else str(arguments)
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return (None,)
    if not isinstance(parsed, dict):
        return (None,)
    payload = cast(dict[str, object], parsed)
    direct = payload.get("agent_name")
    if isinstance(direct, str) and direct.strip():
        return (direct.strip(),)
    requests = payload.get("requests")
    if isinstance(requests, list):
        cleaned_items: list[str] = []
        for item in cast(list[object], requests):
            if not isinstance(item, dict):
                continue
            request = cast(dict[str, object], item)
            agent_name = request.get("agent_name")
            if isinstance(agent_name, str) and agent_name.strip():
                cleaned_items.append(agent_name.strip())
        cleaned = tuple(cleaned_items)
        return cleaned or (None,)
    return (None,)


def _workflow_capability_checks_for_tool_call(
    tool_call: ToolCallRequest,
) -> tuple[tuple[WorkflowCapabilityAction, str | None], ...]:
    """把 ReAct 工具调用转换为一个或多个 role capability 检查项。"""

    if tool_call.name in {"delegate_to_agent", "delegate_parallel"}:
        return tuple(
            (WorkflowCapabilityAction.DELEGATION, target)
            for target in _delegation_targets_from_arguments(tool_call.arguments)
        )
    if tool_call.name == "handoff_to_agent":
        return (
            (WorkflowCapabilityAction.HANDOFF, _agent_name_from_arguments(tool_call.arguments)),
        )
    return ((WorkflowCapabilityAction.TOOL, tool_call.name),)


def _tool_arguments_mapping(arguments: str | None) -> dict[str, Any]:
    """把模型工具参数转换为滥用检测器可接收的字典。"""

    raw = "" if arguments is None else str(arguments)
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else {}


class ReActAgentAdapter(AgentPort):
    """ReAct 模式 Agent 适配器，实现 AgentPort 协议。

    封装"推理→行动→观察"循环逻辑，从 ChatServiceAdapter 中提取。
    持有 ToolRegistry 和 ContextBuilderPort 作为长期依赖，
    在每次调用时通过 AgentConfig 接收运行时配置。

    Attributes:
        _tool_registry: 工具注册表，管理已注册的 Tool 实例，用于执行工具调用
        _context_builder: 上下文构建端口，用于在调用模型前构建序列化消息
    """

    def __init__(
        self,
        tool_registry: ToolRegistry,
        context_builder: ContextBuilderPort,
        approval_policy: ApprovalPolicyPort | None = None,
        approval_store: ApprovalStateStorePort | None = None,
        trace_store: Any | None = None,
        guardrail_policy: Any | None = None,
        run_guardrail_recorder: RunGuardrailRecorderPort | None = None,
        run_event_store: RunEventStorePort | None = None,
    ) -> None:
        """初始化 ReAct Agent 适配器。

        Args:
            tool_registry: 工具注册表实例，管理已注册的 Tool，用于 Agent Loop 中执行工具调用
            context_builder: 上下文构建端口实例，用于在调用模型前构建序列化消息
            approval_policy: 审批策略端口；未提供时默认关闭审批
            approval_store: 审批状态存储端口；未提供时使用 no-op 实现
            trace_store: 结构化追踪存储端口；未提供时追踪静默跳过
            run_guardrail_recorder: Run 级 guardrail 观测记录端口；未提供时跳过 Run 收敛写入
            run_event_store: Run 事件端口；用于 workflow role capability 拒绝事件写入
        """
        self._tool_registry = tool_registry
        self._context_builder = context_builder
        self._approval_policy = approval_policy or _NoApprovalPolicyProvider()
        self._approval_store = approval_store or _NoopApprovalStateStore()
        self._approval_stitcher = ApprovalCheckpointStitcher(
            self._approval_policy, self._approval_store
        )
        self._trace_store = trace_store
        self._trace_recorder = ReActTraceRecorder(trace_store)
        self._tool_execution_coordinator = ReactToolExecutionCoordinator(self)
        self._approval_resume_coordinator = ReactApprovalResumeCoordinator(self)
        self._final_round_streamer = ReactFinalRoundStreamer(
            context_builder=self._context_builder,
            merge_usage=merge_usage,
        )
        self._guardrail_policy = guardrail_policy
        self._run_guardrail_recorder = run_guardrail_recorder
        self._run_event_store = run_event_store
        self._orchestrator = AgentLoopOrchestrator()

    def _guardrail_runtime_accumulator(self) -> _GuardrailRuntimeAccumulator:
        """返回当前执行链路的 guardrail 统计累计器，必要时从 Run 快照恢复。"""

        run_context = get_run_execution_context()
        context_key = (
            run_context.run_id if run_context is not None else None,
            run_context.owner_id if run_context is not None else None,
            run_context.segment_index if run_context is not None else None,
        )
        accumulator = _CURRENT_GUARDRAIL_RUNTIME.get()
        if accumulator is not None and accumulator.context_key == context_key:
            return accumulator
        accumulator = _GuardrailRuntimeAccumulator.from_summary(
            run_context.guardrail_summary if run_context is not None else None,
            context_key=context_key,
        )
        _CURRENT_GUARDRAIL_RUNTIME.set(accumulator)
        return accumulator

    def _tool_abuse_detector(self) -> ToolAbuseDetector:
        """返回当前执行链路的工具滥用检测器。"""

        detector = _CURRENT_TOOL_ABUSE_DETECTOR.get()
        if detector is None:
            detector = ToolAbuseDetector()
            _CURRENT_TOOL_ABUSE_DETECTOR.set(detector)
        return detector

    def _guardrail_model_pricing(self) -> Mapping[str, Any]:
        """从当前 guardrail policy 读取模型价格表。"""

        policy = getattr(self._guardrail_policy, "policy", None)
        pricing = getattr(policy, "model_pricing", None)
        return cast(Mapping[str, Any], pricing) if isinstance(pricing, Mapping) else {}

    def _tool_risk_level(self, tool_name: str) -> ToolRiskLevel:
        """解析工具注册表中的风险等级，缺失时按高风险保守处理。"""

        tool = self._get_registered_tool(tool_name)
        if tool is None:
            return ToolRiskLevel.HIGH
        raw_risk = self._tool_attr_value(tool, "risk_level", ToolRiskLevel.HIGH)
        if isinstance(raw_risk, ToolRiskLevel):
            return raw_risk
        try:
            return ToolRiskLevel(str(raw_risk))
        except ValueError:
            return ToolRiskLevel.HIGH

    async def _record_trace(self, session_id: str | None, step: Any) -> None:
        """记录一步结构化追踪（委托 ``ReActTraceRecorder``）。"""
        await self._trace_recorder.record_step(session_id, step)

    @staticmethod
    def _truncate(text: str, max_len: int) -> str:
        """截断文本到指定长度（委托 ``ReActTraceRecorder``）。"""
        return ReActTraceRecorder.truncate(text, max_len)

    def _build_model_call_trace(self, outcome: RoundOutcome, config: AgentConfig) -> Any:
        """从 RoundOutcome 构建 ModelCallTrace（委托 ``ReActTraceRecorder``）。"""
        return self._trace_recorder.build_model_call_trace(outcome, config)

    def _build_model_call_trace_from_response(
        self,
        round_num: int,
        response: LLMResponse,
        config: AgentConfig,
    ) -> Any:
        """从单轮 ``LLMResponse`` 构建 ``ModelCallTrace``（委托 ``ReActTraceRecorder``）。"""
        return self._trace_recorder.build_model_call_trace_from_response(
            round_num, response, config
        )

    def _build_approval_trace(self, outcome: RoundOutcome) -> Any:
        """从 RoundOutcome 构建 ApprovalTrace（委托 ``ReActTraceRecorder``）。"""
        return self._trace_recorder.build_approval_trace(outcome)

    async def _record_error_trace(
        self,
        session_id: str | None,
        round_num: int,
        exc: BaseException,
    ) -> None:
        """记录 Agent Loop 级别非工具异常为 ``ErrorTrace``（委托 ``ReActTraceRecorder``）。"""
        await self._trace_recorder.record_error_trace(session_id, round_num, exc)

    async def _record_tool_call_trace(
        self,
        session_id: str | None,
        round_num: int,
        tool_call: ToolCallRequest,
        result: ToolExecutionResult,
        is_error: bool,
        elapsed_ms: float,
    ) -> None:
        """记录单个工具调用追踪（委托 ``ReActTraceRecorder``）。"""
        await self._trace_recorder.record_tool_call_trace(
            session_id, round_num, tool_call, result, is_error, elapsed_ms
        )

    async def record_tool_call_trace(
        self,
        session_id: str | None,
        round_num: int,
        tool_call: ToolCallRequest,
        result: ToolExecutionResult,
        is_error: bool,
        elapsed_ms: float,
    ) -> None:
        """为并发执行协作者公开工具调用追踪入口。"""

        await self._record_tool_call_trace(
            session_id,
            round_num,
            tool_call,
            result,
            is_error,
            elapsed_ms,
        )

    @staticmethod
    def _truncate_metadata(
        metadata: dict[str, Any],
        max_total_bytes: int = 2048,
    ) -> dict[str, Any]:
        """截断 metadata dict 的总序列化大小（委托 ``ReActTraceRecorder``）。"""
        return ReActTraceRecorder.truncate_metadata(metadata, max_total_bytes)

    def _ensure_tool_authorized(self, tool_call: ToolCallRequest, config: AgentConfig) -> None:
        """先于审批执行工具授权校验。"""
        if tool_call.name not in config.allowed_tool_names:
            raise ToolPermissionDeniedError(
                tool_name=tool_call.name,
                allowed_tools=config.allowed_tool_names,
            )

    def _record_tool_call_for_abuse_detection(
        self,
        tool_call: ToolCallRequest,
    ) -> ToolAbuseVerdict:
        """记录工具调用意图并返回滥用检测结果。"""

        return self._tool_abuse_detector().record_tool_call(
            tool_call.name,
            _tool_arguments_mapping(tool_call.arguments),
        )

    def _emit_tool_abuse_detected(
        self,
        tool_call: ToolCallRequest,
        verdict: ToolAbuseVerdict,
    ) -> None:
        """写入工具滥用检测的 OpenTelemetry event 和结构化日志字段。"""

        reason = verdict.reason or "unknown_tool_abuse"
        current_span = _otel_trace.get_current_span()
        current_span.add_event(
            "agent.tool_abuse_detected",
            {
                "tool_name": tool_call.name,
                "reason": reason,
            },
        )
        logger.warning(
            "Agent 工具调用滥用检测命中",
            extra={
                "tool_name": tool_call.name,
                "reason": reason,
            },
        )

    def _record_tool_abuse_blocked_result(
        self,
        context: ConversationContext,
        tool_call: ToolCallRequest,
        verdict: ToolAbuseVerdict,
    ) -> tuple[ToolExecutionResult, bool]:
        """按既有 guardrail 阻断路径回写工具滥用检测结果。"""

        reason = verdict.reason or "unknown_tool_abuse"
        result = "工具调用被滥用检测阻断。"
        msg_index = context.add_tool_result(
            tool_name=tool_call.name,
            result=result,
            tool_call_id=tool_call.id,
        )
        msg = context.get_messages()[msg_index]
        assert isinstance(msg, ToolMessage)
        msg.metadata.update(
            {
                "error": True,
                "guardrail_blocked": True,
                "guardrail_action": GuardrailAction.STOP.value,
                "guardrail_reason": reason,
                "risk_gate_required": True,
                "tool_abuse_detected": True,
                "tool_abuse_reason": reason,
            }
        )
        self._stamp_event(context, msg_index)
        return ToolExecutionResult(content=result), True

    def _collect_pending_actions(
        self,
        tool_calls: list[ToolCallRequest],
        config: AgentConfig,
    ) -> tuple[PendingActionRequest, ...]:
        """按模型 tool_calls 顺序收集需要审批的动作（委托 ``ApprovalCheckpointStitcher``）。"""
        return self._approval_stitcher.collect_pending_actions(tool_calls, config)

    async def _save_interrupt(
        self,
        context: ConversationContext,
        config: AgentConfig,
        actions: tuple[PendingActionRequest, ...],
        round_num: int,
        model: str,
        usage_so_far: dict[str, int],
        metadata: dict[str, Any] | None = None,
    ) -> ApprovalRequiredPayload:
        """创建并保存审批中断（委托 ``ApprovalCheckpointStitcher``）。"""
        return await self._approval_stitcher.save_interrupt(
            context, config, actions, round_num, model, usage_so_far, metadata
        )

    async def _first_workflow_capability_denial(
        self,
        tool_call: ToolCallRequest,
    ) -> WorkflowCapabilityDecision | None:
        """返回工具调用映射出的首个 workflow capability 拒绝结果。"""

        for action, target in _workflow_capability_checks_for_tool_call(tool_call):
            decision = await enforce_workflow_capability_before_action(
                event_store=self._run_event_store,
                action=action,
                target=target,
            )
            if decision is not None:
                return decision
        return None

    async def _save_workflow_capability_interrupt(
        self,
        *,
        context: ConversationContext,
        config: AgentConfig,
        tool_call: ToolCallRequest,
        round_num: int,
        model: str,
        usage_so_far: dict[str, int],
        decision: WorkflowCapabilityDecision,
    ) -> ApprovalRequiredPayload:
        """把 workflow role capability 拒绝转换为既有 HITL 审批中断。"""

        action = PendingActionRequest(
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            arguments=tool_call.arguments,
            allowed_decisions=frozenset({"approve", "reject"}),
            reason=decision.reason or "role_capability_rejected",
        )
        run_context = get_run_execution_context()
        return await self._save_interrupt(
            context,
            config,
            (action,),
            round_num,
            model,
            dict(usage_so_far),
            metadata={
                "source": "workflow_role_capability",
                "guardrail_action": "require_approval",
                "guardrail_reason": "role_capability_rejected",
                "risk_gate_required": True,
                "workflow_capability_action": decision.action.value,
                "workflow_capability_reason": decision.reason,
                "workflow_capability_role": decision.role,
                "workflow_capability_target": decision.target,
                "run_id": run_context.run_id if run_context is not None else None,
                "tool_call_ids": [tool_call.id],
            },
        )

    @staticmethod
    def _log_token_budget_exceeded(
        round_num: int, total_usage: dict[str, int], config: AgentConfig
    ) -> None:
        """输出 ``Token_Budget_Exceeded_Warning`` 日志。

        与 ``Max_Rounds_Termination_Warning`` 在同一执行内**互斥**——预算
        超限分支会在产出 ``token_budget_exceeded`` final 前 return，循环
        耗尽分支不会被达到。
        """
        logger.warning(
            "Agent Loop 累计 token 超过 max_total_tokens 预算",
            extra={
                "round_num": round_num,
                "accumulated_total_tokens": compute_total_tokens(total_usage),
                "max_total_tokens": config.max_total_tokens,
            },
        )

    def _resolve_tool_timeout(
        self,
        tool_name: str,
        config: AgentConfig,
    ) -> float | None:
        """解析工具执行超时（秒）。

        优先级：``Tool.timeout_seconds`` > ``AgentConfig.tool_timeout_seconds``
        > ``None``（不超时）。如果底层注册表（如 ``ScopedToolRegistry``）
        不暴露 ``get(name)`` 接口，则跳过 per-tool override，回退到全局值。
        """
        get_fn = getattr(self._tool_registry, "get", None)
        if get_fn is not None:
            try:
                tool = get_fn(tool_name)
            except Exception:
                tool = None
            if tool is not None:
                tool_level = getattr(tool, "timeout_seconds", None)
                # 仅当工具显式声明数值型 ``timeout_seconds`` 时才覆盖全局值；
                # MagicMock 等返回非数值类型时跳过，回退到全局默认。
                if isinstance(tool_level, (int, float)) and not isinstance(tool_level, bool):
                    return float(tool_level)
        return config.tool_timeout_seconds

    async def _execute_tool_call(
        self,
        context: ConversationContext,
        tool_call: ToolCallRequest,
        config: AgentConfig,
        round_num: int = 0,
        usage: dict[str, int] | None = None,
        skip_guardrail_before: bool = False,
        record_guardrail_after: bool = True,
    ) -> tuple[ToolExecutionResult, bool]:
        """执行单个工具调用并追加 ``ToolMessage``，返回 ``(result, is_error)``。

        工具异常（含 ``ToolPermissionDeniedError`` 与运行期异常）按现状作为
        ``ToolMessage`` 内容回灌给 LLM，让模型据此自我纠正；同时通过
        ``_log_tool_failure`` 输出 warning 级日志，确保线上工具失败可观测。
        日志只记录工具名、tool_call_id、异常类名与摘要，不记录工具入参完整
        文本，避免泄露密钥或大文本。

        v2 变更：返回类型由 ``str`` 升级为 ``tuple[str, bool]`` (``Unified_Tool_Execution_Pipeline``
        b 路线)。在工具失败时把 ``ToolMessage.metadata`` 的 ``error`` 键设为
        ``True``，使事件流（``run_events``）与 LLM 上下文（``ToolMessage.to_dict()``
        输出）都能识别失败状态。成功时**不**写入 ``error`` 键，``ToolMessage.metadata``
        保持空 dict，``to_dict()`` 输出沿用既有 "非空 metadata 才输出" 语义,
        成功消息的序列化形态不含 ``metadata`` 键。

        Spec A 扩展：捕获 ``HandoffPerformed`` 信号——这是 ``HandoffToAgentTool``
        在目标 Agent 自然终止后抛出的"成功信号"，**不**视为错误：

        - ``ToolMessage.content`` 取 ``signal.content``（目标 Agent 最终回复）；
        - ``ToolMessage.metadata["handoff_target"]`` 写入目标 Agent 名称，
          供 ``_iter_rounds`` 在下一轮入口检测后短路终止当前 Agent Loop；
        - ``ToolMessage.metadata["error"]`` **不**写入；
        - ``is_error`` 返回 ``False``。

        Args:
            context: 对话上下文，原地修改。
            tool_call: 待执行的工具调用请求。
            config: Agent 执行配置。

        Returns:
            ``(result, is_error)``：

            - ``result``：工具执行结果值对象 ``ToolExecutionResult``；成功时为工具
              实际返回的结构化结果（``content`` 为回灌文本、``metadata`` 为结构化
              元数据），失败时为 ``ToolExecutionResult(content=str(exc),
              metadata={"error_class": ...})``。``result.content`` 同时被回灌为
              ``ToolMessage.content``（保持 LLM 上下文内容与重构前等价）。
            - ``is_error``：当且仅当工具执行抛出异常（含
              ``ToolPermissionDeniedError`` 与运行期 ``Exception``）时为 ``True``。
              ``HandoffPerformed`` 不视为错误。
        """
        is_error = False
        handoff_target: str | None = None
        tool_start_time = time.time()
        risk_level = self._tool_risk_level(tool_call.name)
        if not skip_guardrail_before:
            capability_decision = await self._first_workflow_capability_denial(tool_call)
            if capability_decision is not None:
                approval_payload = await self._save_workflow_capability_interrupt(
                    context=context,
                    config=config,
                    tool_call=tool_call,
                    round_num=round_num,
                    model=config.model or "default",
                    usage_so_far=dict(usage or {}),
                    decision=capability_decision,
                )
                raise _GuardrailApprovalRequired(approval_payload)
        checkpoint_context = get_run_checkpoint_context()
        tool_execution_key: str | None = None
        if checkpoint_context is not None:
            prepared_key = self._guardrail_runtime_accumulator().prepared_checkpoint_key(
                tool_call=tool_call
            )
            if prepared_key is not None:
                tool_execution_key = prepared_key
            else:
                replay_entry, tool_execution_key = await self._checkpoint_before_tool_call(
                    tool_call=tool_call,
                    round_num=round_num,
                    checkpoint_context=checkpoint_context,
                )
                if replay_entry is not None:
                    msg_index = context.add_tool_result(
                        tool_name=tool_call.name,
                        result=replay_entry.result or "",
                        tool_call_id=tool_call.id,
                    )
                    self._stamp_event(context, msg_index)
                    return (
                        ToolExecutionResult(content=replay_entry.result or ""),
                        bool(replay_entry.is_error),
                    )
        if not skip_guardrail_before:
            abuse_verdict = self._record_tool_call_for_abuse_detection(tool_call)
            if abuse_verdict.abuse_detected:
                self._emit_tool_abuse_detected(tool_call, abuse_verdict)
                return self._record_tool_abuse_blocked_result(
                    context,
                    tool_call,
                    abuse_verdict,
                )
        accumulator = self._guardrail_runtime_accumulator()
        if skip_guardrail_before:
            base_stats = (
                accumulator.prepared_tool_before(tool_call=tool_call) or accumulator.snapshot()
            )
            guardrail_decision = None
        else:
            base_stats = accumulator.tool_before(
                tool_call=tool_call,
                risk_level=risk_level,
            )
            accumulator.remember_tool_before(tool_call=tool_call, stats=base_stats)
            guardrail_decision = self._evaluate_tool_guardrail(tool_call, stats=base_stats)
            if (
                guardrail_decision is not None
                and guardrail_decision.action is not GuardrailAction.REQUIRE_APPROVAL
            ):
                await self._record_guardrail_observation(
                    stage=GuardrailEvaluationStage.TOOL_BEFORE_EXECUTION,
                    decision=guardrail_decision,
                    stats=base_stats,
                    round_num=round_num,
                    tool_call=tool_call,
                )
        guardrail_branch = interpret_tool_guardrail_decision(guardrail_decision)
        if guardrail_branch == "require_approval":
            action = PendingActionRequest(
                tool_call_id=tool_call.id,
                tool_name=tool_call.name,
                arguments=tool_call.arguments,
                allowed_decisions=frozenset({"approve", "edit", "reject"}),
                reason=guardrail_decision.message,  # type: ignore[union-attr]
            )
            run_context = get_run_execution_context()
            approval_payload = await self._save_interrupt(
                context,
                config,
                (action,),
                round_num,
                config.model or "default",
                dict(usage or {}),
                metadata=self._guardrail_metadata(
                    guardrail_decision,
                    tool_call=tool_call,
                    blocked=True,
                    risk_gate_required=True,
                )
                | {
                    "source": "guardrail",
                    "guardrail_message": guardrail_decision.message,  # type: ignore[union-attr]
                    "tool_call_ids": [tool_call.id],
                    "run_id": run_context.run_id if run_context is not None else None,
                },
            )
            await self._record_guardrail_observation(
                stage=GuardrailEvaluationStage.TOOL_BEFORE_EXECUTION,
                decision=guardrail_decision,
                stats=base_stats,
                round_num=round_num,
                tool_call=tool_call,
                approval_id=approval_payload.approval_id,
            )
            raise _GuardrailApprovalRequired(approval_payload)
        if guardrail_branch == "stop":
            blocked_message = guardrail_decision.message or "工具调用被护栏阻断。"  # type: ignore[union-attr]
            msg_index = context.add_tool_result(
                tool_name=tool_call.name,
                result=blocked_message,
                tool_call_id=tool_call.id,
            )
            msg = context.get_messages()[msg_index]
            assert isinstance(msg, ToolMessage)
            msg.metadata.update(
                self._guardrail_metadata(
                    guardrail_decision,
                    tool_call=tool_call,
                    blocked=True,
                    risk_gate_required=True,
                )
            )
            msg.metadata["error"] = True
            self._stamp_event(context, msg_index)
            return ToolExecutionResult(content=blocked_message), True

        timeout = self._resolve_tool_timeout(tool_call.name, config)
        result: ToolExecutionResult
        try:
            self._ensure_tool_authorized(tool_call, config)
            if timeout is None:
                result = await self._tool_registry.execute(tool_call)
            else:
                result = await asyncio.wait_for(
                    self._tool_registry.execute(tool_call), timeout=timeout
                )
        except HandoffPerformed as signal:
            # 控制转移成功信号：委托领域层分类
            classification = classify_tool_execution(
                signal, handoff_signal=signal, timeout=timeout
            )
            result = ToolExecutionResult(content=classification.content)
            handoff_target = classification.handoff_target
        except _GuardrailApprovalRequired:
            raise
        except ToolPermissionDeniedError as exc:
            classification = classify_tool_execution(
                exc, handoff_signal=None, timeout=timeout
            )
            self._log_tool_failure(tool_call, exc, "permission_denied")
            result = ToolExecutionResult(
                content=classification.content,
                metadata={"error_class": classification.error_class},
            )
            is_error = True
        except TimeoutError as exc:
            classification = classify_tool_execution(
                exc, handoff_signal=None, timeout=timeout
            )
            self._log_tool_failure(tool_call, exc, "timeout")
            result = ToolExecutionResult(
                content=classification.content,
                metadata={"error_class": classification.error_class},
            )
            is_error = True
        except Exception as exc:
            classification = classify_tool_execution(
                exc, handoff_signal=None, timeout=timeout
            )
            self._log_tool_failure(tool_call, exc, "execution_error")
            result = ToolExecutionResult(
                content=classification.content,
                metadata={"error_class": classification.error_class},
            )
            is_error = True
        msg_index = context.add_tool_result(
            tool_name=tool_call.name,
            result=result.content,
            tool_call_id=tool_call.id,
        )
        msg = context.get_messages()[msg_index]
        assert isinstance(msg, ToolMessage)
        if is_error or handoff_target is not None:
            # 写入 metadata。get_messages() 返回的 list 是 _messages 的浅拷贝,
            # 元素本体仍是同一引用,因此对 msg.metadata 的就地写入会反映到
            # _messages 中存储的同一 ToolMessage 实例(等价于直接索引 _messages,
            # 但走的是公开访问器,不破坏封装)。
            if is_error:
                msg.metadata["error"] = True
            if handoff_target is not None:
                msg.metadata["handoff_target"] = handoff_target
        self._stamp_event(context, msg_index)
        if checkpoint_context is not None and tool_execution_key is not None:
            await checkpoint_context.sink.after_tool_call(
                context=context,
                tool_execution_key=tool_execution_key,
                result=result.content,
                is_error=is_error,
                metadata={
                    "tool_name": tool_call.name,
                    "tool_call_id": tool_call.id,
                    "segment_index": checkpoint_context.segment_index,
                },
                round_num=round_num,
                usage=dict(usage or {}),
            )
        if record_guardrail_after:
            await self._record_tool_after_observation(
                tool_call=tool_call,
                usage=usage,
                round_num=round_num,
                is_error=is_error,
                elapsed_ms=(time.time() - tool_start_time) * 1000,
            )
        return result, is_error

    async def execute_tool_call_for_concurrency(
        self,
        context: ConversationContext,
        tool_call: ToolCallRequest,
        config: AgentConfig,
        *,
        round_num: int,
        skip_guardrail_before: bool,
        record_guardrail_after: bool,
    ) -> tuple[ToolExecutionResult, bool]:
        """为并发执行协作者公开单工具执行入口。"""

        return await self._execute_tool_call(
            context,
            tool_call,
            config,
            round_num=round_num,
            skip_guardrail_before=skip_guardrail_before,
            record_guardrail_after=record_guardrail_after,
        )

    def _evaluate_tool_guardrail(
        self,
        tool_call: ToolCallRequest,
        *,
        stats: GuardrailRuntimeStats | None = None,
    ) -> Any | None:
        """在工具执行前运行 guardrail 策略；未配置时保持旧行为。"""

        if self._guardrail_policy is None:
            return None
        risk_level = self._tool_risk_level(tool_call.name)
        evaluate = getattr(self._guardrail_policy, "evaluate_tool_before_execution", None)
        if not callable(evaluate):
            return None
        runtime_stats = stats or GuardrailRuntimeStats(
            last_tool_name=tool_call.name,
            last_tool_risk_level=risk_level.value,
        )
        return evaluate(
            GuardrailEvaluationContext(
                tool_name=tool_call.name,
                tool_risk_level=risk_level,
                total_tokens=runtime_stats.total_tokens,
                elapsed_ms=runtime_stats.elapsed_ms,
                context_growth_messages=runtime_stats.context_growth_messages,
                repeated_tool_call_count=runtime_stats.repeated_tool_call_count,
                consecutive_failure_count=runtime_stats.consecutive_failure_count,
                metadata=guardrail_runtime_stats_to_dict(runtime_stats),
            )
        )

    async def _record_guardrail_observation(
        self,
        *,
        stage: GuardrailEvaluationStage,
        decision: Any,
        stats: GuardrailRuntimeStats,
        round_num: int,
        tool_call: ToolCallRequest | None = None,
        approval_id: str | None = None,
    ) -> None:
        """把一次 guardrail 观测写入 Run recorder。"""

        if self._run_guardrail_recorder is None:
            return
        if decision is None or not hasattr(decision, "action"):
            return
        run_context = get_run_execution_context()
        if run_context is None:
            return

        tool_risk_level: ToolRiskLevel | None = None
        tool_name = tool_call.name if tool_call is not None else stats.last_tool_name
        tool_call_id = tool_call.id if tool_call is not None else None
        if tool_name is not None:
            try:
                tool_risk_level = self._tool_risk_level(tool_name)
            except ValueError:
                if stats.last_tool_risk_level is not None:
                    tool_risk_level = ToolRiskLevel(str(stats.last_tool_risk_level))

        observation = GuardrailObservation(
            stage=stage,
            decision=decision,
            stats=stats,
            segment_index=run_context.segment_index,
            round_num=round_num,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tool_risk_level=tool_risk_level,
            approval_id=approval_id,
        )
        await self._run_guardrail_recorder.record_observation(observation=observation)

    async def _record_tool_after_observation(
        self,
        *,
        tool_call: ToolCallRequest,
        usage: dict[str, int] | None,
        round_num: int,
        is_error: bool,
        elapsed_ms: float = 0.0,
    ) -> None:
        """按工具原始顺序提交工具执行后的 guardrail 观测。"""

        after_stats = self._guardrail_runtime_accumulator().tool_after(
            tool_call=tool_call,
            risk_level=self._tool_risk_level(tool_call.name),
            elapsed_ms=elapsed_ms,
            is_error=is_error,
        )
        if self._guardrail_policy is None:
            return
        evaluate_after = getattr(self._guardrail_policy, "evaluate_tool_after_execution", None)
        if not callable(evaluate_after):
            return
        risk_level = self._tool_risk_level(tool_call.name)
        decision = evaluate_after(
            GuardrailEvaluationContext(
                tool_name=tool_call.name,
                tool_risk_level=risk_level,
                total_tokens=after_stats.total_tokens,
                elapsed_ms=after_stats.elapsed_ms,
                context_growth_messages=after_stats.context_growth_messages,
                repeated_tool_call_count=after_stats.repeated_tool_call_count,
                consecutive_failure_count=after_stats.consecutive_failure_count,
                metadata=guardrail_runtime_stats_to_dict(after_stats),
            )
        )
        await self._record_guardrail_observation(
            stage=GuardrailEvaluationStage.TOOL_AFTER_EXECUTION,
            decision=decision,
            stats=after_stats,
            round_num=round_num,
            tool_call=tool_call,
        )

    async def record_tool_after_observation(
        self,
        *,
        tool_call: ToolCallRequest,
        usage: dict[str, int] | None,
        round_num: int,
        is_error: bool,
        elapsed_ms: float,
    ) -> None:
        """为并发执行协作者公开工具执行后 guardrail 观测入口。"""

        await self._record_tool_after_observation(
            tool_call=tool_call,
            usage=usage,
            round_num=round_num,
            is_error=is_error,
            elapsed_ms=elapsed_ms,
        )

    async def _prepare_tool_calls_for_execution(
        self,
        *,
        context: ConversationContext,
        config: AgentConfig,
        tool_calls: tuple[ToolCallRequest, ...],
        round_num: int,
        model: str,
        usage_so_far: dict[str, int],
    ) -> tuple[tuple[ToolCallRequest, ...], ApprovalRequiredPayload | None]:
        """按原始工具顺序串行执行 guardrail 前置评估并筛选可执行工具。"""

        executable: list[ToolCallRequest] = []
        for tool_call in tool_calls:
            capability_decision = await self._first_workflow_capability_denial(tool_call)
            if capability_decision is not None:
                approval_payload = await self._save_workflow_capability_interrupt(
                    context=context,
                    config=config,
                    tool_call=tool_call,
                    round_num=round_num,
                    model=model,
                    usage_so_far=usage_so_far,
                    decision=capability_decision,
                )
                return tuple(executable), approval_payload
            checkpoint_context = get_run_checkpoint_context()
            if checkpoint_context is not None:
                replay_entry, _tool_execution_key = await self._checkpoint_before_tool_call(
                    tool_call=tool_call,
                    round_num=round_num,
                    checkpoint_context=checkpoint_context,
                )
                if replay_entry is not None:
                    msg_index = context.add_tool_result(
                        tool_name=tool_call.name,
                        result=replay_entry.result or "",
                        tool_call_id=tool_call.id,
                    )
                    self._stamp_event(context, msg_index)
                    continue
            abuse_verdict = self._record_tool_call_for_abuse_detection(tool_call)
            if abuse_verdict.abuse_detected:
                self._emit_tool_abuse_detected(tool_call, abuse_verdict)
                self._record_tool_abuse_blocked_result(context, tool_call, abuse_verdict)
                continue
            accumulator = self._guardrail_runtime_accumulator()
            base_stats = accumulator.tool_before(
                tool_call=tool_call,
                risk_level=self._tool_risk_level(tool_call.name),
            )
            accumulator.remember_tool_before(tool_call=tool_call, stats=base_stats)
            decision = self._evaluate_tool_guardrail(tool_call, stats=base_stats)
            branch = interpret_tool_guardrail_decision(decision)
            if branch == "require_approval":
                action = PendingActionRequest(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments,
                    allowed_decisions=frozenset({"approve", "edit", "reject"}),
                    reason=decision.message,  # type: ignore[union-attr]
                )
                run_context = get_run_execution_context()
                approval_payload = await self._save_interrupt(
                    context,
                    config,
                    (action,),
                    round_num,
                    model,
                    dict(usage_so_far),
                    metadata=self._guardrail_metadata(
                        decision,
                        tool_call=tool_call,
                        blocked=True,
                        risk_gate_required=True,
                    )
                    | {
                        "source": "guardrail",
                        "guardrail_message": decision.message,  # type: ignore[union-attr]
                        "tool_call_ids": [tool_call.id],
                        "run_id": run_context.run_id if run_context is not None else None,
                    },
                )
                await self._record_guardrail_observation(
                    stage=GuardrailEvaluationStage.TOOL_BEFORE_EXECUTION,
                    decision=decision,
                    stats=base_stats,
                    round_num=round_num,
                    tool_call=tool_call,
                    approval_id=approval_payload.approval_id,
                )
                return tuple(executable), approval_payload
            # branch == "proceed" or "stop": record observation if decision is not None
            if decision is not None:
                await self._record_guardrail_observation(
                    stage=GuardrailEvaluationStage.TOOL_BEFORE_EXECUTION,
                    decision=decision,
                    stats=base_stats,
                    round_num=round_num,
                    tool_call=tool_call,
                )
            if branch == "stop":
                msg_index = context.add_tool_result(
                    tool_name=tool_call.name,
                    result=decision.message or "工具调用被护栏阻断。",  # type: ignore[union-attr]
                    tool_call_id=tool_call.id,
                )
                msg = context.get_messages()[msg_index]
                assert isinstance(msg, ToolMessage)
                msg.metadata.update(
                    self._guardrail_metadata(
                        decision,
                        tool_call=tool_call,
                        blocked=True,
                        risk_gate_required=True,
                    )
                )
                msg.metadata["error"] = True
                self._stamp_event(context, msg_index)
                continue
            executable.append(tool_call)
            continue
        return tuple(executable), None

    @staticmethod
    def _guardrail_metadata(
        decision: Any,
        *,
        tool_call: ToolCallRequest | None = None,
        approval_id: str | None = None,
        blocked: bool = False,
        risk_gate_required: bool = False,
    ) -> dict[str, Any]:
        """构造稳定的 guardrail 元数据标记。"""

        metadata: dict[str, Any] = {
            "guardrail_action": decision.action.value,
            "risk_gate_required": risk_gate_required,
        }
        if blocked:
            metadata["guardrail_blocked"] = True
        reason = getattr(decision, "reason", None)
        if reason is not None:
            metadata["guardrail_reason"] = reason.value
        if tool_call is not None:
            metadata["tool_call_id"] = tool_call.id
            metadata["tool_name"] = tool_call.name
        if approval_id is not None:
            metadata["approval_id"] = approval_id
        return metadata

    @staticmethod
    def _merge_guardrail_metadata(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
        """合并 guardrail 元数据，忽略 None 值。"""

        merged = dict(base)
        for key, value in extra.items():
            if value is not None:
                merged[key] = value
        return merged

    def _tool_runtime_stats(
        self,
        *,
        tool_call: ToolCallRequest,
        usage: dict[str, int] | None,
        start_time: float,
        repeated_tool_call_count: int = 0,
        consecutive_failure_count: int = 0,
        total_tool_calls: int = 0,
        last_tool_error: bool = False,
    ) -> GuardrailRuntimeStats:
        """构造工具执行阶段使用的基础运行时统计。"""

        risk_level = self._tool_risk_level(tool_call.name)
        usage_data = dict(usage or {})
        if usage_data:
            return GuardrailRuntimeStats.from_model_usage(
                usage=usage_data,
                model=None,
                model_pricing=None,
                elapsed_ms=(time.time() - start_time) * 1000,
                repeated_tool_call_count=repeated_tool_call_count,
                consecutive_failure_count=consecutive_failure_count,
                total_model_calls=0,
                total_tool_calls=total_tool_calls,
            )
        return GuardrailRuntimeStats(
            elapsed_ms=(time.time() - start_time) * 1000,
            repeated_tool_call_count=repeated_tool_call_count,
            consecutive_failure_count=consecutive_failure_count,
            total_tool_calls=total_tool_calls,
            last_tool_name=tool_call.name,
            last_tool_risk_level=risk_level.value,
            last_tool_error=last_tool_error,
        )

    async def _checkpoint_before_tool_call(
        self,
        *,
        tool_call: ToolCallRequest,
        round_num: int,
        checkpoint_context: Any,
    ) -> tuple[ToolResultLedgerEntry | None, str]:
        """执行 checkpoint 工具前探测，返回可回放账本与稳定执行键。"""

        replay_policy, side_effect_level, idempotency_key, tool_execution_key = (
            self._checkpoint_tool_metadata(
                tool_call=tool_call,
                run_id=checkpoint_context.run_id,
                segment_index=checkpoint_context.segment_index,
                round_num=round_num,
            )
        )
        replay_entry = await checkpoint_context.sink.before_tool_call(
            tool_call=tool_call,
            round_num=round_num,
            segment_index=checkpoint_context.segment_index,
            replay_policy=replay_policy,
            side_effect_level=side_effect_level,
            idempotency_key=idempotency_key,
        )
        if replay_entry is not None and replay_entry.status is ToolLedgerStatus.COMPLETED:
            return replay_entry, tool_execution_key
        self._guardrail_runtime_accumulator().remember_checkpoint_key(
            tool_call=tool_call,
            tool_execution_key=tool_execution_key,
        )
        return None, tool_execution_key

    def _checkpoint_tool_metadata(
        self,
        *,
        tool_call: ToolCallRequest,
        run_id: str,
        segment_index: int,
        round_num: int,
    ) -> tuple[ToolReplayPolicy, ToolSideEffectLevel, str | None, str]:
        """Resolve checkpoint replay metadata for a tool call."""

        arguments_digest = ToolExecutionKey.digest_arguments(tool_call.arguments)
        execution_key = ToolExecutionKey(
            run_id=run_id,
            segment_index=segment_index,
            round_num=round_num,
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
            arguments_digest=arguments_digest,
        ).stable_key()
        tool = self._get_registered_tool(tool_call.name)
        replay_policy = ToolReplayPolicy.MANUAL_REVIEW
        side_effect_level = ToolSideEffectLevel.EXTERNAL_WRITE
        idempotency_key: str | None = None
        if tool is not None:
            replay_policy = self._tool_attr_value(
                tool,
                "replay_policy",
                ToolReplayPolicy.MANUAL_REVIEW,
            )
            side_effect_level = self._tool_attr_value(
                tool,
                "side_effect_level",
                ToolSideEffectLevel.EXTERNAL_WRITE,
            )
            idempotency_fn = getattr(tool, "idempotency_key", None)
            if callable(idempotency_fn):
                candidate = idempotency_fn(tool_call, execution_key)
                if candidate is not None and not isinstance(candidate, str):
                    raise TypeError("tool.idempotency_key 必须返回 str | None")
                idempotency_key = candidate
        return replay_policy, side_effect_level, idempotency_key, execution_key

    def _get_registered_tool(self, tool_name: str) -> Any | None:
        get_fn = getattr(self._tool_registry, "get", None)
        if get_fn is None:
            return None
        try:
            tool = get_fn(tool_name)
        except Exception:
            return None
        return tool

    @staticmethod
    def _tool_attr_value(tool: Any, name: str, default: Any) -> Any:
        value = getattr(tool, name, default)
        return value() if callable(value) else value

    @staticmethod
    def _log_tool_failure(
        tool_call: ToolCallRequest,
        exc: BaseException,
        reason: str,
    ) -> None:
        """输出工具失败的 warning 级日志。

        本方法用于在工具执行抛出异常（无论是 ``ToolPermissionDeniedError``
        还是工具内部的运行期异常）时，向模块级 ``logger`` 写入一条结构化
        warning 日志，便于线上排查工具失败而不需要依赖回灌给 LLM 的
        ``ToolMessage`` 内容。

        日志字段：

        - ``tool_name``：``tool_call.name``，触发失败的工具名；
        - ``tool_call_id``：``tool_call.id``，模型本轮该次调用的唯一 ID；
        - ``reason``：调用方传入的失败语义标签，当前取值
          ``"permission_denied"`` 或 ``"execution_error"``；
        - ``exc_type``：异常类名（``type(exc).__name__``）；
        - ``exc_msg``：``str(exc)`` 摘要。

        本方法**不**记录 ``tool_call.arguments`` 完整文本，避免在长流场景下
        泄露凭证或写入大段不可控内容到日志系统。

        Args:
            tool_call: 触发失败的工具调用请求。
            exc: 被捕获的异常实例。
            reason: 失败语义标签。
        """
        logger.warning(
            "工具执行失败 tool_name=%s tool_call_id=%s reason=%s exc_type=%s exc_msg=%s",
            tool_call.name,
            tool_call.id,
            reason,
            type(exc).__name__,
            str(exc),
        )

    @staticmethod
    def ensure_agent_system_prompt(
        context: ConversationContext,
        config: AgentConfig,
    ) -> None:
        """以幂等方式注入当前 Agent 的 system_prompt。

        判定规则：当 ``config.system_prompt`` 为空时跳过；当
        ``context.get_messages()`` 中已存在任何 ``role == "system"`` 的消息
        时跳过；否则追加 ``config.system_prompt``。语义与
        ``ChatServiceAdapter._ensure_system_prompt`` 完全一致。

        本方法保证"每个 Agent 拥有独立的 system_prompt"（per-Agent 独立、
        幂等注入）：

        - ``AgentConfig`` 是 frozen dataclass，``system_prompt`` 字段在不同
          实例间互不共享、互不可变；
        - 子 Agent 拥有独立 ``ConversationContext`` 时（默认情形），首轮无
          SystemMessage，注入子 Agent 自己的 ``config.system_prompt``；
        - 子 Agent 复用父 Agent ``ConversationContext`` 时，父侧已注入过
          SystemMessage，幂等规则保证不重复注入，避免父子提示词冲突。

        Args:
            context: 对话上下文，可能被原地追加 SystemMessage。
            config: 当前 Agent 的执行配置。
        """
        if not config.system_prompt:
            return
        if any(m.role == "system" for m in context.get_messages()):
            return
        context.add_system_message(config.system_prompt)

    @staticmethod
    def _stamp_event(context: ConversationContext, message_index: int) -> None:
        """记录指定消息索引对应事件的发生时刻（毫秒整数）。

        v2 直接写入 ``context.event_timestamps`` 正式字段，不再通过 setattr
        隐式挂载 ``_event_timestamps`` 私有属性，亦不再使用 getattr 懒创建
        空 dict——``ConversationContext.__init__`` 已通过
        ``event_timestamps: dict[int, int] = {}`` 保证该字段在所有实例上
        以空 dict 形态存在，无需懒创建。

        该映射供 ``TaskAgentAdapter._extract_trace`` 读取真实事件时刻使用,
        并通过 ``ConversationContext.to_dict`` / ``from_dict`` 参与序列化,
        在 HITL resume 路径下经由 ``ApprovalInterrupt.context_snapshot`` 自然
        回环恢复。

        Args:
            context: 对话上下文，原地修改 ``event_timestamps`` 索引。
            message_index: 待打戳的消息在 ``context.get_messages()`` 中的索引。
        """
        context.event_timestamps[message_index] = int(time.time() * 1000)

    def _record_assistant_with_tool_calls(
        self,
        context: ConversationContext,
        response: LLMResponse,
    ) -> int:
        """将携带 tool_calls 的 AssistantMessage 追加到上下文并记录事件时刻。

        使用 ``ConversationContext.add_assistant_message_with_tool_calls`` 公开
        API 完成追加，并直接读取该 API 返回的索引，避免依赖
        ``message_count - 1`` 的隐式约定。同时把"该 AssistantMessage 在消息
        列表中的索引 → 当前事件发生时刻（毫秒整数）"写入
        ``context.event_timestamps`` 正式字段，供
        ``TaskAgentAdapter._extract_trace`` 读取真实时刻。

        Args:
            context: 对话上下文，原地修改。
            response: 当前轮次模型响应。

        Returns:
            追加后的 AssistantMessage 在 ``context.get_messages()`` 中的索引。
        """
        msg_index = context.add_assistant_message_with_tool_calls(
            content=response.content,
            tool_calls=list(response.tool_calls),
        )
        self._stamp_event(context, msg_index)
        return msg_index

    async def _iter_rounds(
        self,
        context: ConversationContext,
        config: AgentConfig,
        model_access: ModelAccessPort,
        *,
        start_round: int = 1,
        initial_usage: dict[str, int] | None = None,
        terminal_round: int | None = None,
        preserve_guardrail_runtime: bool = False,
    ) -> AsyncIterator[RoundOutcome]:
        """统一的轮次推进异步生成器——委托给领域编排主体。

        签名与四入口 async for 调用方式不变。实现体委托给
        ``AgentLoopOrchestrator.iter_rounds``，副作用经 ``self``
        （实现 ``AgentLoopEffects`` 协议）回调。
        """
        async for outcome in self._orchestrator.iter_rounds(
            context,
            config,
            model_access,
            effects=self,
            start_round=start_round,
            initial_usage=initial_usage,
            terminal_round=terminal_round,
            preserve_guardrail_runtime=preserve_guardrail_runtime,
        ):
            yield outcome

    # ──────────────────────────────────────────────────────────────────────────
    # AgentLoopEffects 端口实现
    # ──────────────────────────────────────────────────────────────────────────

    async def prepare_runtime(
        self,
        context: ConversationContext,
        config: AgentConfig,
        *,
        preserve_guardrail_runtime: bool,
    ) -> None:
        """准备运行时环境：guardrail 累加器、abuse detector、system prompt。"""
        run_context = get_run_execution_context()
        context_key = (
            run_context.run_id if run_context is not None else None,
            run_context.owner_id if run_context is not None else None,
            run_context.segment_index if run_context is not None else None,
        )
        current_accumulator = _CURRENT_GUARDRAIL_RUNTIME.get()
        if not (
            preserve_guardrail_runtime
            and current_accumulator is not None
            and current_accumulator.context_key == context_key
        ):
            _CURRENT_GUARDRAIL_RUNTIME.set(
                _GuardrailRuntimeAccumulator.from_summary(
                    run_context.guardrail_summary if run_context is not None else None,
                    context_key=context_key,
                )
            )
        _CURRENT_TOOL_ABUSE_DETECTOR.set(ToolAbuseDetector())
        self.ensure_agent_system_prompt(context, config)

    async def perform_model_round(
        self,
        context: ConversationContext,
        config: AgentConfig,
        model_access: ModelAccessPort,
        *,
        round_num: int,
        total_usage: dict[str, int],
    ) -> ModelRoundResult:
        """执行单轮模型调用（OTel span 内部关闭后返回）。"""
        response: LLMResponse
        with tracer.start_as_current_span(
            "react_agent.round",
            attributes={"react.round_num": round_num},
        ) as span:
            try:
                model_started_at = time.monotonic()
                builder_result = await self._context_builder.build(
                    context.get_messages(),
                    model_access=model_access,
                    model=config.model,
                )
                chat_request = ChatRequest(
                    messages=builder_result.messages,
                    model=config.model,
                    tools=config.tool_schemas,
                )
                accumulator = _RoundStreamAccumulator(model=config.model or "")
                await accumulator.consume(model_access.stream(chat_request))
                response = accumulator.build_response()
                new_total_usage = merge_usage(
                    dict(total_usage), builder_result.usage, response.usage
                )
                if self._guardrail_policy is not None:
                    model_elapsed_ms = (time.monotonic() - model_started_at) * 1000
                    model_stats = self._guardrail_runtime_accumulator().model_completed(
                        usage=response.usage,
                        model=response.model or config.model,
                        model_pricing=self._guardrail_model_pricing(),
                        elapsed_ms=model_elapsed_ms,
                        context_growth_messages=1,
                    )
                    evaluate_model_completed = getattr(
                        self._guardrail_policy,
                        "evaluate_model_completed",
                        None,
                    )
                    if callable(evaluate_model_completed):
                        model_decision = evaluate_model_completed(
                            GuardrailEvaluationContext(
                                total_tokens=model_stats.total_tokens,
                                elapsed_ms=model_stats.elapsed_ms,
                                context_growth_messages=model_stats.context_growth_messages,
                                repeated_tool_call_count=model_stats.repeated_tool_call_count,
                                consecutive_failure_count=model_stats.consecutive_failure_count,
                                metadata=guardrail_runtime_stats_to_dict(model_stats),
                            )
                        )
                        await self._record_guardrail_observation(
                            stage=GuardrailEvaluationStage.MODEL_COMPLETED,
                            decision=model_decision,
                            stats=model_stats,
                            round_num=round_num,
                        )

                # OTel span attributes
                span.set_attribute("react.tool_call_count", len(response.tool_calls))
                span.set_attribute("react.has_tool_calls", bool(response.tool_calls))
                for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    v = new_total_usage.get(k)
                    if isinstance(v, int):
                        span.set_attribute(f"gen_ai.usage.{k}", v)
                if not response.tool_calls:
                    pass  # text 路径
                else:
                    _pending_preview = self._collect_pending_actions(
                        response.tool_calls, config
                    )
                    if _pending_preview:
                        span.set_attribute("react.approval_required", True)
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR))
                raise

        return ModelRoundResult(response=response, total_usage=new_total_usage)

    def record_assistant_with_tool_calls(
        self,
        context: ConversationContext,
        response: LLMResponse,
    ) -> int:
        """将携带 tool_calls 的模型响应追加到上下文并返回消息索引。"""
        return self._record_assistant_with_tool_calls(context, response)

    def resolve_approval_policies(
        self,
        tool_calls: tuple[ToolCallRequest, ...],
        config: AgentConfig,
    ) -> Mapping[str, ApprovalPolicy]:
        """为 tool_calls 解析审批策略映射（含 warning 日志）。"""
        # 输出 warning 日志（副作用留 adapter）
        for tool_call in tool_calls:
            if tool_call.name not in config.allowed_tool_names:
                logger.warning(
                    "工具调用被拒绝: %s，允许的工具: %s",
                    tool_call.name,
                    sorted(config.allowed_tool_names),
                )
        # 预解析 policies mapping
        policies: dict[str, ApprovalPolicy] = {
            tc.name: self._approval_policy.policy_for(tc.name)
            for tc in tool_calls
            if tc.name in config.allowed_tool_names
        }
        return policies

    async def save_interrupt(
        self,
        context: ConversationContext,
        config: AgentConfig,
        actions: tuple[PendingActionRequest, ...],
        round_num: int,
        model: str,
        usage_so_far: dict[str, int],
    ) -> ApprovalRequiredPayload:
        """创建并保存审批中断状态。"""
        return await self._save_interrupt(
            context, config, actions, round_num, model, usage_so_far
        )

    async def prepare_tool_calls_for_execution(
        self,
        context: ConversationContext,
        config: AgentConfig,
        tool_calls: tuple[ToolCallRequest, ...],
        round_num: int,
        model: str,
        usage_so_far: dict[str, int],
    ) -> tuple[tuple[ToolCallRequest, ...], ApprovalRequiredPayload | None]:
        """按原始工具顺序执行 guardrail 前置评估并筛选可执行工具。"""
        return await self._prepare_tool_calls_for_execution(
            context=context,
            config=config,
            tool_calls=tool_calls,
            round_num=round_num,
            model=model,
            usage_so_far=usage_so_far,
        )

    async def execute_tool_call_result(
        self,
        context: ConversationContext,
        tool_call: ToolCallRequest,
        config: AgentConfig,
        *,
        round_num: int = 0,
        usage: dict[str, int] | None = None,
        skip_guardrail_before: bool = False,
        record_guardrail_after: bool = True,
    ) -> tuple[ToolExecutionResult, bool]:
        """执行一次工具调用，并返回结果与错误标志。"""

        return await self._execute_tool_call(
            context,
            tool_call,
            config,
            round_num=round_num,
            usage=usage,
            skip_guardrail_before=skip_guardrail_before,
            record_guardrail_after=record_guardrail_after,
        )

    async def dispatch_concurrent_tool_calls(
        self,
        context: ConversationContext,
        tool_calls: tuple[ToolCallRequest, ...],
        config: AgentConfig,
        session_id: str | None = None,
        round_num: int = 0,
    ) -> None:
        """通过并发工具协调器执行已准备的调用批次。"""

        await self._dispatch_concurrent_tool_calls(
            context,
            tool_calls,
            config,
            session_id=session_id,
            round_num=round_num,
        )

    def guardrail_runtime_stats(self) -> GuardrailRuntimeStats:
        """返回当前累计的护栏运行时统计。"""

        return self._guardrail_runtime_accumulator().snapshot()

    async def iter_rounds(
        self,
        context: ConversationContext,
        config: AgentConfig,
        model_access: ModelAccessPort,
        *,
        terminal_round: int | None = None,
    ) -> AsyncIterator[RoundOutcome]:
        """向流式应用适配器提供带类型的轮次迭代器。"""

        async for outcome in self._iter_rounds(
            context, config, model_access, terminal_round=terminal_round
        ):
            yield outcome

    async def checkpoint_model_completed(
        self,
        context: ConversationContext,
        round_num: int,
        total_usage: dict[str, int],
        response: LLMResponse,
    ) -> None:
        """模型调用完成后的 checkpoint 写入。"""
        checkpoint_context = get_run_checkpoint_context()
        if checkpoint_context is not None:
            await checkpoint_context.sink.model_completed(
                context=context,
                round_num=round_num,
                usage=dict(total_usage),
                trace_summary={
                    "model": response.model,
                    "tool_call_count": len(response.tool_calls),
                },
                segment_metadata={
                    "segment_index": checkpoint_context.segment_index,
                },
            )

    async def checkpoint_approval_interrupt(
        self,
        context: ConversationContext,
        round_num: int,
        total_usage: dict[str, int],
        approval_id: str,
    ) -> None:
        """审批中断时的 checkpoint 写入。"""
        checkpoint_context = get_run_checkpoint_context()
        if checkpoint_context is not None:
            await checkpoint_context.sink.approval_interrupt(
                context=context,
                round_num=round_num,
                usage=dict(total_usage),
                approval_id=approval_id,
            )

    def record_terminated(
        self,
        reason: str,
        round_num: int,
        total_usage: dict[str, int],
        config: AgentConfig,
        *,
        tool_call_count: int = 0,
        handoff_target: str = "",
    ) -> None:
        """记录 Agent Loop 终止：OTel span + 日志。"""
        if reason == "token_budget_exceeded":
            self._log_token_budget_exceeded(round_num, total_usage, config)
            with tracer.start_as_current_span(
                "react_agent.terminated",
                attributes={
                    "react.terminated_reason": "token_budget_exceeded",
                    "react.round_num": round_num,
                },
            ):
                pass
        elif reason == "max_rounds":
            logger.warning(
                "Agent Loop 达到 max_rounds 仍存在未消费 tool_calls",
                extra={
                    "round_num": round_num,
                    "tool_call_count": tool_call_count,
                },
            )
            with tracer.start_as_current_span(
                "react_agent.terminated",
                attributes={
                    "react.terminated_reason": "max_rounds",
                    "react.round_num": round_num,
                    "react.tool_call_count": tool_call_count,
                },
            ):
                pass
        elif reason == "handoff":
            with tracer.start_as_current_span(
                "react_agent.terminated",
                attributes={
                    "react.terminated_reason": "handoff",
                    "react.handoff_target": handoff_target,
                    "react.round_num": round_num,
                },
            ):
                pass

    async def execute_tool_call(
        self,
        context: ConversationContext,
        tool_call: ToolCallRequest,
        config: AgentConfig,
        *,
        round_num: int,
    ) -> None:
        """供工具执行协调器回调的单工具执行入口。"""

        start_t = time.time()
        result, is_error = await self._execute_tool_call(
            context,
            tool_call,
            config,
            round_num=round_num,
            skip_guardrail_before=True,
            record_guardrail_after=False,
        )
        elapsed_ms = (time.time() - start_t) * 1000
        await self._record_tool_call_trace(
            context.session_id,
            round_num,
            tool_call,
            result,
            is_error,
            elapsed_ms,
        )
        await self._record_tool_after_observation(
            tool_call=tool_call,
            usage=None,
            round_num=round_num,
            is_error=is_error,
            elapsed_ms=elapsed_ms,
        )

    @staticmethod
    def tool_progress_chunk(
        round_num: int,
        tool_call: ToolCallRequest,
        phase: str,
    ) -> StreamingChunk:
        """供工具执行协调器构造工具进度分片。"""

        return StreamingChunk(
            delta_content="",
            finished=False,
            metadata={
                "type": "tool_progress",
                "round": round_num,
                "tool_name": tool_call.name,
                "tool_call_id": tool_call.id,
                "phase": phase,
            },
        )

    @staticmethod
    def tool_start_event(round_num: int, tool_call: ToolCallRequest) -> AgentStreamEvent:
        """供工具执行协调器构造工具开始事件。"""

        return AgentStreamEvent(
            kind="tool_start",
            tool_name=tool_call.name,
            tool_call_id=tool_call.id,
            arguments=tool_call.arguments,
            metadata={"round": round_num},
        )

    @staticmethod
    def tool_result_event(
        round_num: int,
        tool_call: ToolCallRequest,
        content: str,
    ) -> AgentStreamEvent:
        """供工具执行协调器构造工具成功事件。"""

        return AgentStreamEvent(
            kind="tool_result",
            content=content,
            tool_name=tool_call.name,
            tool_call_id=tool_call.id,
            arguments=tool_call.arguments,
            metadata={"round": round_num},
        )

    @staticmethod
    def tool_error_event(
        round_num: int,
        tool_call: ToolCallRequest,
        content: str,
    ) -> AgentStreamEvent:
        """供工具执行协调器构造工具失败事件。"""

        return AgentStreamEvent(
            kind="tool_error",
            content=content,
            tool_name=tool_call.name,
            tool_call_id=tool_call.id,
            arguments=tool_call.arguments,
            metadata={"round": round_num},
        )

    async def _dispatch_concurrent_tool_calls(
        self,
        context: ConversationContext,
        tool_calls: tuple[ToolCallRequest, ...],
        config: AgentConfig,
        session_id: str | None = None,
        round_num: int = 0,
    ) -> None:
        """同轮多个工具调用并发执行（委托工具执行协调器，ADR-0013 留基础设施）。"""
        await self._tool_execution_coordinator.dispatch(
            context=context,
            tool_calls=tool_calls,
            config=config,
            round_num=round_num,
        )

    async def _stream_concurrent_tool_progress(
        self,
        context: ConversationContext,
        tool_calls: tuple[ToolCallRequest, ...],
        config: AgentConfig,
        round_num: int,
    ) -> AsyncIterator[StreamingChunk]:
        """run_streaming 同轮工具并发进度（委托工具执行协调器）。"""
        async for chunk in self._tool_execution_coordinator.stream_progress(
            context=context,
            tool_calls=tool_calls,
            config=config,
            round_num=round_num,
        ):
            yield chunk

    async def _events_concurrent_tool_calls(
        self,
        context: ConversationContext,
        tool_calls: tuple[ToolCallRequest, ...],
        config: AgentConfig,
        round_num: int,
    ) -> AsyncIterator[AgentStreamEvent]:
        """run_events 同轮工具并发事件（委托工具执行协调器）。"""
        async for event in self._tool_execution_coordinator.stream_events(
            context=context,
            tool_calls=tool_calls,
            config=config,
            round_num=round_num,
        ):
            yield event

    async def run(
        self,
        context: ConversationContext,
        config: AgentConfig,
        model_access: ModelAccessPort,
    ) -> AgentResult:
        """同步执行 Agent Loop，消费 ``_iter_rounds`` 产出的轮次结果。

        每轮按 ``RoundOutcome.kind`` 分支处理：
        - ``tool_calls``：并发执行工具，结果回灌 context，继续下一轮；
        - ``text`` / ``final`` / ``approval``：翻译为 ``AgentResult`` 返回。

        Args:
            context: 对话上下文对象，循环过程中会被原地修改
            config: Agent 执行配置
            model_access: 模型访问端口实例

        Returns:
            AgentResult，包含最终回复内容、模型名称、累计 token 用量和延迟信息
        """
        last_round_num = 1
        try:
            async for outcome in self._iter_rounds(context, config, model_access):
                last_round_num = outcome.round_num
                # 记录 ModelCallTrace（每轮都有 response）
                if outcome.response:
                    await self._record_trace(
                        context.session_id,
                        self._build_model_call_trace(outcome, config),
                    )
                if outcome.kind == "tool_calls":
                    await self._dispatch_concurrent_tool_calls(
                        context, outcome.tool_calls, config, context.session_id, outcome.round_num
                    )
                    continue
                # approval 路径额外记录 ApprovalTrace
                if outcome.kind == "approval" and outcome.approval:
                    await self._record_trace(
                        context.session_id,
                        self._build_approval_trace(outcome),
                    )
                return outcome_to_agent_result(outcome)
        except _GuardrailApprovalRequired:
            raise
        except Exception as exc:
            # Agent Loop 级别非工具异常：补录 ErrorTrace 后继续向上传播原始异常。
            await self._record_error_trace(context.session_id, last_round_num, exc)
            raise

        # 理论上不可达：_iter_rounds 至少会 yield 一个非 tool_calls 的 outcome
        raise RuntimeError("_iter_rounds 未产出终止结果")

    async def apply_approval_decisions(
        self,
        context: ConversationContext,
        config: AgentConfig,
        interrupt: ApprovalInterrupt,
        decisions: tuple[ApprovalDecision, ...],
    ) -> None:
        """校验并应用审批恢复决策。"""
        await self._apply_approval_decisions(context, config, interrupt, decisions)

    async def _apply_approval_decisions(
        self,
        context: ConversationContext,
        config: AgentConfig,
        interrupt: ApprovalInterrupt,
        decisions: tuple[ApprovalDecision, ...],
    ) -> None:
        """校验并应用审批恢复决策（委托审批恢复协调器）。"""
        await self._approval_resume_coordinator.apply_decisions(
            context=context,
            config=config,
            interrupt=interrupt,
            decisions=decisions,
        )

    async def execute_approved_tool_call(
        self,
        context: ConversationContext,
        tool_call: ToolCallRequest,
        config: AgentConfig,
        *,
        round_num: int,
        usage: dict[str, int],
    ) -> None:
        """供审批恢复协调器执行 approve/edit 后的工具调用。"""

        await self._execute_tool_call(
            context,
            tool_call,
            config,
            round_num=round_num,
            usage=usage,
        )

    def validate_edited_tool_call(self, tool_name: str, arguments: object) -> None:
        """供审批恢复协调器复用注册工具参数校验。"""

        tool = self._tool_registry.get(tool_name)
        if tool is None:
            return
        if not isinstance(arguments, dict):
            raise ApprovalEditInvalidArgumentsError(tool_name, "工具参数必须为对象")
        cast_params = tool.cast_params(cast(dict[str, Any], arguments))
        errors = tool.validate_params(cast_params)
        if errors:
            raise ApprovalEditInvalidArgumentsError(tool_name, "; ".join(errors))

    async def record_rejected_tool_call(
        self,
        context: ConversationContext,
        action: PendingActionRequest,
        decision: ApprovalDecision,
        *,
        round_num: int,
        usage: dict[str, int],
    ) -> None:
        """供审批恢复协调器记录 reject 决策。"""

        await self._record_rejected_tool_call(
            context=context,
            action=action,
            result=decision.message or "用户拒绝执行该工具调用。",
            round_num=round_num,
            usage=usage,
        )

    async def _record_rejected_tool_call(
        self,
        *,
        context: ConversationContext,
        action: PendingActionRequest,
        result: str,
        round_num: int,
        usage: dict[str, int],
    ) -> None:
        """Record a rejected HITL action as a tool result and checkpoint entry."""

        checkpoint_context = get_run_checkpoint_context()
        tool_call = ToolCallRequest(
            id=action.tool_call_id,
            name=action.tool_name,
            arguments=action.arguments,
        )
        tool_execution_key: str | None = None
        if checkpoint_context is not None:
            replay_policy, side_effect_level, idempotency_key, tool_execution_key = (
                self._checkpoint_tool_metadata(
                    tool_call=tool_call,
                    run_id=checkpoint_context.run_id,
                    segment_index=checkpoint_context.segment_index,
                    round_num=round_num,
                )
            )
            replay_entry = await checkpoint_context.sink.before_tool_call(
                tool_call=tool_call,
                round_num=round_num,
                segment_index=checkpoint_context.segment_index,
                replay_policy=replay_policy,
                side_effect_level=side_effect_level,
                idempotency_key=idempotency_key,
            )
            if (
                replay_entry is not None
                and replay_entry.status is ToolLedgerStatus.COMPLETED
                and replay_entry.result is not None
            ):
                msg_index = context.add_tool_result(
                    tool_name=action.tool_name,
                    result=replay_entry.result,
                    tool_call_id=action.tool_call_id,
                )
                self._stamp_event(context, msg_index)
                return

        msg_index = context.add_tool_result(
            tool_name=action.tool_name,
            result=result,
            tool_call_id=action.tool_call_id,
        )
        msg = context.get_messages()[msg_index]
        assert isinstance(msg, ToolMessage)
        msg.metadata["error"] = True
        self._stamp_event(context, msg_index)
        if checkpoint_context is not None and tool_execution_key is not None:
            await checkpoint_context.sink.after_tool_call(
                context=context,
                tool_execution_key=tool_execution_key,
                result=result,
                is_error=True,
                metadata={
                    "decision": "reject",
                    "tool_name": action.tool_name,
                    "tool_call_id": action.tool_call_id,
                    "segment_index": checkpoint_context.segment_index,
                },
                round_num=round_num,
                usage=dict(usage),
            )

    def _latest_tool_calls_by_id(
        self,
        context: ConversationContext,
    ) -> dict[str, ToolCallRequest]:
        """返回上下文中最近 assistant tool_calls 的 ID 映射（委托审批恢复协调器）。"""
        return dict(self._approval_resume_coordinator.latest_tool_calls_by_id(context))

    async def resume(
        self,
        context: ConversationContext,
        config: AgentConfig,
        model_access: ModelAccessPort,
        interrupt: ApprovalInterrupt,
        decisions: tuple[ApprovalDecision, ...],
    ) -> AgentResult:
        """从审批中断恢复执行 Agent Loop，消费 ``_iter_rounds`` 继续推进。"""
        try:
            await self._apply_approval_decisions(context, config, interrupt, decisions)
        except _GuardrailApprovalRequired as exc:
            return AgentResult(
                content="",
                model=config.model or interrupt.model,
                usage=dict(interrupt.usage_so_far),
                latency_ms=0.0,
                status="approval_required",
                approval=exc.payload,
                terminated_reason="completed",
            )
        last_round_num = interrupt.round_num + 1
        try:
            async for outcome in self._iter_rounds(
                context,
                config,
                model_access,
                start_round=interrupt.round_num + 1,
                initial_usage=dict(interrupt.usage_so_far),
                preserve_guardrail_runtime=True,
            ):
                last_round_num = outcome.round_num
                if outcome.response:
                    await self._record_trace(
                        context.session_id,
                        self._build_model_call_trace(outcome, config),
                    )
                if outcome.kind == "tool_calls":
                    await self._dispatch_concurrent_tool_calls(
                        context, outcome.tool_calls, config, context.session_id, outcome.round_num
                    )
                    continue
                if outcome.kind == "approval" and outcome.approval:
                    await self._record_trace(
                        context.session_id,
                        self._build_approval_trace(outcome),
                    )
                return outcome_to_agent_result(outcome)
        except _GuardrailApprovalRequired:
            raise
        except Exception as exc:
            await self._record_error_trace(context.session_id, last_round_num, exc)
            raise

        raise RuntimeError("_iter_rounds 未产出终止结果")

    @staticmethod
    def _heartbeat_chunk(round_num: int) -> StreamingChunk:
        """构造心跳分片，通知客户端中间轮次仍在执行。"""
        return StreamingChunk(
            delta_content="",
            finished=False,
            metadata={"type": "heartbeat", "round": round_num},
        )

    async def _stream_final_round(
        self,
        context: ConversationContext,
        config: AgentConfig,
        model_access: ModelAccessPort,
        base_usage: dict[str, int],
        response_capture: list[LLMResponse] | None = None,
    ) -> AsyncIterator[StreamingChunk]:
        """委托最终轮流式协作者产出 ``StreamingChunk``。"""
        async for chunk in self._final_round_streamer.stream_chunks(
            context=context,
            config=config,
            model_access=model_access,
            round_num=config.max_rounds,
            initial_usage=base_usage,
            response_capture=response_capture,
        ):
            yield chunk

    async def _stream_events_final_round(
        self,
        context: ConversationContext,
        config: AgentConfig,
        model_access: ModelAccessPort,
        base_usage: dict[str, int],
        round_num: int,
        response_capture: list[LLMResponse] | None = None,
    ) -> AsyncIterator[AgentStreamEvent]:
        """委托最终轮流式协作者产出结构化事件。"""
        async for event in self._final_round_streamer.stream_events(
            context=context,
            config=config,
            model_access=model_access,
            round_num=round_num,
            initial_usage=base_usage,
            response_capture=response_capture,
        ):
            yield event

    async def run_streaming(
        self,
        context: ConversationContext,
        config: AgentConfig,
        model_access: ModelAccessPort,
    ) -> AsyncIterator[StreamingChunk]:
        """流式执行 Agent Loop，消费 ``_iter_rounds`` 产出 + 最后一轮流式输出。

        中间轮次通过 ``_iter_rounds`` 以同步 chat() 推进，并产出心跳/工具进度
        分片保持连接活跃。当 ``max_rounds == 1`` 时不进入 ``_iter_rounds``，
        直接调用 ``_stream_final_round`` 产出单轮流式分片。

        v2 重构：入口处不再调用 ``_ensure_agent_system_prompt``——
        ``Single_System_Prompt_Injection_Site``
        把生产代码注入点收口为 ``_iter_rounds`` 入口；``max_rounds == 1`` 分支
        因不经 ``_iter_rounds``，需独立显式注入 ``system_prompt``。

        Args:
            context: 对话上下文对象
            config: Agent 执行配置
            model_access: 模型访问端口实例

        Yields:
            StreamingChunk 分片
        """
        if config.max_rounds == 1:
            # 该分支不进 _iter_rounds，需独立保证 system_prompt 幂等注入
            # （Single_System_Prompt_Injection_Site 例外：唯一不经 _iter_rounds 的注入点）
            self.ensure_agent_system_prompt(context, config)
            # T3.5：快速路径同样补录 ModelCallTrace，使所有入口 trace 时间线一致。
            fast_path_response: list[LLMResponse] = []
            try:
                async for chunk in self._stream_final_round(
                    context,
                    config,
                    model_access,
                    base_usage={},
                    response_capture=fast_path_response,
                ):
                    yield chunk
            except _GuardrailApprovalRequired:
                raise
            except Exception as exc:
                await self._record_error_trace(context.session_id, 1, exc)
                raise
            if fast_path_response:
                await self._record_trace(
                    context.session_id,
                    self._build_model_call_trace_from_response(
                        round_num=1,
                        response=fast_path_response[0],
                        config=config,
                    ),
                )
            return

        terminal_round = config.max_rounds - 1
        last_usage: dict[str, int] = {}
        last_round_num = 1

        try:
            async for outcome in self._iter_rounds(
                context, config, model_access, terminal_round=terminal_round
            ):
                last_round_num = outcome.round_num
                if outcome.response:
                    await self._record_trace(
                        context.session_id,
                        self._build_model_call_trace(outcome, config),
                    )
                if outcome.kind == "tool_calls":
                    yield self._heartbeat_chunk(outcome.round_num)
                    async for chunk in self._stream_concurrent_tool_progress(
                        context, outcome.tool_calls, config, outcome.round_num
                    ):
                        yield chunk
                    last_usage = outcome.total_usage
                    continue

                if outcome.kind == "approval":
                    approval = outcome.approval
                    if approval is None:
                        raise RuntimeError("approval outcome 缺少审批载荷")
                    await self._record_trace(
                        context.session_id,
                        self._build_approval_trace(outcome),
                    )
                    yield StreamingChunk(
                        delta_content=(
                            "当前会话等待人工审批，"
                            f"approval_id={approval.approval_id}"
                        ),
                        finished=True,
                        usage=outcome.total_usage,
                        metadata=approval_payload_to_metadata(approval),
                    )
                    return

                if outcome.kind == "handoff":
                    # Spec A R1.3：控制权已转移，目标 Agent 最终回复直接作为 final
                    yield StreamingChunk(
                        delta_content=outcome.handoff_content,
                        finished=True,
                        usage=outcome.total_usage,
                        metadata={"handoff_target": outcome.handoff_target or ""},
                    )
                    return

                if outcome.kind == "text":
                    yield StreamingChunk(
                        delta_content=outcome.response.content,
                        finished=True,
                        usage=outcome.total_usage,
                    )
                    return

                # kind == "final": 中间轮次已耗尽
                if outcome.terminated_reason in ("max_rounds", "token_budget_exceeded"):
                    # max_rounds / token_budget_exceeded 命中：跳过 _stream_final_round，
                    # 不发起最后一轮 stream（NFR-1：命中预算时不追加额外 stream）。
                    yield StreamingChunk(
                        delta_content="",
                        finished=True,
                        usage=outcome.total_usage,
                        metadata={"terminated_reason": outcome.terminated_reason},
                    )
                    return
                last_usage = outcome.total_usage

            # 进入最后一轮流式调用（复用 _stream_final_round，避免与 max_rounds == 1 分支重复）
            async for chunk in self._stream_final_round(
                context, config, model_access, base_usage=last_usage
            ):
                yield chunk
        except _GuardrailApprovalRequired:
            raise
        except Exception as exc:
            await self._record_error_trace(context.session_id, last_round_num, exc)
            raise

    async def run_events(
        self,
        context: ConversationContext,
        config: AgentConfig,
        model_access: ModelAccessPort,
    ) -> AsyncIterator[AgentStreamEvent]:
        """流式结构化事件输出，消费 ``_iter_rounds`` + 最后一轮流式。

        不产出 Heartbeat / Tool_Progress 分片（与 run_streaming 区分）。

        v2 重构：入口处不再调用 ``_ensure_agent_system_prompt``——
        ``Single_System_Prompt_Injection_Site`` 把生产代码注入点收口为
        ``_iter_rounds`` 入口；``max_rounds == 1`` 分支因不经 ``_iter_rounds``，
        需独立显式注入 ``system_prompt``。
        """
        if config.max_rounds == 1:
            # 该分支不进 _iter_rounds，需独立保证 system_prompt 幂等注入
            # （Single_System_Prompt_Injection_Site 例外：唯一不经 _iter_rounds 的注入点）
            self.ensure_agent_system_prompt(context, config)
            yield AgentStreamEvent(
                kind="status",
                content="Agent round 1",
                metadata={"round": 1},
            )
            # T3.5：快速路径同样补录 ModelCallTrace，使所有入口 trace 时间线一致。
            fast_path_response: list[LLMResponse] = []
            try:
                async for ev in self._stream_events_final_round(
                    context,
                    config,
                    model_access,
                    base_usage={},
                    round_num=1,
                    response_capture=fast_path_response,
                ):
                    yield ev
            except _GuardrailApprovalRequired:
                raise
            except Exception as exc:
                await self._record_error_trace(context.session_id, 1, exc)
                raise
            if fast_path_response:
                await self._record_trace(
                    context.session_id,
                    self._build_model_call_trace_from_response(
                        round_num=1,
                        response=fast_path_response[0],
                        config=config,
                    ),
                )
            return

        terminal_round = config.max_rounds - 1
        last_usage: dict[str, int] = {}
        last_status_round = 1
        last_round_num = 1

        yield AgentStreamEvent(
            kind="status",
            content="Agent round 1",
            metadata={"round": 1},
        )

        try:
            async for outcome in self._iter_rounds(
                context, config, model_access, terminal_round=terminal_round
            ):
                last_round_num = outcome.round_num
                if outcome.round_num > last_status_round:
                    yield AgentStreamEvent(
                        kind="status",
                        content=f"Agent round {outcome.round_num}",
                        metadata={"round": outcome.round_num},
                    )
                    last_status_round = outcome.round_num

                if outcome.response:
                    await self._record_trace(
                        context.session_id,
                        self._build_model_call_trace(outcome, config),
                    )

                if outcome.kind == "tool_calls":
                    async for event in self._events_concurrent_tool_calls(
                        context, outcome.tool_calls, config, outcome.round_num
                    ):
                        yield event
                    last_usage = outcome.total_usage
                    continue

                if outcome.kind == "approval":
                    approval = outcome.approval
                    if approval is None:
                        raise RuntimeError("approval outcome 缺少审批载荷")
                    await self._record_trace(
                        context.session_id,
                        self._build_approval_trace(outcome),
                    )
                    yield AgentStreamEvent(
                        kind="approval_required",
                        content="当前请求等待人工审批，请通过审批恢复接口提交决策。",
                        usage=outcome.total_usage,
                        metadata=approval_payload_to_metadata(approval)
                        | {"round": outcome.round_num},
                    )
                    return

                if outcome.kind == "handoff":
                    # Spec A R1.3：控制权已转移，目标 Agent 最终回复作为父 Agent
                    # 最终回复透出（assistant_delta + assistant_done）
                    if outcome.handoff_content:
                        yield AgentStreamEvent(
                            kind="assistant_delta",
                            content=outcome.handoff_content,
                        )
                    yield AgentStreamEvent(
                        kind="assistant_done",
                        usage=outcome.total_usage,
                        metadata={
                            "round": outcome.round_num,
                            "handoff_target": outcome.handoff_target or "",
                        },
                    )
                    return

                if outcome.kind == "text":
                    if outcome.response.content:
                        yield AgentStreamEvent(
                            kind="assistant_delta",
                            content=outcome.response.content,
                        )
                    yield AgentStreamEvent(
                        kind="assistant_done",
                        usage=outcome.total_usage,
                        metadata={"round": outcome.round_num},
                    )
                    return

                # kind == "final": 中间轮次耗尽
                if outcome.terminated_reason in ("max_rounds", "token_budget_exceeded"):
                    # max_rounds / token_budget_exceeded 命中：跳过 _stream_events_final_round。
                    yield AgentStreamEvent(
                        kind="status",
                        content="round-final",
                        metadata={"round": outcome.round_num},
                    )
                    yield AgentStreamEvent(
                        kind="assistant_done",
                        usage=outcome.total_usage,
                        metadata={
                            "round": outcome.round_num,
                            "terminated_reason": outcome.terminated_reason,
                        },
                    )
                    return
                last_usage = outcome.total_usage

            # 最后一轮流式（复用 _stream_events_final_round，避免与 max_rounds == 1 分支重复）
            final_round = config.max_rounds
            yield AgentStreamEvent(
                kind="status",
                content=f"Agent round {final_round}",
                metadata={"round": final_round},
            )
            async for ev in self._stream_events_final_round(
                context, config, model_access, base_usage=last_usage, round_num=final_round
            ):
                yield ev
        except _GuardrailApprovalRequired:
            raise
        except Exception as exc:
            await self._record_error_trace(context.session_id, last_round_num, exc)
            raise
