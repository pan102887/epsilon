"""Agent 工具安全护栏静态策略测试。"""

from pathlib import Path

BOOT_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BOOT_ROOT.parent
SECURITY_DOC = REPO_ROOT / "docs" / "security" / "agent-tool-guardrails.md"
SHELL_TOOL = BOOT_ROOT / "src" / "infrastructure" / "tools" / "shell_exec" / "shell_exec_tool.py"
HTTP_TOOL = BOOT_ROOT / "src" / "infrastructure" / "tools" / "http_request" / "http_request_tool.py"
SHELL_BEHAVIOR_TEST = (
    BOOT_ROOT / "test" / "infrastructure" / "tools" / "shell_exec" / "test_shell_exec_tool_unit.py"
)
HTTP_BEHAVIOR_TEST = (
    BOOT_ROOT / "test" / "infrastructure" / "tools" / "http_request" / "test_http_request_tool.py"
)


def _read(path: Path) -> str:
    """读取文本文件内容。"""
    return path.read_text(encoding="utf-8")


def _assert_contains_all(content: str, fragments: list[str]) -> None:
    """断言文本包含全部策略标记。"""
    missing = [fragment for fragment in fragments if fragment not in content]
    assert not missing, f"缺少策略标记: {missing}"


def test_security_guardrail_document_contains_required_policy_markers() -> None:
    """安全护栏文档必须包含 Shell/HTTP 工具策略标记。"""
    _assert_contains_all(
        _read(SECURITY_DOC),
        [
            "ShellExecTool",
            "HttpRequestTool",
            "Dangerous_Command_Fragment",
            "SSRF_Risk_Target",
            "Model_Controlled_Sensitive_Header",
            "SHELL_EXEC_ENABLED=false",
            "http/https",
            "Config_Primary_Source",
        ],
    )


def test_shell_exec_tool_contains_guardrail_policy_markers() -> None:
    """ShellExecTool 必须保留危险命令阻断策略标记。"""
    _assert_contains_all(
        _read(SHELL_TOOL),
        [
            "_reject_dangerous_command",
            "blocked-command",
            "rm -rf",
            "mkfs",
            "dd if=",
            "remote script execution",
            "sensitive file read",
            ".env",
            "/etc/shadow",
            "~/.ssh/id_rsa",
        ],
    )


def test_http_request_tool_contains_guardrail_policy_markers() -> None:
    """HttpRequestTool 必须保留 SSRF 与敏感 Header 策略标记。"""
    _assert_contains_all(
        _read(HTTP_TOOL),
        [
            "_reject_sensitive_headers",
            "_host_block_reason",
            "169.254.169.254",
            "localhost",
            "private",
            "non-global",
            "authorization",
            "cookie",
            "x-api-key",
            "api-key",
            "proxy-authorization",
        ],
    )


def test_guardrail_policy_is_backed_by_runtime_behavior_tests() -> None:
    """高风险工具护栏不得只依赖字符串标记，必须有运行时行为测试兜底。"""
    shell_behavior = _read(SHELL_BEHAVIOR_TEST)
    http_behavior = _read(HTTP_BEHAVIOR_TEST)

    _assert_contains_all(
        shell_behavior,
        [
            "test_rejects_dangerous_commands_before_workspace_and_subprocess",
            "fake_exec.assert_not_called",
            "ws.capabilities.assert_not_called",
            "blocked-command",
        ],
    )
    _assert_contains_all(
        http_behavior,
        [
            "test_sensitive_header_execute_blocks_before_url_dns_and_request",
            "mock_validate.assert_not_called",
            "mock_request.assert_not_called",
            "test_ssrf_private_ip_rejection",
        ],
    )
