"""任务继续与审批恢复端口契约测试模块。"""

import inspect

from domain.task.ports import TaskAgentPort


def test_task_agent_port_continue_task_signature() -> None:
    """验证 TaskAgentPort.continue_task(...) 协议签名。"""
    signature = inspect.signature(TaskAgentPort.continue_task)

    assert list(signature.parameters) == ["self", "request"]
    assert (
        str(signature.parameters["request"].annotation).replace('"', "").replace("'", "")
        == "TaskContinueRequest"
    )
    assert str(signature.return_annotation).replace('"', "").replace("'", "") == "TaskResult"


def test_task_agent_port_resume_approval_signature() -> None:
    """验证 TaskAgentPort.resume_approval(...) 协议签名。"""
    signature = inspect.signature(TaskAgentPort.resume_approval)

    assert list(signature.parameters) == ["self", "request"]
    assert (
        str(signature.parameters["request"].annotation).replace('"', "").replace("'", "")
        == "TaskApprovalResumeRequest"
    )
    assert str(signature.return_annotation).replace('"', "").replace("'", "") == "TaskResult"
