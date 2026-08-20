"""会话领域模块依赖方向属性测试。

使用 AST 解析 chat 领域模块和 RedisSessionContextAdapter 的 import 语句，
验证依赖方向符合六边形架构约束：
- chat 领域不依赖 infrastructure
- RedisSessionContextAdapter 的领域依赖仅指向 chat
"""

import ast
from pathlib import Path

import hypothesis.strategies as st
from hypothesis import given, settings

# chat 领域模块文件路径
_CONVERSATION_DOMAIN_DIR = Path(__file__).resolve().parents[3] / "src" / "domain" / "chat"
_CONVERSATION_MODULE_FILES = ["context.py", "ports.py"]

# RedisSessionContextAdapter 文件路径
_REDIS_ADAPTER_PATH = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "infrastructure"
    / "session"
    / "redis_session_context_adapter.py"
)


def _extract_import_modules(file_path: Path) -> list[str]:
    """从 Python 源文件中提取所有 import 语句的模块路径。

    解析 AST，收集 import 和 from...import 语句中的模块名称。
    对于 TYPE_CHECKING 块内的 import 同样会被收集。

    Args:
        file_path: Python 源文件路径

    Returns:
        模块路径字符串列表，例如 ["domain.chat.context", "json"]
    """
    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(file_path))

    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return modules


# ── Property 3: Conversation 领域模块独立性 ──
# Feature: chat-bounded-context, Property 3: Conversation 领域模块独立性


# 生成策略：从 chat 领域模块文件中随机选取
_conversation_file_st = st.sampled_from(_CONVERSATION_MODULE_FILES)


@settings(max_examples=100)
@given(filename=_conversation_file_st)
def test_conversation_domain_no_infrastructure_dependency(filename: str) -> None:
    """验证 chat 领域模块不依赖 infrastructure 层。

    对于 chat 领域中的任意模块文件，其 import 语句
    不得包含 'infrastructure' 路径，确保领域层不依赖基础设施层。
    """
    file_path = _CONVERSATION_DOMAIN_DIR / filename
    assert file_path.exists(), f"模块文件不存在: {file_path}"

    modules = _extract_import_modules(file_path)
    for module in modules:
        assert "infrastructure" not in module, (
            f"{filename} 中存在对 infrastructure 的依赖: {module}"
        )


# ── Property 4: RedisSessionContextAdapter 依赖方向正确性 ──
# Feature: chat-bounded-context, Property 4: RedisSessionContextAdapter 依赖方向正确性


def test_redis_adapter_domain_imports_point_to_conversation() -> None:
    """验证 RedisSessionContextAdapter 的 domain import 指向 chat 领域。

    解析 RedisSessionContextAdapter 的所有 import 语句，对于引用 domain 层的
    import，验证其路径指向 domain.chat，确保适配器仅依赖会话管理限界上下文。
    """
    assert _REDIS_ADAPTER_PATH.exists(), (
        f"RedisSessionContextAdapter 文件不存在: {_REDIS_ADAPTER_PATH}"
    )

    modules = _extract_import_modules(_REDIS_ADAPTER_PATH)
    domain_imports = [m for m in modules if m.startswith("domain.")]

    # 至少应有一个 domain import（引用 ConversationContext）
    assert len(domain_imports) > 0, "RedisSessionContextAdapter 应至少引用一个 domain 模块"

    for module in domain_imports:
        assert "domain.chat" in module, (
            f"RedisSessionContextAdapter 的 domain import 应指向 chat 领域，但发现: {module}"
        )
