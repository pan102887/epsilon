"""聊天响应分段元数据单元测试。"""

from __future__ import annotations

from domain.agent.segmented_execution import SegmentRunMetadata
from domain.chat.value_objects import ChatResponseVO


def test_chat_response_default_segment_metadata() -> None:
    """ChatResponseVO 默认携带单段 completed 元数据。"""
    response = ChatResponseVO(
        session_id="s1",
        reply="ok",
        model="m",
        usage={},
        prompt_id="chat-default@v1",
    )

    assert response.segment_metadata.segment_index == 1
    assert response.segment_metadata.segment_count == 1
    assert response.segment_metadata.segment_stop_reason == "completed"


def test_chat_response_accepts_explicit_segment_metadata() -> None:
    """ChatResponseVO 可携带显式分段元数据。"""
    metadata = SegmentRunMetadata(
        segment_index=1,
        segment_count=1,
        segment_stop_reason="auto_disabled",
    )
    response = ChatResponseVO(
        session_id="s1",
        reply="",
        model="m",
        usage={},
        prompt_id="chat-default@v1",
        status="paused",
        terminated_reason="max_rounds",
        can_continue=True,
        segment_metadata=metadata,
    )

    assert response.segment_metadata is metadata
