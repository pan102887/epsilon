"""聊天路由 HITL HTTP 测试模块。"""

import importlib.util
import pathlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.value_objects import PendingActionRequest
from domain.chat.value_objects import ChatResponseVO


def _load_chat_module():
    """直接加载 chat 路由模块。"""
    chat_path = pathlib.Path(__file__).resolve().parents[3] / "src/application/routers/chat.py"
    spec = importlib.util.spec_from_file_location("test_chat_router_hitl_module", str(chat_path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_sync_chat_returns_approval_required() -> None:
    """验证同步 chat approval_required 响应字段。"""
    module = _load_chat_module()
    service = MagicMock()
    service.chat = AsyncMock(
        return_value=ChatResponseVO(
            session_id="s1",
            reply="",
            model="gpt-test",
            usage={},
            prompt_id="chat-default@v1",
            status="approval_required",
            approval_id="a1",
            action_requests=(
                PendingActionRequest("call-1", "write_file", "{}", frozenset({"approve"})),
            ),
        )
    )

    response = await module.chat(
        module.ChatRequestBody(session_id="s1", message="hi"),
        service=service,
    )

    body = response.model_dump()
    assert body["status"] == "approval_required"
    assert body["approval_id"] == "a1"
    assert body["action_requests"][0]["tool_call_id"] == "call-1"
    assert body["terminated_reason"] == "completed"
    assert body["can_continue"] is False


@pytest.mark.asyncio
async def test_resume_completed_returns_completed() -> None:
    """验证 resume completed 响应字段。"""
    module = _load_chat_module()
    service = MagicMock()
    service.resume_approval = AsyncMock(
        return_value=ChatResponseVO(
            session_id="s1",
            reply="done",
            model="gpt-test",
            usage={},
            prompt_id="chat-default@v1",
        )
    )

    approval_resume_request_body = module.resume_approval.__globals__[
        "ApprovalResumeRequestBody"
    ]
    approval_decision_body = module.resume_approval.__globals__["ApprovalDecisionBody"]

    response = await module.resume_approval(
        "s1",
        "a1",
        approval_resume_request_body(
            decisions=[approval_decision_body(type="approve", tool_call_id="call-1")]
        ),
        service=service,
    )

    body = response.model_dump()
    assert body["status"] == "completed"
    assert body["reply"] == "done"
    assert body["terminated_reason"] == "completed"
    assert body["can_continue"] is False
