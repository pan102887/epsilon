"""StreamingChunk metadata 兼容测试模块。"""

from domain.model_access.value_objects import StreamingChunk


def test_streaming_chunk_metadata_defaults_to_empty_dict() -> None:
    """验证 metadata 默认是空 dict。"""
    chunk = StreamingChunk()

    assert chunk.metadata == {}


def test_streaming_chunk_old_construction_remains_compatible() -> None:
    """验证旧构造方式仍可使用。"""
    chunk = StreamingChunk(
        delta_content="hello",
        finished=True,
        usage={"total_tokens": 1},
    )

    assert chunk.delta_content == "hello"
    assert chunk.finished is True
    assert chunk.usage == {"total_tokens": 1}
    assert chunk.metadata == {}
