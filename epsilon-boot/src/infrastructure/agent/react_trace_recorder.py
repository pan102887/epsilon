"""ReAct 结构化追踪记账协作者。

从 ``ReActAgentAdapter`` 门面抽出的结构化 trace 记账职责（SRP 拆分，
``ddd-followup-refinements`` 切片 C）：把「模型调用 / 审批 / 工具调用 / 错误」
四类 trace 值对象的构建与写入收敛到单一协作类，门面通过组合持有本记录器并在
原调用点委托。本模块为基础设施层内部协作者，不改变分层方向、不上提领域层、
不改变对外可观测行为，trace 写入失败仍按既有语义静默隔离（记 ``warning``，
不阻断主流程）。

``TraceStorePort`` 为 ``None`` 或 ``session_id`` 为空时记账静默跳过，与拆分前
行为逐字节等价。
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from domain.agent.agent_loop_policy import RoundOutcome
    from domain.agent.tools import ToolExecutionResult
    from domain.agent.value_objects import AgentConfig
    from domain.model_access.value_objects import LLMResponse, ToolCallRequest

logger = logging.getLogger(__name__)


class ReActTraceRecorder:
    """封装 ReAct 执行的结构化 trace 构建与写入。

    持有可选的 ``TraceStorePort``；为 ``None`` 或 ``session_id`` 为空时全部记账
    操作静默跳过。所有写入经 ``_record`` 统一做故障隔离（捕获异常 + ``warning``），
    保证 trace 写入失败不影响 Agent Loop 主流程。
    """

    def __init__(self, trace_store: Any | None) -> None:
        """初始化 trace 记录器。

        Args:
            trace_store: 结构化追踪存储端口；未提供时全部记账静默跳过。
        """
        self._trace_store = trace_store

    async def record_step(self, session_id: str | None, step: Any) -> None:
        """记录一步结构化追踪。trace_store 为 None 或 session_id 为 None 时静默跳过。"""
        if self._trace_store is None or not session_id:
            return
        try:
            await self._trace_store.append_step(session_id, step)
        except Exception:
            logger.warning("trace 记录失败，session_id=%s", session_id, exc_info=True)

    @staticmethod
    def truncate(text: str, max_len: int) -> str:
        """截断文本到指定长度。"""
        if len(text) <= max_len:
            return text
        return text[: max_len - 3] + "..."

    def build_model_call_trace(self, outcome: RoundOutcome, config: AgentConfig) -> Any:
        """从 RoundOutcome 构建 ModelCallTrace。"""
        from domain.agent.trace_value_objects import ModelCallTrace

        usage = outcome.response.usage if outcome.response else {}
        return ModelCallTrace(
            round_num=outcome.round_num,
            model=outcome.response.model if outcome.response else (config.model or "default"),
            prompt_id=config.prompt_id,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            latency_ms=outcome.response.latency_ms if outcome.response else 0.0,
            timestamp_epoch=time.time(),
        )

    def build_model_call_trace_from_response(
        self,
        round_num: int,
        response: LLMResponse,
        config: AgentConfig,
    ) -> Any:
        """从单轮 ``LLMResponse`` 构建 ``ModelCallTrace``。

        供 ``run_streaming`` / ``run_events`` 的 ``max_rounds==1`` 快速路径补录
        使用，字段构造逻辑与 ``build_model_call_trace`` 完全一致，仅入参从
        ``RoundOutcome`` 换为直接的 ``LLMResponse``（快速路径不产出
        ``RoundOutcome``）。

        Args:
            round_num: 轮次号，快速路径固定为 1。
            response: 单轮模型响应，携带 model / usage / latency_ms。
            config: Agent 执行配置，提供 prompt_id 与默认 model 回退。

        Returns:
            构造好的 ``ModelCallTrace`` 值对象。
        """
        from domain.agent.trace_value_objects import ModelCallTrace

        usage = response.usage
        return ModelCallTrace(
            round_num=round_num,
            model=response.model or (config.model or "default"),
            prompt_id=config.prompt_id,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            latency_ms=response.latency_ms,
            timestamp_epoch=time.time(),
        )

    def build_approval_trace(self, outcome: RoundOutcome) -> Any:
        """从 RoundOutcome 构建 ApprovalTrace。"""
        from domain.agent.trace_value_objects import ApprovalTrace

        approval = outcome.approval
        actions_summary = [a.tool_name for a in approval.actions] if approval else []
        return ApprovalTrace(
            round_num=outcome.round_num,
            approval_id=approval.approval_id if approval else "",
            actions_summary=actions_summary,
            timestamp_epoch=time.time(),
        )

    async def record_error_trace(
        self,
        session_id: str | None,
        round_num: int,
        exc: BaseException,
    ) -> None:
        """记录 Agent Loop 级别非工具异常为 ``ErrorTrace``（fire-and-forget）。

        仅用于 Agent Loop 级别的非工具异常（如模型调用失败、上下文构建错误、
        HITL 状态加载失败等）；工具执行失败仍通过 ``ToolCallTrace.success=False``
        与 ``error_class`` / ``error_message`` 记录，不走本路径。

        本方法委托 ``record_step`` 的既有故障隔离语义（捕获异常 +
        ``logger.warning``），追踪写入失败不会阻止原始异常向上传播。

        Args:
            session_id: 会话唯一标识；为 None 时追踪静默跳过。
            round_num: 异常发生时的当前轮次号。
            exc: 被捕获的 Agent Loop 级异常实例。
        """
        from domain.agent.trace_value_objects import ERROR_MESSAGE_MAX_LEN, ErrorTrace

        await self.record_step(
            session_id,
            ErrorTrace(
                round_num=round_num,
                error_class=type(exc).__name__,
                error_message=self.truncate(str(exc), ERROR_MESSAGE_MAX_LEN),
                timestamp_epoch=time.time(),
            ),
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
        """记录单个工具调用追踪。

        从 ``result.content`` 截断得到 ``result_summary``，从 ``result.metadata``
        提取结构化元数据（含失败时的 ``error_class``）并透传给
        ``ToolCallTrace.metadata``。失败（``is_error=True``）时，``error_class``
        取 ``result.metadata`` 中的 ``error_class`` 键，``error_message`` 取截断后
        的 ``result.content``；成功时二者均为 ``None``。

        Args:
            session_id: 会话唯一标识；为 None 时追踪静默跳过。
            round_num: 当前轮次号。
            tool_call: 触发的工具调用请求。
            result: 工具执行结果值对象，携带回灌文本与结构化元数据。
            is_error: 工具是否执行失败。
            elapsed_ms: 工具执行耗时（毫秒）。
        """
        from domain.agent.trace_value_objects import (
            ARGUMENTS_SUMMARY_MAX_LEN,
            ERROR_MESSAGE_MAX_LEN,
            RESULT_SUMMARY_MAX_LEN,
            ToolCallTrace,
        )

        error_class: str | None = None
        error_message: str | None = None
        if is_error:
            raw_error_class = result.metadata.get("error_class")
            error_class = str(raw_error_class) if raw_error_class is not None else None
            error_message = self.truncate(result.content, ERROR_MESSAGE_MAX_LEN)

        await self.record_step(
            session_id,
            ToolCallTrace(
                round_num=round_num,
                tool_name=tool_call.name,
                tool_call_id=tool_call.id,
                arguments_summary=self.truncate(tool_call.arguments, ARGUMENTS_SUMMARY_MAX_LEN),
                result_summary=self.truncate(result.content, RESULT_SUMMARY_MAX_LEN),
                success=not is_error,
                latency_ms=elapsed_ms,
                timestamp_epoch=time.time(),
                error_class=error_class,
                error_message=error_message,
                metadata=self.truncate_metadata(result.metadata),
            ),
        )

    @staticmethod
    def truncate_metadata(
        metadata: dict[str, Any],
        max_total_bytes: int = 2048,
    ) -> dict[str, Any]:
        """截断 metadata dict 的总序列化大小，控制单条 JSONL 行体积。

        逐个保留键值对，直到累计序列化字节数接近上限；超出时丢弃剩余键并写入
        ``_truncated`` 标记。序列化使用 ``default=str`` 兜底异构值类型（NFR-6：
        metadata 序列化失败不中断追踪）。

        Args:
            metadata: 工具执行结果携带的结构化元数据。
            max_total_bytes: 序列化后 UTF-8 字节数上限，默认 2048（≈2KB）。

        Returns:
            截断后的 metadata dict；空输入返回空 dict。
        """
        if not metadata:
            return {}
        serialized = json.dumps(metadata, ensure_ascii=False, default=str)
        if len(serialized.encode("utf-8")) <= max_total_bytes:
            return metadata
        # 逐键截断，预留 50 字节给 ``_truncated`` 标记。
        truncated: dict[str, Any] = {}
        current_size = 2  # "{}"
        for key, value in metadata.items():
            entry = json.dumps({key: value}, ensure_ascii=False, default=str)
            entry_size = len(entry.encode("utf-8"))
            if current_size + entry_size > max_total_bytes - 50:
                truncated["_truncated"] = True
                break
            truncated[key] = value
            current_size += entry_size
        return truncated
