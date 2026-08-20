"""TaskAgentAdapter 单元测试模块。

验证 TaskAgentAdapter 的核心场景，包括：
- Protocol 协议合规性
- 无 session_id 执行成功
- 有 session_id 执行成功（load/save 调用）
- 异常处理返回 FAILED
- 执行轨迹提取
- build_system_prompt 仅 goal 场景
- build_system_prompt 全字段场景
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.value_objects import AgentResult
from domain.chat.context import AssistantMessage, ConversationContext, ToolMessage
from domain.model_access.value_objects import ToolCallRequest
from domain.prompt.value_objects import LoadedPrompt
from domain.task.value_objects import Task, TaskStatus
from infrastructure.task.task_agent_adapter import TaskAgentAdapter


def _create_adapter(agent_result=None, agent_exception=None):
    """创建带有 mock 依赖的 TaskAgentAdapter 实例。

    Args:
        agent_result: AgentPort.run() 的返回值，默认返回简单成功结果
        agent_exception: AgentPort.run() 抛出的异常，优先于 agent_result

    Returns:
        (adapter, agent_mock, session_store_mock) 三元组
    """
    agent = AsyncMock()
    if agent_exception:
        agent.run.side_effect = agent_exception
    else:
        agent.run.return_value = agent_result or AgentResult(content="ok", model="test-model")

    tool_registry = MagicMock()
    tool_registry.get_schemas.return_value = []

    model_registry = MagicMock()
    model_access = AsyncMock()
    model_registry.get_adapter_for_model.return_value = model_access
    model_registry.get_default_model.return_value = "default-model"

    compaction = MagicMock()

    session_store = AsyncMock()
    session_store.load.return_value = ConversationContext()

    adapter = TaskAgentAdapter(
        agent=agent,
        tool_registry=tool_registry,
        model_registry=model_registry,
        compaction=compaction,
        session_store=session_store,
        prompt_registry=MagicMock(
            get=MagicMock(
                return_value=LoadedPrompt(
                    prompt_id="task-template@v1",
                    name="task-template",
                    version="v1",
                    content="骨架",
                )
            )
        ),
    )
    return adapter, agent, session_store


class TestTaskAgentAdapter:
    """TaskAgentAdapter 核心场景单元测试。"""

    def test_task_agent_port_protocol_compliance(self) -> None:
        """验证 TaskAgentAdapter 满足 TaskAgentPort Protocol。

        通过结构检查确认 TaskAgentAdapter 具有 execute 异步方法，
        符合 TaskAgentPort 协议定义的接口签名。
        """
        adapter, _, _ = _create_adapter()
        # TaskAgentPort 未标记 @runtime_checkable，使用结构检查
        assert hasattr(adapter, "execute"), "TaskAgentAdapter 应具有 execute 方法"
        assert callable(adapter.execute), "execute 应为可调用对象"
        # 验证 execute 是协程函数（async def）
        import asyncio

        assert asyncio.iscoroutinefunction(adapter.execute), "execute 应为异步方法"

    @pytest.mark.asyncio
    async def test_execute_without_session_id_success(self) -> None:
        """验证无 session_id 时执行成功：不调用 save，TaskResult.status == SUCCESS。"""
        agent_result = AgentResult(
            content="分析完成",
            model="gpt-4",
            usage={"total_tokens": 100},
        )
        adapter, _agent, session_store = _create_adapter(agent_result=agent_result)
        task = Task(goal="分析数据")

        result = await adapter.execute(task)

        assert result.status == TaskStatus.SUCCESS
        assert result.content == "分析完成"
        assert result.model == "gpt-4"
        assert result.usage == {"total_tokens": 100}
        session_store.save.assert_not_called()

    @pytest.mark.asyncio
    async def test_execute_with_session_id_success(self) -> None:
        """验证有 session_id 时执行成功：调用 load 和 save，session_id 正确。"""
        agent_result = AgentResult(content="完成", model="gpt-4")
        adapter, _agent, session_store = _create_adapter(agent_result=agent_result)
        task = Task(goal="继续分析", session_id="sess-123")

        result = await adapter.execute(task)

        assert result.status == TaskStatus.SUCCESS
        session_store.load.assert_called_once_with("sess-123")
        session_store.save.assert_called_once()
        saved_session_id = session_store.save.call_args[0][0]
        assert saved_session_id == "sess-123"

    @pytest.mark.asyncio
    async def test_execute_exception_returns_failed(self) -> None:
        """验证 AgentPort.run() 抛出异常时返回 FAILED，content 为异常信息。"""
        adapter, _agent, _session_store = _create_adapter(
            agent_exception=RuntimeError("模型调用失败"),
        )
        task = Task(goal="执行任务")

        result = await adapter.execute(task)

        assert result.status == TaskStatus.FAILED
        assert result.content == "模型调用失败"

    def test_extract_trace_with_tool_calls(self) -> None:
        """验证 _extract_trace 正确提取含 tool_calls 的执行轨迹。

        构造包含 AssistantMessage（带 tool_calls）和 ToolMessage 的消息序列，
        验证提取的 TraceEntry 列表中 action 类型和 step 编号正确。
        """
        adapter, _, _ = _create_adapter()
        messages = [
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCallRequest(id="call_1", name="read_file", arguments='{"path": "/tmp"}'),
                ],
            ),
            ToolMessage(content="文件内容", tool_name="read_file", tool_call_id="call_1"),
        ]

        trace = adapter._extract_trace(messages, start_index=0)

        assert len(trace) == 2
        assert trace[0].step == 1
        assert trace[0].action == "tool_call"
        assert "read_file" in trace[0].detail
        assert trace[1].step == 2
        assert trace[1].action == "tool_result"
        assert trace[1].detail == "文件内容"

    def test_build_system_prompt_goal_only(self) -> None:
        """验证仅有 goal 时，提示词包含 goal 但不包含其他段落标题。"""
        task = Task(goal="请分析这段代码")

        prompt = TaskAgentAdapter.build_system_prompt(task)

        assert "请分析这段代码" in prompt
        assert "Input Data" not in prompt
        assert "Constraints" not in prompt
        assert "Expected Output Format" not in prompt

    @pytest.mark.asyncio
    async def test_system_message_idempotent_on_session_reuse(self) -> None:
        """验证会话复用时不重复追加 SystemMessage。

        Given：``ConversationContext`` 中已有一条 SystemMessage（与本次
        ``build_system_prompt`` 生成内容一致）；
        When：``execute`` 被再次调用；
        Then：上下文中 SystemMessage 数量保持 1。
        """
        agent_result = AgentResult(content="ok", model="gpt-4")
        adapter, _, session_store = _create_adapter(agent_result=agent_result)
        existing_ctx = ConversationContext()
        existing_ctx.add_system_message("分析数据")
        session_store.load.return_value = existing_ctx

        task = Task(goal="分析数据", session_id="sess-1")
        await adapter.execute(task)

        system_msgs = [m for m in existing_ctx.get_messages() if m.role == "system"]
        assert len(system_msgs) == 1
        assert system_msgs[0].content == "分析数据"

    @pytest.mark.asyncio
    async def test_system_message_mismatch_logs_info_no_append(self, caplog) -> None:
        """验证既有 SystemMessage 与本次内容不一致时仅产生 info 日志、不追加。"""
        import logging

        agent_result = AgentResult(content="ok", model="gpt-4")
        adapter, _, session_store = _create_adapter(agent_result=agent_result)
        existing_ctx = ConversationContext()
        existing_ctx.add_system_message("旧的系统提示")
        session_store.load.return_value = existing_ctx

        task = Task(goal="新的目标", session_id="sess-1")
        with caplog.at_level(logging.INFO):
            await adapter.execute(task)

        system_msgs = [m for m in existing_ctx.get_messages() if m.role == "system"]
        assert len(system_msgs) == 1
        assert system_msgs[0].content == "旧的系统提示"
        assert any("复用既有 system 消息" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_system_message_appended_on_fresh_context(self) -> None:
        """验证首次新建 context 时正常追加 SystemMessage。"""
        agent_result = AgentResult(content="ok", model="gpt-4")
        adapter, _, session_store = _create_adapter(agent_result=agent_result)

        task = Task(goal="目标 X")  # 无 session_id → 新建 context
        await adapter.execute(task)

        # 通过 session_store.save 未被调用确认走了新建 context 路径
        session_store.save.assert_not_called()

    def test_extract_trace_uses_event_timestamps_when_available(self) -> None:
        """验证 _extract_trace 优先取 event_timestamps 中的事件时刻。

        Given：``messages`` 中包含 1 条 AssistantMessage（带 tool_calls）
        和 1 条 ToolMessage；提供 ``event_timestamps`` 显式指定两条消息
        的事件时刻；
        When：调用 ``_extract_trace`` 并传入 ``event_timestamps``；
        Then：返回的 ``TraceEntry.timestamp_ms`` 与 ``event_timestamps``
        一致，不取 ``time.time()`` 的提取时刻。
        """
        adapter, _, _ = _create_adapter()
        messages = [
            AssistantMessage(
                content="",
                tool_calls=[
                    ToolCallRequest(id="call_1", name="read_file", arguments='{"path": "/tmp"}'),
                ],
            ),
            ToolMessage(content="文件内容", tool_name="read_file", tool_call_id="call_1"),
        ]
        event_timestamps = {0: 1_000_000, 1: 1_000_500}

        trace = adapter._extract_trace(
            messages,
            start_index=0,
            event_timestamps=event_timestamps,
        )

        assert trace[0].timestamp_ms == 1_000_000
        assert trace[1].timestamp_ms == 1_000_500

    def test_extract_trace_falls_back_to_now_when_stamp_missing(self) -> None:
        """验证 _extract_trace 在缺少事件时刻映射时回退到 time.time()。

        Given：``event_timestamps`` 为空；
        When：调用 ``_extract_trace``；
        Then：``timestamp_ms`` 取自 ``int(time.time() * 1000)``，正常返回
        非零整数。
        """
        adapter, _, _ = _create_adapter()
        messages = [
            ToolMessage(content="结果", tool_name="t", tool_call_id="id"),
        ]

        trace = adapter._extract_trace(messages, start_index=0, event_timestamps={})

        assert len(trace) == 1
        assert isinstance(trace[0].timestamp_ms, int)
        assert trace[0].timestamp_ms > 0

    @pytest.mark.asyncio
    async def test_execute_reads_event_timestamps_via_promoted_field(self, monkeypatch) -> None:
        """验证 execute() 通过 ``context.event_timestamps`` 正式字段读取事件时刻。

        v2 把 ``event_timestamps`` 升级为 ConversationContext 的正式字段后,
        ``TaskAgentAdapter.execute`` 不再使用 ``getattr(context,
        "_event_timestamps", {}) or {}``,而是直接读取 ``context.event_timestamps``。

        Given：``context`` 在 Agent.run() 期间向 ``event_timestamps`` 写入
        了消息索引 → 毫秒整数的映射;
        When：``execute`` 完成后构造 TraceEntry;
        Then：``Trace_Entry.timestamp_ms`` 等于事件发生时刻(由
        ``event_timestamps`` 直接读取),与 v1 通过 ``getattr`` 读取语义等价。
        """
        agent_result = AgentResult(content="done", model="gpt-4")
        adapter, agent, session_store = _create_adapter(agent_result=agent_result)

        # 注入一个 context, 在 agent.run() 内追加一条 ToolMessage 并直接写入正式字段
        injected_ctx = ConversationContext()

        async def _fake_run(context, _config, _model_access):
            context.add_assistant_message_with_tool_calls(
                content="",
                tool_calls=[
                    ToolCallRequest(id="call_1", name="echo", arguments="{}"),
                ],
            )
            # build_system_prompt 之后 system_msg 占索引 0,user_msg 占索引 1,
            # 因此 assistant 索引 = 2, tool 索引 = 3
            context.add_tool_result(tool_name="echo", result="done", tool_call_id="call_1")
            # 直接写入正式字段(模拟 _stamp_event 的行为)
            context.event_timestamps[2] = 1_717_000_000_000
            context.event_timestamps[3] = 1_717_000_000_500
            return agent_result

        agent.run = _fake_run
        session_store.load.return_value = injected_ctx

        task = Task(goal="测试时间戳读取", session_id="sess-trace")
        result = await adapter.execute(task)

        assert result.status == TaskStatus.SUCCESS
        # Trace 时间戳应取自正式字段写入的事件时刻
        assert len(result.trace) == 2
        assert result.trace[0].timestamp_ms == 1_717_000_000_000
        assert result.trace[1].timestamp_ms == 1_717_000_000_500

    def test_build_system_prompt_all_fields(self) -> None:
        """验证全字段 Task 时，提示词包含 goal、input_data JSON、每条约束和 output_format。"""
        task = Task(
            goal="分析用户行为",
            input_data={"user_id": "u001", "action": "click"},
            constraints=["不超过500字", "使用中文"],
            output_format="JSON格式",
        )

        prompt = TaskAgentAdapter.build_system_prompt(task)

        assert "分析用户行为" in prompt
        input_json = json.dumps(
            {"user_id": "u001", "action": "click"}, ensure_ascii=False, indent=2
        )
        assert input_json in prompt
        assert "不超过500字" in prompt
        assert "使用中文" in prompt
        assert "JSON格式" in prompt
