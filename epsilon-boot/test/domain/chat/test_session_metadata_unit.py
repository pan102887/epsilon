"""会话元数据值对象单元测试模块。"""

import pytest

from domain.chat.value_objects import SessionMetadata


def test_session_metadata_accepts_valid_payload() -> None:
    """验证合法会话元数据可构造。"""
    metadata = SessionMetadata(
        session_id="tui-1",
        updated_at_epoch_ms=1000,
        message_count=2,
        preview="hello",
        created_at_epoch_ms=900,
        model="qwen3",
    )

    assert metadata.session_id == "tui-1"
    assert metadata.updated_at_epoch_ms == 1000
    assert metadata.message_count == 2
    assert metadata.preview == "hello"
    assert metadata.created_at_epoch_ms == 900
    assert metadata.model == "qwen3"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"session_id": ""},
        {"updated_at_epoch_ms": -1},
        {"updated_at_epoch_ms": 1.5},
        {"message_count": -1},
        {"message_count": 1.5},
        {"preview": ""},
        {"created_at_epoch_ms": -1},
        {"created_at_epoch_ms": 1.5},
    ],
)
def test_session_metadata_rejects_invalid_fields(kwargs: dict[str, object]) -> None:
    """验证非法会话元数据字段会被拒绝。"""
    payload: dict[str, object] = {
        "session_id": "tui-1",
        "updated_at_epoch_ms": 1000,
        "message_count": 1,
        "preview": "hello",
    }
    payload.update(kwargs)

    with pytest.raises(ValueError):
        SessionMetadata(**payload)  # type: ignore[arg-type]
