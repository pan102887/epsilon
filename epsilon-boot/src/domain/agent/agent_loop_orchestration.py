"""领域层 Agent Loop 编排主体模块（P2 第二片 Wave 2）。

承载 ``AgentLoopOrchestrator``：从 ``ReActAgentAdapter._iter_rounds`` 平移的
循环推进骨架，副作用全部经 ``AgentLoopEffects`` 端口委托，实现零 OTel /
infrastructure / 框架依赖的领域服务。

本模块复用首片 ``agent_loop_policy`` 中的：
- ``RoundOutcome`` / ``RoundOutcomeKind``
- ``detect_handoff`` / ``is_token_budget_exceeded``
- ``collect_pending_actions``

编排主体保留源 ``_iter_rounds`` 的：
- 轮次区间 ``range(start_round, effective_terminal + 1)``
- ``budget_exceeded_pending_after_tools`` 跨轮状态机
- ``RoundOutcome`` 五态产出顺序
- ``Terminal_Round_Boundary_Assert``
- ``last_response is None`` 边界短路
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from domain.agent.agent_loop_policy import (
    RoundOutcome,
    collect_pending_actions,
    detect_handoff,
    is_token_budget_exceeded,
)

if TYPE_CHECKING:
    from domain.agent.ports import AgentLoopEffects, ModelRoundResult
    from domain.agent.value_objects import AgentConfig
    from domain.chat.context import ConversationContext
    from domain.model_access.ports import ModelAccessPort

logger = logging.getLogger(__name__)


class AgentLoopOrchestrator:
    """Agent Loop 编排主体（领域服务）。

    从 ``ReActAgentAdapter._iter_rounds`` 骨架平移而来，保留完整的循环推进
    状态机语义，副作用全部经 ``effects`` 端口委托。

    使用方式::

        orchestrator = AgentLoopOrchestrator()
        async for outcome in orchestrator.iter_rounds(
            context, config, model_access, effects=adapter, ...
        ):
            ...
    """

    async def iter_rounds(
        self,
        context: ConversationContext,
        config: AgentConfig,
        model_access: ModelAccessPort,
        *,
        effects: AgentLoopEffects,
        start_round: int = 1,
        initial_usage: dict[str, int] | None = None,
        terminal_round: int | None = None,
        preserve_guardrail_runtime: bool = False,
    ) -> AsyncIterator[RoundOutcome]:
        """统一的轮次推进异步生成器。

        覆盖 ``run`` / ``run_streaming`` / ``run_events`` / ``resume`` 四个入口的
        单轮推进语义。生成器的产出顺序由 ``RoundOutcome.kind`` 表达：

        - ``"tool_calls"``：调用方应在 ``__anext__`` 之前同步执行
          ``outcome.tool_calls`` 并通过 ``context.add_tool_result`` 把结果
          回写到上下文；
        - ``"approval"`` / ``"text"`` / ``"final"`` / ``"handoff"``：生成器
          自身已停止迭代，调用方拿到该 outcome 后即可结束消费。

        Args:
            context: 对话上下文，原地修改。
            config: Agent 执行配置。
            model_access: 模型访问端口。
            effects: 副作用端口实现（通常为 ``ReActAgentAdapter`` 自身）。
            start_round: 起始轮次号。
            initial_usage: 起始累计用量。
            terminal_round: 循环结束轮次（含），默认 ``config.max_rounds``。
            preserve_guardrail_runtime: 是否保留 guardrail 统计累加器。

        Yields:
            每轮的 ``RoundOutcome``。
        """
        # 准备运行时环境
        await effects.prepare_runtime(
            context, config, preserve_guardrail_runtime=preserve_guardrail_runtime
        )

        total_usage: dict[str, int] = dict(initial_usage or {})
        last_response = None  # LLMResponse | None
        effective_terminal = terminal_round if terminal_round is not None else config.max_rounds

        # 跨轮 token 预算超限标记
        budget_exceeded_pending_after_tools = False

        for round_num in range(start_round, effective_terminal + 1):
            if budget_exceeded_pending_after_tools:
                # 上一轮 tool_calls 命中预算，工具执行已完成 → 终止。
                effects.record_terminated(
                    reason="token_budget_exceeded",
                    round_num=round_num - 1,
                    total_usage=total_usage,
                    config=config,
                )
                yield RoundOutcome(
                    kind="final",
                    round_num=round_num - 1,
                    response=last_response,  # type: ignore[arg-type]
                    total_usage=dict(total_usage),
                    terminated_reason="token_budget_exceeded",
                )
                return

            # Handoff 短路检测
            if round_num > start_round and last_response is not None:
                handoff = detect_handoff(context)
                if handoff is not None:
                    handoff_target, handoff_content = handoff
                    effects.record_terminated(
                        reason="handoff",
                        round_num=round_num - 1,
                        total_usage=total_usage,
                        config=config,
                        handoff_target=handoff_target,
                    )
                    yield RoundOutcome(
                        kind="handoff",
                        round_num=round_num - 1,
                        response=last_response,
                        total_usage=dict(total_usage),
                        handoff_target=handoff_target,
                        handoff_content=handoff_content,
                    )
                    return

            # 执行模型调用（OTel span 在实现内部关闭）
            model_result: ModelRoundResult = await effects.perform_model_round(
                context,
                config,
                model_access,
                round_num=round_num,
                total_usage=total_usage,
            )
            response = model_result.response
            total_usage = model_result.total_usage
            last_response = response

            # ── span 已关闭，以下分支可安全 yield ──

            if not response.tool_calls:
                await effects.checkpoint_model_completed(
                    context, round_num, total_usage, response
                )
                # text 路径：自然终止
                yield RoundOutcome(
                    kind="text",
                    round_num=round_num,
                    response=response,
                    total_usage=dict(total_usage),
                )
                return

            await effects.checkpoint_model_completed(
                context, round_num, total_usage, response
            )
            msg_index = effects.record_assistant_with_tool_calls(context, response)

            # 审批策略检查
            policies = effects.resolve_approval_policies(
                tuple(response.tool_calls), config
            )
            pending = collect_pending_actions(
                tool_calls=list(response.tool_calls),
                allowed_tool_names=config.allowed_tool_names,
                policies=policies,
            )

            if pending:
                # approval 路径
                approval = await effects.save_interrupt(
                    context,
                    config,
                    pending,
                    round_num,
                    response.model,
                    dict(total_usage),
                )
                await effects.checkpoint_approval_interrupt(
                    context, round_num, dict(total_usage), approval.approval_id
                )
                yield RoundOutcome(
                    kind="approval",
                    round_num=round_num,
                    response=response,
                    tool_calls=tuple(response.tool_calls),
                    approval=approval,
                    total_usage=dict(total_usage),
                    assistant_message_index=msg_index,
                )
                return

            # guardrail 前置评估
            (
                executable_tool_calls,
                guardrail_approval,
            ) = await effects.prepare_tool_calls_for_execution(
                context,
                config,
                tuple(response.tool_calls),
                round_num,
                response.model,
                dict(total_usage),
            )
            if guardrail_approval is not None:
                await effects.checkpoint_approval_interrupt(
                    context, round_num, dict(total_usage), guardrail_approval.approval_id
                )
                yield RoundOutcome(
                    kind="approval",
                    round_num=round_num,
                    response=response,
                    tool_calls=tuple(response.tool_calls),
                    approval=guardrail_approval,
                    total_usage=dict(total_usage),
                    assistant_message_index=msg_index,
                )
                return

            # token budget 跨轮检测
            if is_token_budget_exceeded(config, total_usage):
                budget_exceeded_pending_after_tools = True

            yield RoundOutcome(
                kind="tool_calls",
                round_num=round_num,
                response=response,
                tool_calls=executable_tool_calls,
                total_usage=dict(total_usage),
                assistant_message_index=msg_index,
            )
            # 工具执行由 caller 完成
            logger.info(
                "Agent Loop 第 %d 轮完成，执行工具: %s",
                round_num,
                [tc.name for tc in response.tool_calls],
            )

        # 循环耗尽
        if last_response is None:
            # 数学边界：terminal_round=0 等情况下未发生任何 stream 调用。
            return

        # Terminal_Round_Boundary_Assert
        from domain.chat.context import ToolMessage as _ToolMessage

        messages = context.get_messages()
        assert (
            bool(last_response.tool_calls)
            and bool(messages)
            and isinstance(messages[-1], _ToolMessage)
        ), (
            "Agent Loop 循环耗尽分支不变量被打破：期望最后一轮 tool_calls 且"
            " caller 已执行工具回写 ToolMessage（自然终止路径已在循环体内 return）"
        )

        # max_rounds 命中
        effects.record_terminated(
            reason="max_rounds",
            round_num=effective_terminal,
            total_usage=total_usage,
            config=config,
            tool_call_count=len(last_response.tool_calls),
        )
        yield RoundOutcome(
            kind="final",
            round_num=effective_terminal,
            response=last_response,
            total_usage=dict(total_usage),
            terminated_reason="max_rounds",
        )
