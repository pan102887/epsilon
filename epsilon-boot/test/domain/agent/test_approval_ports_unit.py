"""Agent 审批端口静态契约测试模块。"""

import inspect

from domain.agent.ports import AgentPort, ApprovalPolicyPort, ApprovalStateStorePort
from domain.agent.value_objects import ApprovalInterrupt, ApprovalInterruptSummary


def _annotation_text(annotation: object) -> str:
    """把签名注解转为稳定可断言的文本。"""
    return str(annotation).replace('"', "").replace("'", "")


def test_agent_port_resume_signature() -> None:
    """验证 AgentPort.resume(...) 参数与返回注解。"""
    signature = inspect.signature(AgentPort.resume)

    assert list(signature.parameters) == [
        "self",
        "context",
        "config",
        "model_access",
        "interrupt",
        "decisions",
    ]
    assert _annotation_text(signature.parameters["interrupt"].annotation) == "ApprovalInterrupt"
    assert _annotation_text(signature.parameters["decisions"].annotation) == (
        "tuple[ApprovalDecision, ...]"
    )
    assert _annotation_text(signature.return_annotation) == "AgentResult"


def test_approval_policy_port_signature() -> None:
    """验证 ApprovalPolicyPort.policy_for(...) 参数与返回注解。"""
    signature = inspect.signature(ApprovalPolicyPort.policy_for)

    assert list(signature.parameters) == ["self", "tool_name"]
    assert _annotation_text(signature.parameters["tool_name"].annotation) == "str"
    assert _annotation_text(signature.return_annotation) == "ApprovalPolicy"


def test_approval_state_store_consume_signature() -> None:
    """验证 ApprovalStateStorePort.consume(...) 参数与返回注解。"""
    signature = inspect.signature(ApprovalStateStorePort.consume)

    assert list(signature.parameters) == ["self", "session_id", "approval_id"]
    assert _annotation_text(signature.parameters["session_id"].annotation) == "str"
    assert _annotation_text(signature.parameters["approval_id"].annotation) == "str"
    assert _annotation_text(signature.return_annotation) == "ApprovalInterrupt | None"


def test_approval_state_store_list_pending_by_session_signature() -> None:
    """验证 ApprovalStateStorePort.list_pending_by_session(...) 参数与返回注解。"""
    signature = inspect.signature(ApprovalStateStorePort.list_pending_by_session)

    assert list(signature.parameters) == ["self", "session_id"]
    assert _annotation_text(signature.parameters["session_id"].annotation) == "str"
    assert _annotation_text(signature.return_annotation) == "list[ApprovalInterruptSummary]"


class DummyPolicyProvider:
    """用于验证策略端口形状的 dummy 实现。"""

    def policy_for(self, tool_name: str) -> None:
        """返回 None 即可，测试只验证协议方法可被调用。"""
        return None


class DummyStateStore:
    """用于验证状态存储端口形状的 dummy 实现。"""

    async def save(self, interrupt: ApprovalInterrupt) -> None:
        """保存 dummy 状态。"""
        return None

    async def load(self, session_id: str, approval_id: str) -> ApprovalInterrupt | None:
        """加载 dummy 状态。"""
        return None

    async def consume(self, session_id: str, approval_id: str) -> ApprovalInterrupt | None:
        """消费 dummy 状态。"""
        return None

    async def delete(self, session_id: str, approval_id: str) -> None:
        """删除 dummy 状态。"""
        return None

    async def delete_session(self, session_id: str) -> None:
        """删除 dummy 会话状态。"""
        return None

    async def list_pending_by_session(
        self, session_id: str
    ) -> list[ApprovalInterruptSummary]:
        """列出 dummy 会话审批摘要。"""
        return []


def test_dummy_classes_expose_required_methods() -> None:
    """验证 dummy class 具备新增端口方法。"""
    assert callable(DummyPolicyProvider().policy_for)
    store = DummyStateStore()
    assert callable(store.save)
    assert callable(store.load)
    assert callable(store.consume)
    assert callable(store.delete)
    assert callable(store.delete_session)
    assert callable(store.list_pending_by_session)
