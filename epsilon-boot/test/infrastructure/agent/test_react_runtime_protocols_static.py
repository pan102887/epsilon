"""ReAct 运行时协议模块的静态边界测试。"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BOOT_ROOT = Path(__file__).resolve().parents[3]
PROTOCOL_PATH = BOOT_ROOT / "src" / "infrastructure" / "agent" / "react_runtime_protocols.py"
PROTOCOL_PACKAGE = "infrastructure.agent"
FORBIDDEN_IMPORT_PREFIXES = (
    "application",
    "common.configuration",
    "infrastructure",
)
TOOL_RUNTIME_METHODS = {
    "execute_tool_call",
    "tool_progress_chunk",
    "tool_start_event",
    "tool_result_event",
    "tool_error_event",
}
APPROVAL_RUNTIME_METHODS = {
    "execute_approved_tool_call",
    "validate_edited_tool_call",
    "record_rejected_tool_call",
}
EXPECTED_RETURNS = {
    "execute_tool_call": "Awaitable[None]",
    "tool_progress_chunk": "StreamingChunk",
    "tool_start_event": "AgentStreamEvent",
    "tool_result_event": "AgentStreamEvent",
    "tool_error_event": "AgentStreamEvent",
    "execute_approved_tool_call": "Awaitable[None]",
    "validate_edited_tool_call": "None",
    "record_rejected_tool_call": "Awaitable[None]",
}


def _source_path() -> Path:
    """返回协议源文件路径；并发切片尚未创建时跳过。"""

    if not PROTOCOL_PATH.exists():
        pytest.skip("Task 1.2 has not created react_runtime_protocols.py yet.")
    return PROTOCOL_PATH


def _parse_module() -> ast.Module:
    """解析协议源码 AST，不执行任何模块导入。"""

    source_path = _source_path()
    return ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))


def _has_prefix(module: str, prefix: str) -> bool:
    """按模块分段判断导入是否命中禁止前缀。"""

    return module == prefix or module.startswith(prefix + ".")


def _resolve_import_from(node: ast.ImportFrom) -> str | None:
    """解析 ImportFrom 的绝对模块名，覆盖相对导入。"""

    if node.level == 0:
        return node.module

    package_parts = PROTOCOL_PACKAGE.split(".")
    retained_parts = package_parts[: len(package_parts) - node.level + 1]
    module_parts = [*retained_parts]
    if node.module:
        module_parts.append(node.module)
    return ".".join(module_parts)


def _imports(tree: ast.Module) -> set[str]:
    """提取源码内全部导入模块名。"""

    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_import_from(node)
            if module:
                modules.add(module)
    return modules


def _classes(tree: ast.Module) -> dict[str, ast.ClassDef]:
    """按类名索引模块内的类定义。"""

    return {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}


def _inherits_protocol(node: ast.ClassDef) -> bool:
    """判断类是否显式继承 Protocol。"""

    return any(
        (isinstance(base, ast.Name) and base.id == "Protocol")
        or (isinstance(base, ast.Attribute) and base.attr == "Protocol")
        for base in node.bases
    )


def _public_methods(node: ast.ClassDef) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """返回类上公开方法定义。"""

    return {
        item.name: item
        for item in node.body
        if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
        and not item.name.startswith("_")
    }


def _return_annotation(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """返回方法返回值注解的源码文本。"""

    if node.returns is None:
        return None
    return ast.unparse(node.returns)


def test_react_runtime_protocols_do_not_import_application_configuration_or_adapters() -> None:
    """协议模块不得导入 application、运行时配置或基础设施具体实现。"""

    imports = _imports(_parse_module())
    violations = {
        module
        for module in imports
        if any(_has_prefix(module, prefix) for prefix in FORBIDDEN_IMPORT_PREFIXES)
    }

    assert violations == set()


def test_tool_execution_runtime_exposes_expected_protocol_methods() -> None:
    """ToolExecutionRuntime 只暴露任务定义的窄协议方法。"""

    protocol_class = _classes(_parse_module())["ToolExecutionRuntime"]
    methods = _public_methods(protocol_class)

    assert _inherits_protocol(protocol_class)
    assert set(methods) == TOOL_RUNTIME_METHODS
    for name, method in methods.items():
        assert isinstance(method, ast.FunctionDef)
        assert _return_annotation(method) == EXPECTED_RETURNS[name]


def test_approval_resume_runtime_exposes_expected_protocol_methods() -> None:
    """ApprovalResumeRuntime 只暴露任务定义的窄协议方法。"""

    protocol_class = _classes(_parse_module())["ApprovalResumeRuntime"]
    methods = _public_methods(protocol_class)

    assert _inherits_protocol(protocol_class)
    assert set(methods) == APPROVAL_RUNTIME_METHODS
    for name, method in methods.items():
        assert isinstance(method, ast.FunctionDef)
        assert _return_annotation(method) == EXPECTED_RETURNS[name]
