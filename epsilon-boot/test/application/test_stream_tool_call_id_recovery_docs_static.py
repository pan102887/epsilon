"""流式工具调用 id 恢复文档静态检查测试。"""

from pathlib import Path


def test_stream_tool_call_id_recovery_docs_and_config_terms() -> None:
    """验证文档和 config.properties 覆盖策略、默认值和合成 id 前缀。"""
    root = Path(__file__).resolve().parents[2]
    docs = "\n".join(
        [
            (root.parent / "docs" / "agent.md").read_text(encoding="utf-8"),
            (root / "config.properties").read_text(encoding="utf-8"),
        ]
    )

    for term in [
        "MODEL_QWEN_STREAM_TOOL_CALL_ID_STRATEGY",
        "MODEL_ZHIPU_STREAM_TOOL_CALL_ID_STRATEGY",
        "MODEL_CLIPROXY_STREAM_TOOL_CALL_ID_STRATEGY",
        "MODEL_OPENAI_STREAM_TOOL_CALL_ID_STRATEGY",
        "recover",
        "raise",
        "call_synthetic_",
        "tool_call_id_recovered",
        "synthetic_tool_call_count",
        "source=stream_finished",
        "recovery_strategy=recover",
    ]:
        assert term in docs
