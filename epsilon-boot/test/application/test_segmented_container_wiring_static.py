"""阶段二分段执行容器装配静态测试。"""

from __future__ import annotations

import inspect
from pathlib import Path

from domain.agent.segmented_execution import SegmentExecutionPolicy
from domain.chat.ports import ChatServicePort
from infrastructure.chat.chat_service_adapter import ChatServiceAdapter
from infrastructure.task.task_agent_adapter import TaskAgentAdapter

_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_container_passes_segment_policy_to_chat_and_task_adapters() -> None:
    """组合根必须把配置映射出的分段策略传入 Chat 应用服务与 adapters。"""
    source_paths = [
        _REPO_ROOT / "epsilon-boot/src/application/container_config.py",
        *_REPO_ROOT.glob("epsilon-boot/src/application/container/*.py"),
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_paths)

    assert "segment_policy = chat_config.to_segment_policy()" in source
    assert "ChatSessionContextWorkflow(" in source
    assert "ChatApplicationService(" in source
    assert "segment_policy=segment_policy" in source
    assert "session_workflow=session_workflow" in source
    assert "chat_application_service=cast(Any, chat_application_service)" in source
    assert "task_agent_config.to_segment_policy()" in source


def test_chat_service_port_declares_segmented_stream_methods() -> None:
    """ChatServicePort 暴露结构化分段流接口，保持既有接口不变。"""
    assert hasattr(ChatServicePort, "stream_segmented_chat_events")
    assert hasattr(ChatServicePort, "stream_segmented_continue_chat_events")


def test_chat_and_task_adapters_accept_segment_policy() -> None:
    """Chat/Task adapters 构造函数必须接受可选 SegmentExecutionPolicy。"""
    chat_param = inspect.signature(ChatServiceAdapter).parameters["segment_policy"]
    task_param = inspect.signature(TaskAgentAdapter).parameters["segment_policy"]

    assert chat_param.default is None
    assert task_param.default is None
    assert "SegmentExecutionPolicy" in str(chat_param.annotation)
    assert "SegmentExecutionPolicy" in str(task_param.annotation)


def test_default_segment_policy_remains_disabled() -> None:
    """构造层默认策略保持关闭自动续跑。"""
    policy = SegmentExecutionPolicy()

    assert policy.auto_continue_enabled is False
