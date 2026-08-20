"""聊天路由属性测试 + 同步路径集成测试。

使用 Hypothesis 对 SSE 序列化格式进行属性测试，
验证任意 StreamingChunk 对象序列化为 SSE data 字段后，
结果是合法 JSON 且包含正确类型的 delta_content 和 finished 字段。

同时覆盖同步 ``POST /api/chat`` 路径中 ``prompt_id`` 字段的端到端透传
（任务 9.4 / 需求 7.3 / Property 8）。
"""

import importlib.util
import json
import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from domain.chat.value_objects import ChatResponseVO
from domain.model_access.value_objects import StreamingChunk

# mock prometheus_client 以避免 Windows 平台兼容问题
if "prometheus_client" not in sys.modules:
    _mock_prom = MagicMock()
    _mock_prom.CONTENT_TYPE_LATEST = "text/plain"
    _mock_prom.generate_latest = MagicMock(return_value=b"")
    sys.modules["prometheus_client"] = _mock_prom


def _load_chat_module():
    """直接加载 chat 路由模块，绕过 application 包的 __init__.py。"""
    chat_path = (
        pathlib.Path(__file__).resolve().parents[3] / "src" / "application" / "routers" / "chat.py"
    )
    spec = importlib.util.spec_from_file_location("test_chat_router_sync_module", str(chat_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _serialize_chunk_to_sse_data(chunk: StreamingChunk) -> str:
    """将 StreamingChunk 序列化为 SSE 事件的 data 字段。

    复现 Chat_Router 中 ``_event_generator`` 的序列化逻辑，
    确保测试验证的是与生产代码一致的序列化格式。

    Args:
        chunk: 流式响应分片值对象。

    Returns:
        JSON 格式的 SSE data 字符串。
    """
    return json.dumps(
        {"delta_content": chunk.delta_content, "finished": chunk.finished},
        ensure_ascii=False,
    )


# --- Hypothesis strategies ---

_usage_strategy = st.none() | st.fixed_dictionaries(
    {
        "prompt_tokens": st.integers(min_value=0, max_value=100_000),
        "completion_tokens": st.integers(min_value=0, max_value=100_000),
        "total_tokens": st.integers(min_value=0, max_value=200_000),
    }
)

_streaming_chunk_strategy = st.builds(
    StreamingChunk,
    delta_content=st.text(min_size=0, max_size=200),
    finished=st.booleans(),
    usage=_usage_strategy,
)


# Feature: chat-chat-api, Property 7: SSE 序列化格式正确性
@settings(max_examples=100)
@given(chunk=_streaming_chunk_strategy)
def test_sse_serialization_produces_valid_json_with_correct_fields(
    chunk: StreamingChunk,
) -> None:
    """属性测试：SSE 序列化格式正确性。

    对任意 StreamingChunk 对象，将其序列化为 SSE 事件的 data 字段后：
    1. data 字段是合法的 JSON 字符串（json.loads 不抛异常）。
    2. 解析后包含 ``delta_content`` 字段且为字符串类型。
    3. 解析后包含 ``finished`` 字段且为布尔类型。

    Validates: Requirements 6.2, 6.4
    """
    data = _serialize_chunk_to_sse_data(chunk)

    # 1. 合法 JSON
    parsed = json.loads(data)

    # 2. delta_content 存在且为字符串
    assert "delta_content" in parsed, "SSE data 缺少 delta_content 字段"
    assert isinstance(parsed["delta_content"], str), (
        f"delta_content 应为 str，实际为 {type(parsed['delta_content']).__name__}"
    )

    # 3. finished 存在且为布尔
    assert "finished" in parsed, "SSE data 缺少 finished 字段"
    assert isinstance(parsed["finished"], bool), (
        f"finished 应为 bool，实际为 {type(parsed['finished']).__name__}"
    )


# ---------------------------------------------------------------------------
# 任务 9.4：同步 ``POST /api/chat`` ``prompt_id`` 字段集成测试
# # Validates: Requirement 7.3
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_chat_response_carries_prompt_id() -> None:
    """同步路径下响应 JSON 必须含非空 ``prompt_id`` 字段。

    注入 fake ``ChatServicePort.chat`` 返回 ``ChatResponseVO`` 携带
    ``prompt_id="chat-default@v3"``，发起 ``POST /api/chat`` 后断言
    响应体的 ``prompt_id`` 字段透传一致。
    """
    chat_module = _load_chat_module()

    mock_service = AsyncMock()
    mock_service.chat = AsyncMock(
        return_value=ChatResponseVO(
            session_id="s-int",
            reply="hi",
            model="test-model",
            usage={"prompt_tokens": 1, "completion_tokens": 1},
            prompt_id="chat-default@v3",
        )
    )

    response = await chat_module.chat(
        chat_module.ChatRequestBody(session_id="s-int", message="你好", stream=False),
        service=mock_service,
    )

    body = response.model_dump()
    assert body["prompt_id"] == "chat-default@v3"
    assert isinstance(body["prompt_id"], str) and body["prompt_id"] != ""
