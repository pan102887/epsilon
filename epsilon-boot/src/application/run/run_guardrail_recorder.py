"""Run guardrail 记录应用服务。

本模块负责把 ReAct 运行时产生的 guardrail 观测，在存在
``RunExecutionContext`` 时收敛为同一条 Run 的事件流与护栏摘要。
应用服务只编排领域值对象与 Run 存储端口，不依赖 FastAPI 或基础设施实现。
"""

from __future__ import annotations

import contextlib
from dataclasses import replace
from typing import Any

from application.run.serialization_ports import GuardrailSerializerPort
from domain.agent.guardrails import (
    GuardrailAction,
    GuardrailObservation,
    GuardrailRuntimeStats,
    merge_guardrail_summary,
)
from domain.agent.ports import RunGuardrailRecorderPort
from domain.run.exceptions import RunNotFoundError
from domain.run.ports import RunObservationStorePort, RunStorePort
from domain.run.runtime_context import get_run_execution_context
from domain.run.value_objects import RunEventType, RunSnapshot


class RunGuardrailRecorder(RunGuardrailRecorderPort):
    """把 guardrail 决策收敛到 Run 事件流与摘要。"""

    def __init__(
        self,
        *,
        run_store: RunStorePort,
        observation_store: RunObservationStorePort,
        guardrail_serializer: GuardrailSerializerPort,
    ) -> None:
        """初始化 Run guardrail recorder。"""

        self._run_store = run_store
        self._observation_store = observation_store
        self._guardrail_serializer = guardrail_serializer

    async def record_observation(
        self,
        *,
        observation: GuardrailObservation,
    ) -> RunSnapshot | None:
        """记录一次 guardrail 观测；非 Run 路径直接返回 None。"""

        context = get_run_execution_context()
        if context is None:
            return None

        snapshot = await self._run_store.get_run(context.run_id)
        if snapshot is None:
            raise RunNotFoundError(context.run_id)

        event_payload = self._guardrail_serializer.guardrail_observation_to_event_payload(
            observation
        )
        normalized_observation = _observation_with_payload_stats(
            observation,
            event_payload,
        )
        summary_after = merge_guardrail_summary(
            snapshot.guardrail_summary,
            normalized_observation,
            event_cursor=_next_event_cursor(snapshot),
        )
        snapshot_after, _ = await self._observation_store.record_runtime_observation(
            run_id=context.run_id,
            owner_id=context.owner_id,
            event_type=_event_type_for_action(observation.decision.action),
            payload=event_payload,
            guardrail_summary=self._guardrail_serializer.guardrail_summary_to_dict(
                summary_after
            ),
        )
        return snapshot_after


def _event_type_for_action(action: GuardrailAction) -> RunEventType:
    """根据 guardrail 动作映射对应的 Run 事件类型。"""

    if action in {GuardrailAction.ALLOW, GuardrailAction.OBSERVE}:
        return RunEventType.GUARDRAIL_EVALUATED
    return RunEventType.GUARDRAIL_BLOCKED


def _observation_with_payload_stats(
    observation: GuardrailObservation,
    payload: dict[str, Any],
) -> GuardrailObservation:
    """让摘要合并复用事件 payload 中的同一份运行时统计。"""

    payload_stats = payload.get("stats")
    if not isinstance(payload_stats, dict):
        return observation
    return replace(
        observation,
        stats=GuardrailRuntimeStats(**payload_stats),
    )


def _next_event_cursor(snapshot: RunSnapshot) -> int:
    """基于当前快照保守估算下一条事件游标。"""

    latest_cursor = snapshot.latest_event_cursor or 0
    summary = snapshot.guardrail_summary
    if isinstance(summary, dict):
        raw_cursor = summary.get("last_event_cursor")
        if raw_cursor is not None:
            with contextlib.suppress(TypeError, ValueError):
                latest_cursor = max(latest_cursor, int(raw_cursor))
    return latest_cursor + 1
