"""``ports.py`` 的 ``tier`` 参数签名与依赖方向静态断言。

覆盖：

1. ``TraceStorePort`` 三方法（``append_step`` / ``get_session_trace`` /
   ``list_traces``）各含 keyword-only 的 ``tier`` 参数，且默认值为
   ``StorageTier.PROJECT``（tasks.md 精确验收；需求 3.3、Property 6）。
2. 新增 ``ArtifactStorePort`` 定义了 ``append_artifact`` / ``list_artifacts``，
   两方法均含 keyword-only 的 ``tier`` 参数（需求 3.3、6.1）。
3. 依赖方向静态断言（复用仓库既有源码扫描风格）：``ports.py`` 源码不含任何
   物理路径 / 后端字符串字面量（``.epsilon`` / ``~`` / ``WORKSPACE_ROOT`` /
   ``OSS`` / ``S3``），也不 import 任何 ``infrastructure`` 模块——保证领域层
   与基础设施解耦（需求 1.2、8.3、Property 3）。
"""

from __future__ import annotations

import ast
import inspect

import pytest

from domain.agent import ports as ports_module
from domain.agent.ports import ArtifactStorePort, TraceStorePort
from domain.storage.storage_tier import StorageTier

_FORBIDDEN_LITERALS: tuple[str, ...] = (
    ".epsilon",
    "~",
    "WORKSPACE_ROOT",
    "OSS",
    "S3",
)
"""领域层禁止出现的物理路径 / 后端字符串字面量。"""


@pytest.mark.parametrize(
    "method_name",
    ["append_step", "get_session_trace", "list_traces"],
)
def test_trace_store_port_methods_have_keyword_only_tier(method_name: str) -> None:
    """``TraceStorePort`` 三方法含 keyword-only ``tier`` 且默认 ``StorageTier.PROJECT``。"""
    method = getattr(TraceStorePort, method_name)
    signature = inspect.signature(method)
    tier_param = signature.parameters["tier"]
    assert tier_param.kind is inspect.Parameter.KEYWORD_ONLY
    assert tier_param.default is StorageTier.PROJECT


def test_artifact_store_port_defines_expected_methods() -> None:
    """``ArtifactStorePort`` 定义了 ``append_artifact`` 与 ``list_artifacts``。"""
    assert callable(ArtifactStorePort.append_artifact)
    assert callable(ArtifactStorePort.list_artifacts)


@pytest.mark.parametrize(
    "method_name",
    ["append_artifact", "list_artifacts"],
)
def test_artifact_store_port_methods_have_keyword_only_tier(method_name: str) -> None:
    """``ArtifactStorePort`` 两方法含 keyword-only ``tier`` 且默认 ``StorageTier.PROJECT``。"""
    method = getattr(ArtifactStorePort, method_name)
    signature = inspect.signature(method)
    tier_param = signature.parameters["tier"]
    assert tier_param.kind is inspect.Parameter.KEYWORD_ONLY
    assert tier_param.default is StorageTier.PROJECT


def test_ports_source_has_no_physical_path_or_backend_literals() -> None:
    """``ports.py`` 源码不含物理路径 / 后端字符串字面量（Property 3）。"""
    source = inspect.getsource(ports_module)
    for literal in _FORBIDDEN_LITERALS:
        assert literal not in source, f"ports.py 不应出现字面量: {literal!r}"


def test_ports_does_not_import_infrastructure() -> None:
    """``ports.py`` 不 import 任何 ``infrastructure`` 模块（依赖方向静态断言）。"""
    source = inspect.getsource(ports_module)
    tree = ast.parse(source)
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.append(node.module)
    for module_name in imported_modules:
        assert not module_name.startswith("infrastructure"), (
            f"ports.py 不应导入 infrastructure 模块: {module_name}"
        )
