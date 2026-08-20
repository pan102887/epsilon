"""上下文压缩 budget 命名静态防回归测试。"""

from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_compaction_production_files_do_not_use_budget_naming() -> None:
    """压缩相关生产文件不引入预算语义命名。"""
    paths = [
        _PROJECT_ROOT / "src/domain/chat/ports.py",
        _PROJECT_ROOT / "src/domain/chat/value_objects.py",
        _PROJECT_ROOT / "src/infrastructure/chat/chat_config.py",
        _PROJECT_ROOT / "src/infrastructure/chat/llm_summary_compaction_adapter.py",
        _PROJECT_ROOT / "config.properties",
    ]
    forbidden = [
        "COMPACTION_BUDGET",
        "budget_tokens",
        "compaction_budget",
    ]

    for path in paths:
        content = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in content, f"{path} 不应包含 {token}"
