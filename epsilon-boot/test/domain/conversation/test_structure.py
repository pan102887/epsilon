"""会话领域模块结构和类存在性验证测试。

验证 chat 限界上下文的模块结构完整性，包括：
- 目录包含预期的三个文件（__init__.py、context.py、ports.py）
- Message 和 ConversationContext 可从 domain.chat.context 导入
- SessionContextStorePort 可从 domain.chat.ports 导入且包含预期方法
"""

from pathlib import Path

_CONVERSATION_DOMAIN_DIR = Path(__file__).resolve().parents[3] / "src" / "domain" / "chat"


class TestConversationModuleStructure:
    """验证 chat 领域模块的目录结构完整性。"""

    def test_conversation_dir_contains_init(self) -> None:
        """验证 chat 领域目录包含 __init__.py。"""
        assert (_CONVERSATION_DOMAIN_DIR / "__init__.py").exists()

    def test_conversation_dir_contains_context(self) -> None:
        """验证 chat 领域目录包含 context.py。"""
        assert (_CONVERSATION_DOMAIN_DIR / "context.py").exists()

    def test_conversation_dir_contains_ports(self) -> None:
        """验证 chat 领域目录包含 ports.py。"""
        assert (_CONVERSATION_DOMAIN_DIR / "ports.py").exists()


class TestConversationClassImportability:
    """验证 chat 领域中的核心类可正常导入。"""

    def test_message_importable(self) -> None:
        """验证 Message 可从 domain.chat.context 导入。"""
        from domain.chat.context import Message

        assert Message is not None

    def test_conversation_context_importable(self) -> None:
        """验证 ConversationContext 可从 domain.chat.context 导入。"""
        from domain.chat.context import ConversationContext

        assert ConversationContext is not None

    def test_session_context_store_port_importable(self) -> None:
        """验证 SessionContextStorePort 可从 domain.chat.ports 导入。"""
        from domain.chat.ports import SessionContextStorePort

        assert SessionContextStorePort is not None

    def test_session_context_store_port_has_save_load_delete_exists(self) -> None:
        """验证 SessionContextStorePort 包含 save/load/delete/exists 方法。"""
        from domain.chat.ports import SessionContextStorePort

        for method_name in ("save", "load", "delete", "exists"):
            assert hasattr(SessionContextStorePort, method_name), (
                f"SessionContextStorePort 缺少 {method_name} 方法"
            )
