"""上下文构建领域依赖边界静态测试。"""

from pathlib import Path

DOMAIN_CHAT_FILES = (
    Path("src/domain/chat/ports.py"),
    Path("src/domain/chat/value_objects.py"),
)
FORBIDDEN_DEPENDENCY_MARKERS = (
    "infrastructure.",
    "pydantic_settings",
    "fastapi",
    "redis",
    "sqlalchemy",
    "openai",
)


def test_context_builder_domain_files_do_not_import_infrastructure_dependencies() -> None:
    """上下文构建领域契约不得依赖基础设施、框架或模型 SDK。"""
    project_root = Path(__file__).resolve().parents[3]

    for relative_path in DOMAIN_CHAT_FILES:
        source = (project_root / relative_path).read_text(encoding="utf-8")
        for marker in FORBIDDEN_DEPENDENCY_MARKERS:
            assert marker not in source, f"{relative_path} contains {marker!r}"
