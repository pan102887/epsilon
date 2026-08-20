"""ReActAgentAdapter 权限拒绝日志单元测试模块。

验证 ReActAgentAdapter 在工具调用被权限校验拒绝时，记录 WARNING 级别日志，
日志内容包含被拒绝的工具名称和当前允许的工具集合。

与属性测试互补，覆盖日志记录的具体示例场景。

**Validates: Requirements 5.5**
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.tools import ToolExecutionResult
from domain.agent.value_objects import AgentConfig
from domain.chat.context import ConversationContext, UserMessage
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.value_objects import LLMResponse, ToolCallRequest
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter
from test.infrastructure.agent._v3_stream_helpers import install_stream_mock


def _make_adapter() -> ReActAgentAdapter:
    """构造测试用 ReActAgentAdapter，使用 mock 依赖。

    tool_registry.execute 不应被调用（未授权工具不执行），
    context_builder.build 返回可直接用于模型调用的消息。

    Returns:
        配置了 mock 依赖的 ReActAgentAdapter 实例
    """
    tool_registry = MagicMock()
    tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(content="tool result"))
    context_builder = MagicMock()
    context_builder.build = AsyncMock(
        return_value=ContextBuilderResult(
            messages=[UserMessage(content="请执行操作")],
            usage={},
        )
    )
    return ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=context_builder,
    )


class TestPermissionDenialWarningLog:
    """权限拒绝 WARNING 日志测试。

    验证当 LLM 返回未授权工具调用时，ReActAgentAdapter 记录 WARNING 级别日志，
    日志内容包含被拒绝的工具名称和允许的工具集合。
    """

    @pytest.mark.asyncio
    async def test_warning_log_contains_denied_tool_and_allowed_set(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """权限拒绝时记录 WARNING 日志，包含被拒绝的工具名称和允许的工具集合。

        场景：
        - 第 1 轮：LLM 返回 tool_call 调用 "unauthorized_tool"（不在允许集合内）
        - 第 2 轮：LLM 返回纯文本回复

        验证：
        - 日志级别为 WARNING
        - 日志包含 "unauthorized_tool"（被拒绝的工具名）
        - 日志包含 "allowed_tool"（允许的工具名）

        **Validates: Requirements 5.5**
        """
        unauthorized_call = ToolCallRequest(
            id="tc1", name="unauthorized_tool", arguments='{"key": "value"}'
        )
        responses = [
            LLMResponse(
                content="",
                model="gpt-4",
                usage={"prompt_tokens": 10, "completion_tokens": 5},
                latency_ms=100.0,
                tool_calls=[unauthorized_call],
            ),
            LLMResponse(
                content="抱歉，该工具不可用",
                model="gpt-4",
                usage={"prompt_tokens": 20, "completion_tokens": 10},
                latency_ms=150.0,
                tool_calls=[],
            ),
        ]

        model_access = MagicMock()
        install_stream_mock(model_access, responses)

        adapter = _make_adapter()
        context = ConversationContext()
        context.add_user_message("请执行操作")

        config = AgentConfig(
            system_prompt="你是一个助手",
            tool_schemas=[
                {"type": "function", "function": {"name": "allowed_tool"}},
            ],
            model="gpt-4",
            max_rounds=10,
            prompt_id="chat-default@v1",
            allowed_tool_names=frozenset({"allowed_tool"}),
        )

        with caplog.at_level(logging.WARNING, logger="infrastructure.agent.react_agent_adapter"):
            await adapter.run(context, config, model_access)

        # 验证存在 WARNING 级别日志
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) >= 1, "应至少记录一条 WARNING 日志"

        # 验证日志内容包含被拒绝的工具名称和允许的工具集合
        warning_message = warning_records[0].getMessage()
        assert "unauthorized_tool" in warning_message, (
            f"WARNING 日志应包含被拒绝的工具名 'unauthorized_tool'，实际: {warning_message}"
        )
        assert "allowed_tool" in warning_message, (
            f"WARNING 日志应包含允许的工具名 'allowed_tool'，实际: {warning_message}"
        )
