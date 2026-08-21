"""``Workspace`` / ``LocallyMaterializable`` Port 结构类型契约单元测试。

本测试文件覆盖 tasks.md 4.2 的 3 类断言：

1. 用 ``unittest.mock.MagicMock`` 构造一个声明了全部 10 个方法的 mock，
   断言其通过 ``isinstance(mock, Workspace)`` 结构类型检查（依赖
   ``@runtime_checkable``）。
2. 断言 ``Workspace.__dict__`` 含 10 个方法名；
   ``LocallyMaterializable.__dict__`` 含 ``materialize_cwd``。
3. 断言 ``Workspace.read`` 的返回类型注解经 ``typing.get_type_hints``
   解析后等于 ``bytes``。

测试采用仓库既有的 pytest + class-based 风格，不依赖 pytest-asyncio。
"""

from __future__ import annotations

import typing
from collections.abc import Mapping
from unittest.mock import MagicMock

from domain.workspace.ports import LocallyMaterializable, Workspace
from domain.workspace.value_objects import (
    WorkspaceCapabilities,
    WorkspacePath,
    WorkspaceStatEntry,
)

# ── Workspace Port 应暴露的 10 个方法名 ──
_EXPECTED_WORKSPACE_METHODS: frozenset[str] = frozenset(
    {
        # 7 个 I/O 方法
        "exists",
        "stat",
        "read",
        "write",
        "edit",
        "list_dir",
        "delete",
        # 3 个非 I/O 方法
        "resolve_path",
        "capabilities",
        "display_root_hint",
    }
)


class _WorkspaceStub:
    """手写满足 ``Workspace`` 协议结构类型的最小 stub。

    Python 3.13 开始 ``typing._ProtocolMeta.__instancecheck__`` 对
    ``@runtime_checkable`` Protocol 的判定更严格——``MagicMock`` 的动态
    属性生成不再被视为满足 Protocol 要求（见 2026-05-11 pytest 回归
    缺陷修复批次 C）。这里改用手写 stub 确保结构类型契约在 3.13+ 仍可验证。
    """

    def resolve_path(self, requested: str) -> WorkspacePath:
        raise NotImplementedError

    async def exists(
        self, path: WorkspacePath, *, context: Mapping[str, object] | None = None
    ) -> bool:
        return False

    async def stat(
        self, path: WorkspacePath, *, context: Mapping[str, object] | None = None
    ) -> WorkspaceStatEntry:
        raise NotImplementedError

    async def read(
        self,
        path: WorkspacePath,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
        context: Mapping[str, object] | None = None,
    ) -> bytes:
        return b""

    async def write(
        self,
        path: WorkspacePath,
        content: bytes,
        *,
        context: Mapping[str, object] | None = None,
    ) -> int:
        return 0

    async def edit(
        self,
        path: WorkspacePath,
        old_content: bytes,
        new_content: bytes,
        *,
        context: Mapping[str, object] | None = None,
    ) -> int:
        return 0

    async def list_dir(
        self,
        path: WorkspacePath,
        *,
        recursive: bool = True,
        context: Mapping[str, object] | None = None,
    ) -> list[WorkspaceStatEntry]:
        return []

    async def delete(
        self, path: WorkspacePath, *, context: Mapping[str, object] | None = None
    ) -> None:
        pass

    def capabilities(self) -> WorkspaceCapabilities:
        return WorkspaceCapabilities()

    def display_root_hint(self) -> str:
        return ""


class TestWorkspaceStructuralTyping:
    """``Workspace`` 作为 ``@runtime_checkable`` Protocol 的结构类型契约。"""

    def test_stub_with_all_methods_satisfies_workspace(self) -> None:
        """手写声明 10 个方法的 stub 通过 ``isinstance`` 检查。

        这是 Protocol 结构类型的正面用例——对象只要在结构上具备协议声明
        的全部方法名（签名不做校验），``isinstance(obj, Proto)`` 即返回
        ``True``。Python 3.13 的收紧仅影响 ``MagicMock`` 这类"动态生成
        属性"的对象，对手写普通类无影响。
        """
        assert isinstance(_WorkspaceStub(), Workspace)

    def test_magic_mock_does_not_raise_on_isinstance(self) -> None:
        """``MagicMock`` 上做 ``isinstance`` 判定不应抛异常。

        Python 3.13 下 ``isinstance(MagicMock(), Workspace)`` 的返回值
        不再稳定为 ``True``（取决于 mock 的属性可见性），但仍应返回
        ``bool`` 而不是抛异常——这是 Protocol 运行时检查对 Mock 兼容性
        的最低保障，供 development.md 中关于 Mock 的约定兜底。
        """
        mock = MagicMock()
        result = isinstance(mock, Workspace)
        assert isinstance(result, bool)

    def test_object_without_required_methods_fails_isinstance(self) -> None:
        """不具备必要方法的对象应被 ``isinstance`` 判定拒绝。"""

        class Empty:
            pass

        # Protocol 结构类型：缺失方法的对象应返回 False
        assert not isinstance(Empty(), Workspace)


class TestWorkspaceMethodDirectory:
    """``Workspace`` 类字典必须精确包含 10 个方法名。"""

    def test_workspace_has_all_ten_methods(self) -> None:
        """``Workspace.__dict__`` 含全部 10 个方法名。"""
        for method_name in _EXPECTED_WORKSPACE_METHODS:
            assert method_name in Workspace.__dict__, f"Workspace 缺失方法：{method_name}"

    def test_workspace_exposes_seven_io_methods(self) -> None:
        """7 个 I/O 方法显式存在。"""
        io_methods = {
            "exists",
            "stat",
            "read",
            "write",
            "edit",
            "list_dir",
            "delete",
        }
        for name in io_methods:
            assert name in Workspace.__dict__

    def test_workspace_exposes_three_non_io_methods(self) -> None:
        """3 个非 I/O 方法显式存在。"""
        for name in ("resolve_path", "capabilities", "display_root_hint"):
            assert name in Workspace.__dict__


class TestLocallyMaterializableMethodDirectory:
    """``LocallyMaterializable`` 必须恰好声明 ``materialize_cwd``。"""

    def test_locally_materializable_has_materialize_cwd(self) -> None:
        assert "materialize_cwd" in LocallyMaterializable.__dict__

    def test_stub_with_materialize_cwd_satisfies_protocol(self) -> None:
        """手写声明 ``materialize_cwd`` 的 stub 通过 ``isinstance`` 检查。

        Python 3.13 下 ``MagicMock(spec=["materialize_cwd"])`` 在
        ``isinstance(mock, LocallyMaterializable)`` 判定中不再稳定返回
        ``True``（见 2026-05-11 pytest 回归缺陷修复批次 C），这里改用
        手写 stub 验证 Protocol 结构类型契约。
        """

        class _MaterializableStub:
            def materialize_cwd(self, path: WorkspacePath) -> str:
                return ""

        assert isinstance(_MaterializableStub(), LocallyMaterializable)


class TestWorkspaceReadReturnAnnotation:
    """``Workspace.read`` 的返回类型注解经 ``get_type_hints`` 解析为 ``bytes``。"""

    def test_read_return_annotation_is_bytes(self) -> None:
        hints = typing.get_type_hints(Workspace.read)
        assert "return" in hints, "Workspace.read 未声明返回类型注解"
        assert hints["return"] is bytes, (
            f"Workspace.read 返回注解应为 bytes，实际为 {hints['return']!r}"
        )
