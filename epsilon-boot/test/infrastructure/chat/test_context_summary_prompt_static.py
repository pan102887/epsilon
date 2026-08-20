"""上下文摘要 Prompt 静态防回归测试。"""

from pathlib import Path


def test_llm_summary_adapter_does_not_embed_full_prompt_columns() -> None:
    """生产适配器代码不硬编码摘要 Prompt 栏目正文。"""
    source = Path("src/infrastructure/chat/llm_summary_compaction_adapter.py").read_text(
        encoding="utf-8"
    )

    embedded_columns = ["当前目标", "关键命令与结果", "错误与阻塞"]
    assert not all(column in source for column in embedded_columns)


def test_llm_summary_adapter_loads_context_summary_prompt() -> None:
    """LLMSummaryCompactionAdapter 构造期通过 registry 加载 context-summary。"""
    source = Path("src/infrastructure/chat/llm_summary_compaction_adapter.py").read_text(
        encoding="utf-8"
    )

    assert 'prompt_registry.get("context-summary")' in source
