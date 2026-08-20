"""HITL 审批载荷的流式元数据共享序列化模块。

抽取 ``run_streaming`` / ``run_events`` 与
``approval_state_store.approval_interrupt_to_dict`` 共用的 actions 序列化
形态，保证 ``Approval_Stream_Metadata`` 可被标准 ``json.dumps`` 直接序列化，
避免在多处独立维护两份字典生成代码。

本模块属于基础设施层内部 helper，仅被 ``infrastructure/agent/`` 下的
``react_agent_adapter`` 与 ``approval_state_store`` 复用，不向 ``domain/``
反向暴露任何符号，遵循 `docs/steering/ddd-architecture.md` 的依赖方向。
"""

from __future__ import annotations

from typing import Any

from domain.agent.value_objects import (
    ApprovalRequiredPayload,
    PendingActionRequest,
)

_APPROVAL_METADATA_WHITELIST = (
    "source",
    "guardrail_action",
    "guardrail_reason",
    "guardrail_message",
    "risk_gate_required",
)


def approval_actions_to_dicts(
    actions: tuple[PendingActionRequest, ...],
) -> list[dict[str, Any]]:
    """把 ``PendingActionRequest`` 元组序列化为 JSON 友好的 dict 列表。

    ``allowed_decisions`` 通过 ``sorted(...)`` 转换为 list，确保 JSON 安全
    （``frozenset`` 无法被标准 ``json.dumps`` 直接序列化）；与
    ``infrastructure.agent.approval_state_store.approval_interrupt_to_dict``
    中 actions 的字段集合及类型形态保持完全一致，避免存储侧与流式元数据侧
    的字典 schema 出现漂移。

    Args:
        actions: 待审批动作元组，顺序与模型 ``tool_calls`` 一致。

    Returns:
        每个 dict 至少包含 ``tool_call_id`` / ``tool_name`` / ``arguments`` /
        ``allowed_decisions`` / ``reason``，全部为 JSON 原生类型；
        ``allowed_decisions`` 为 ``sorted(list[str])``。
    """
    return [
        {
            "tool_call_id": action.tool_call_id,
            "tool_name": action.tool_name,
            "arguments": action.arguments,
            "allowed_decisions": sorted(action.allowed_decisions),
            "reason": action.reason,
        }
        for action in actions
    ]


def approval_payload_to_metadata(
    payload: ApprovalRequiredPayload,
) -> dict[str, Any]:
    """构造 ``run_streaming`` / ``run_events`` 共用的安全审批元数据字典。

    该返回值用于普通 SSE / 结构化事件元数据，而不是已认证审批界面的完整
    载荷，因此必须避免把完整 ``actions`` 或工具参数透传到通用观测链路。
    完整动作参数仍保留在 ``ApprovalRequiredPayload.actions`` 与独立的审批状态
    存储对象中，供受控审批界面读取。

    返回值满足以下不变量：

    - 顶层只包含稳定安全字段：``status`` / ``session_id`` /
      ``approval_id`` / ``action_count`` / ``action_summaries``；
    - ``action_summaries`` 仅暴露 ``tool_call_id`` / ``tool_name`` /
      ``allowed_decisions`` / ``reason``，不包含 ``arguments``；
    - 仅白名单透传 ``payload.metadata`` 中的稳定 guardrail 标记，避免
      ``**payload.metadata`` 带入未经审查的敏感字段；
    - 不直接引用 ``frozenset`` / ``tuple`` / dataclass 等无法被标准
      ``json.dumps`` 处理的对象。

    Args:
        payload: Agent 返回给上层的审批中断载荷。

    Returns:
        JSON 安全的安全元数据字典；调用方可按需 ``| {"round": ...}``
        合并轮次号或其他上下文键。
    """
    metadata: dict[str, Any] = {
        "status": "approval_required",
        "session_id": payload.session_id,
        "approval_id": payload.approval_id,
        "action_count": len(payload.actions),
        "action_summaries": [
            {
                "tool_call_id": action.tool_call_id,
                "tool_name": action.tool_name,
                "allowed_decisions": sorted(action.allowed_decisions),
                "reason": action.reason,
            }
            for action in payload.actions
        ],
    }
    for key in _APPROVAL_METADATA_WHITELIST:
        value = payload.metadata.get(key)
        if value is not None:
            metadata[key] = value
    return metadata
