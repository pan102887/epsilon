"""ChatServiceAdapter 测试装配工具。"""

from __future__ import annotations

from unittest.mock import MagicMock

from application.chat import ChatApplicationService, ChatSessionContextWorkflow
from domain.agent.ports import AgentPort, ApprovalStateStorePort
from domain.agent.segmented_execution import SegmentExecutionPolicy
from domain.agent.value_objects import AgentConfig
from domain.chat.ports import SessionContextStorePort, SessionIndexPort
from domain.model_access.ports import ModelAccessPort, ModelRegistryPort
from domain.prompt.value_objects import LoadedPrompt
from infrastructure.prompt.workspace_guidance import append_workspace_path_guidance


def make_chat_adapter_dependencies(
    *,
    session_store: SessionContextStorePort,
    model_registry: ModelRegistryPort,
    loaded_prompt: LoadedPrompt,
    agent: AgentPort | MagicMock,
    tool_schemas: list[dict],
    max_tool_rounds: int,
    approval_store: ApprovalStateStorePort | MagicMock | None = None,
    session_index: SessionIndexPort | MagicMock | None = None,
    segment_policy: SegmentExecutionPolicy | None = None,
) -> dict[str, object]:
    """为直接构造 ChatServiceAdapter 的测试创建显式 application 依赖。

    Args:
        session_store: 测试使用的会话存储。
        model_registry: 测试使用的模型注册表。
        loaded_prompt: prompt registry 将返回的 Prompt。
        agent: 测试使用的 AgentPort 或 mock。
        tool_schemas: 工具 schema 列表。
        max_tool_rounds: Agent 最大轮数。
        approval_store: 可选审批存储。
        session_index: 可选会话索引。
        segment_policy: 可选分段策略。

    Returns:
        可展开传给 ``ChatServiceAdapter`` 的 ``session_workflow`` 与
        ``chat_application_service`` 关键字参数。
    """

    system_prompt = append_workspace_path_guidance(loaded_prompt.content)
    effective_policy = segment_policy or SegmentExecutionPolicy()
    session_workflow = ChatSessionContextWorkflow(
        session_store=session_store,
        session_index=session_index,
        system_prompt=system_prompt,
        prompt_id=loaded_prompt.prompt_id,
    )

    def _resolve_model_access(model: str | None) -> tuple[ModelAccessPort, str]:
        """按测试模型注册表解析模型访问端口。"""

        if model is not None:
            return model_registry.get_adapter_for_model(model), model
        default_model = model_registry.get_default_model()
        return model_registry.get_adapter_for_model(default_model), default_model

    def _make_agent_config(model: str | None) -> AgentConfig:
        """构造测试用 AgentConfig。"""

        return AgentConfig(
            system_prompt=system_prompt,
            tool_schemas=tool_schemas,
            model=model,
            max_rounds=max_tool_rounds,
            prompt_id=loaded_prompt.prompt_id,
        )

    chat_application_service = ChatApplicationService(
        session_workflow=session_workflow,
        agent=agent,
        approval_store=approval_store,
        segment_policy=effective_policy,
        resolve_model_access=_resolve_model_access,
        make_agent_config=_make_agent_config,
    )
    return {
        "session_workflow": session_workflow,
        "chat_application_service": chat_application_service,
    }
