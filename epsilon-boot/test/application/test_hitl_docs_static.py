"""HITL 文档静态检查测试。"""

from pathlib import Path


def test_hitl_docs_and_config_contain_required_terms() -> None:
    """验证 HITL 文档覆盖协议、默认策略和边界。"""
    root = Path(__file__).resolve().parents[2]
    docs = "\n".join(
        [
            (root.parent / "docs" / "agent.md").read_text(encoding="utf-8"),
            (root.parent / "docs" / "api.md").read_text(encoding="utf-8"),
            (root.parent / "docs" / "tools.md").read_text(encoding="utf-8"),
            (root / "config.properties").read_text(encoding="utf-8"),
        ]
    )

    for term in [
        "HITL_ENABLED",
        "approval_required",
        "/api/chat/sessions/{session_id}/approvals/{approval_id}/resume",
        "write_file",
        "edit_file",
        "shell_exec",
        "python_exec",
        "delegate_to_agent",
        "LangChain Deep Agents",
        "v1",
        "v2",
        "安全边界",
    ]:
        assert term in docs
