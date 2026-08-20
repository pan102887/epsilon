"""ReActAgentAdapter 终止原因正交性特征化测试模块。

本模块锁定的对外可观测行为面为「(a) 终止四态之 ``completed``」，性质为
characterization（回归基线）——只照 ``ReActAgentAdapter`` 当前实际返回值写断言，
不表达任何"理想应有"行为。补齐既有 ``test_react_agent_adapter_unit.py::
test_returns_correct_agent_result`` 未显式断言 ``terminated_reason`` 的缺口 G1：

- ``test_run_plain_text_completed_orthogonal``：单轮纯文本自然收尾时，
  ``AgentRunStatus`` 与 ``AgentTerminationReason`` 二者同为 ``"completed"``，
  锁定 ``status`` 与 ``terminated_reason`` 的正交关系。
- ``test_run_tool_loop_natural_completion``：``[tool_calls, text]`` 两轮正常
  收尾时，``terminated_reason == "completed"``，锁定工具循环正常收尾亦判为
  ``completed``（``_iter_rounds`` text 分支）。

harness 复用既有 ``_v3_stream_helpers.install_stream_mock`` + 全程 stream 的
fake ``ModelAccessPort`` 语义；上下文构建器为本文件内 ``_FakeContextBuilder``
（原样透传领域消息、空 usage），不引入新替身以免与既有断言语义分歧。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from domain.agent.tools import ToolExecutionResult
from domain.agent.value_objects import AgentConfig
from domain.chat.context import BaseMessage, ConversationContext
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.value_objects import LLMResponse, ToolCallRequest
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter
from test.infrastructure.agent._v3_stream_helpers import install_stream_mock


class _FakeContextBuilder:
    """测试用上下文构建器：原样透传领域消息列表，usage 为空。"""

    async def build(
        self,
        messages: list[BaseMessage],
        **kwargs: object,
    ) -> ContextBuilderResult:
        """原样透传领域消息列表并返回空 usage。"""
        return ContextBuilderResult(messages=messages, usage={})


def _config() -> AgentConfig:
    """构造无工具审批约束的最小 Agent 配置（max_rounds=3）。"""
    return AgentConfig(
        system_prompt="system",
        tool_schemas=[{"type": "function", "function": {"name": "echo"}}],
        model="gpt-test",
        max_rounds=3,
        prompt_id="chat-default@v1",
    )


def _adapter() -> ReActAgentAdapter:
    """构造仅装配 ``echo`` 工具的适配器，工具执行返回固定内容。"""

    class _EchoTool:
        """记录调用并返回固定内容的测试工具。"""

        @property
        def name(self) -> str:
            return "echo"

        @property
        def description(self) -> str:
            return "echo tool"

        @property
        def parameters(self) -> dict[str, object]:
            return {"type": "object", "properties": {}, "required": []}

        def cast_params(self, params: dict[str, object]) -> dict[str, object]:
            return params

        def validate_params(self, params: dict[str, object]) -> list[str]:
            return []

        async def execute(self, **kwargs: object) -> ToolExecutionResult:
            return ToolExecutionResult(content="echoed")

    registry = MagicMock()
    registry.get.return_value = _EchoTool()
    return ReActAgentAdapter(
        tool_registry=registry,
        context_builder=_FakeContextBuilder(),  # type: ignore[arg-type]
    )


async def test_run_plain_text_completed_orthogonal() -> None:
    """锁定单轮纯文本自然收尾：status 与 terminated_reason 同为 completed。

    单轮模型直接返回纯文本 ``LLMResponse(content="ok", tool_calls=[])``，
    ``run`` 应返回 ``status == "completed"`` **且** ``terminated_reason ==
    "completed"``、``content == "ok"``，据实锁定 ``AgentRunStatus`` 与
    ``AgentTerminationReason`` 二者正交且纯文本自然收尾同为 completed。
    """
    adapter = _adapter()
    model = MagicMock()
    install_stream_mock(
        model,
        [LLMResponse(content="ok", model="gpt-test", usage={"total_tokens": 3}, tool_calls=[])],
    )
    context = ConversationContext()
    context.add_user_message("hi")

    result = await adapter.run(context, _config(), model)

    assert result.status == "completed"
    assert result.terminated_reason == "completed"
    assert result.content == "ok"


async def test_run_tool_loop_natural_completion() -> None:
    """锁定 [tool_calls, text] 两轮正常收尾：terminated_reason == completed。

    第一轮返回 ``echo`` 工具调用（工具被执行、结果回灌），第二轮返回纯文本
    ``"done"``。``run`` 应据实返回 ``terminated_reason == "completed"``、
    ``content == "done"``，锁定工具循环正常收尾亦判为 completed
    （``_iter_rounds`` text 分支）。
    """
    adapter = _adapter()
    model = MagicMock()
    install_stream_mock(
        model,
        [
            LLMResponse(
                content="",
                model="gpt-test",
                usage={"total_tokens": 2},
                tool_calls=[ToolCallRequest("call-1", "echo", "{}")],
            ),
            LLMResponse(content="done", model="gpt-test", usage={"total_tokens": 4}),
        ],
    )
    context = ConversationContext()
    context.add_user_message("please echo")

    result = await adapter.run(context, _config(), model)

    assert result.terminated_reason == "completed"
    assert result.content == "done"
