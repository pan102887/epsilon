"""``_execute_tool_call`` 元组返回与失败标记单元测试模块。

验证 v2 重构中 ``_execute_tool_call`` 返回类型由 ``str`` 升级为
``tuple[str, bool]``,以及失败时写入 ``ToolMessage.metadata["error"] = True``
的行为：

- (a) 工具成功 → 返回 ``(result, False)``,``ToolMessage.metadata == {}``,
  ``to_dict()`` 不含 ``metadata`` 键;
- (b) ``ToolPermissionDeniedError`` → 返回 ``(str(exc), True)``,
  ``ToolMessage.metadata == {"error": True}``,``_log_tool_failure`` 的
  ``reason="permission_denied"`` warning 字段集合不降级 (NFR-7);
- (c) 运行期 ``Exception`` → 同 (b),``reason="execution_error"``;
- (d) ``is_error=True`` 时 ``_stamp_event`` 仍写入 ``event_timestamps``。

覆盖需求 3.1, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9 与 NFR-7, Property 5。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.tools import ToolExecutionResult
from domain.agent.value_objects import AgentConfig
from domain.chat.context import ConversationContext, ToolMessage
from domain.model_access.value_objects import ToolCallRequest
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter


def _config(allowed: list[str]) -> AgentConfig:
    return AgentConfig(
        system_prompt="sys",
        tool_schemas=[
            {"type": "function", "function": {"name": name, "parameters": {}}} for name in allowed
        ],
        model="test-model",
        max_rounds=3,
        prompt_id="chat-default@v1",
    )


def _adapter_with_tool_result(result_value: str = "tool ok") -> ReActAgentAdapter:
    tool_registry = MagicMock()
    tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(content=result_value))
    return ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=MagicMock(),
    )


def _adapter_with_tool_exc(exc: Exception) -> ReActAgentAdapter:
    tool_registry = MagicMock()
    tool_registry.execute = AsyncMock(side_effect=exc)
    return ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=MagicMock(),
    )


def _tool_call() -> ToolCallRequest:
    return ToolCallRequest(id="call-1", name="echo", arguments='{"x": 1}')


class TestSuccessReturnsResultAndFalse:
    """工具成功 → 返回 ``(result, False)``,metadata 保持空。"""

    @pytest.mark.asyncio
    async def test_returns_tuple_with_false(self) -> None:
        adapter = _adapter_with_tool_result("ok-result")
        ctx = ConversationContext()
        cfg = _config(allowed=["echo"])

        result, is_error = await adapter.execute_tool_call_result(ctx, _tool_call(), cfg)

        assert isinstance(result, ToolExecutionResult)
        assert result.content == "ok-result"
        assert is_error is False

    @pytest.mark.asyncio
    async def test_tool_message_metadata_empty_on_success(self) -> None:
        adapter = _adapter_with_tool_result("ok")
        ctx = ConversationContext()
        cfg = _config(allowed=["echo"])

        await adapter.execute_tool_call_result(ctx, _tool_call(), cfg)

        last = ctx.get_messages()[-1]
        assert isinstance(last, ToolMessage)
        assert last.metadata == {}
        # to_dict 输出不含 metadata 键
        assert "metadata" not in last.to_dict()


class TestPermissionDeniedReturnsErrorTuple:
    """``ToolPermissionDeniedError`` → 返回 ``(str(exc), True)``,metadata 标记 error。"""

    @pytest.mark.asyncio
    async def test_returns_tuple_with_true(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        adapter = _adapter_with_tool_result("ok")
        # 把工具名换成不在 allowed_tool_names 的,触发 ToolPermissionDeniedError
        ctx = ConversationContext()
        cfg = _config(allowed=["other-tool"])
        bad_call = ToolCallRequest(id="call-x", name="echo", arguments="{}")

        with caplog.at_level(logging.WARNING):
            result, is_error = await adapter.execute_tool_call_result(ctx, bad_call, cfg)

        assert is_error is True
        assert result.content  # 非空 str(exc)
        assert result.metadata.get("error_class") == "ToolPermissionDeniedError"
        last = ctx.get_messages()[-1]
        assert isinstance(last, ToolMessage)
        assert last.metadata == {"error": True}
        # to_dict 输出 metadata: {"error": True}
        assert last.to_dict()["metadata"] == {"error": True}
        # warning 日志含 reason=permission_denied
        assert any("reason=permission_denied" in record.message for record in caplog.records)


class TestExecutionErrorReturnsErrorTuple:
    """运行期 ``Exception`` → 同 permission_denied 但 reason=execution_error。"""

    @pytest.mark.asyncio
    async def test_returns_tuple_with_true(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        adapter = _adapter_with_tool_exc(RuntimeError("model died"))
        ctx = ConversationContext()
        cfg = _config(allowed=["echo"])

        with caplog.at_level(logging.WARNING):
            result, is_error = await adapter.execute_tool_call_result(ctx, _tool_call(), cfg)

        assert is_error is True
        assert "model died" in result.content
        assert result.metadata.get("error_class") == "RuntimeError"
        last = ctx.get_messages()[-1]
        assert isinstance(last, ToolMessage)
        assert last.metadata == {"error": True}
        assert any("reason=execution_error" in record.message for record in caplog.records)


class TestStampEventOnFailure:
    """``is_error=True`` 时仍写入 ``event_timestamps``。"""

    @pytest.mark.asyncio
    async def test_event_timestamps_written_on_failure(self) -> None:
        adapter = _adapter_with_tool_exc(RuntimeError("boom"))
        ctx = ConversationContext()
        cfg = _config(allowed=["echo"])

        await adapter.execute_tool_call_result(ctx, _tool_call(), cfg)

        # 应当为新追加的 ToolMessage(索引 0)写入时间戳
        assert 0 in ctx.event_timestamps
        assert ctx.event_timestamps[0] > 0


class TestLogToolFailureFieldsNotDegraded:
    """NFR-7: ``_log_tool_failure`` warning 字段集合不降级。"""

    @pytest.mark.asyncio
    async def test_log_tool_failure_includes_required_fields(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        adapter = _adapter_with_tool_exc(ValueError("bad"))
        ctx = ConversationContext()
        cfg = _config(allowed=["echo"])

        with caplog.at_level(logging.WARNING):
            await adapter.execute_tool_call_result(ctx, _tool_call(), cfg)

        # 至少 1 条 warning 日志
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert warnings
        msg = warnings[-1].message
        # 必须包含字段标签: tool_name / tool_call_id / reason / exc_type / exc_msg
        assert "tool_name=echo" in msg
        assert "tool_call_id=call-1" in msg
        assert "reason=execution_error" in msg
        assert "exc_type=ValueError" in msg
        assert "exc_msg=bad" in msg
