"""``LocalFilesystemWorkspace`` 各 I/O 方法的 happy-path + 常见错误单元测试。

覆盖范围（对应 tasks 7.10）：

- ``exists`` / ``stat``：存在 / 不存在 / 非目录（``stat`` 返回 ``is_dir=False``）。
- ``read``：整文件 / 行范围 / 二进制+行范围 → ``WorkspaceIoError(decode_failed)`` /
  不存在 → ``WorkspaceNotFoundError``。
- ``write``：成功写入字节数；父级目录自动创建。
- ``list_dir``：递归 / 非递归；空目录；不存在 → ``WorkspaceNotFoundError``。
- ``delete``：文件 / 目录 / 不存在。
- ``materialize_cwd``：目录返回宿主路径字符串；非目录抛
  ``WorkspaceIoError(reason="not_a_directory")``。
- **所有 I/O 方法**：传入 ``context={"tool_name": ..., "trace_id": ...}`` 与
  ``context=None`` 均不改变 happy-path 输出（纯观测透传红线）。

本测试依赖仓库 ``pyproject.toml`` 中的 ``asyncio_mode = "auto"`` 自动运行
异步用例，并使用 ``tmp_path`` fixture 做 root。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from domain.workspace.exceptions import (
    WorkspaceIoError,
    WorkspaceNotFoundError,
)
from domain.workspace.policy import WorkspacePolicy
from infrastructure.workspace.local_filesystem import LocalFilesystemWorkspace


@pytest.fixture
def ws(tmp_path: Path) -> LocalFilesystemWorkspace:
    """构造一个以 ``tmp_path`` 为 root 的 ``LocalFilesystemWorkspace``。"""
    return LocalFilesystemWorkspace(
        root=tmp_path.resolve(),
        follow_symlinks=False,
        policy=WorkspacePolicy(),
    )


_CTX = {"tool_name": "read_file", "trace_id": "t1"}


class TestExistsAndStat:
    """``exists`` / ``stat`` happy-path + 常见错误。"""

    async def test_exists_true_and_false(
        self, ws: LocalFilesystemWorkspace, tmp_path: Path
    ) -> None:
        """存在/不存在的文件分别返回 True/False，``context`` 透传不改变结果。"""
        wp = ws.resolve_path("a.txt")
        assert await ws.exists(wp) is False
        assert await ws.exists(wp, context=_CTX) is False
        (tmp_path / "a.txt").write_bytes(b"x")
        assert await ws.exists(wp) is True
        assert await ws.exists(wp, context=_CTX) is True

    async def test_stat_happy(self, ws: LocalFilesystemWorkspace, tmp_path: Path) -> None:
        """``stat`` 返回字段与 ``os.stat`` 一致，``context`` 透传无副作用。"""
        (tmp_path / "b.txt").write_bytes(b"hello")
        wp = ws.resolve_path("b.txt")
        entry = await ws.stat(wp)
        assert entry.is_file is True
        assert entry.is_dir is False
        assert entry.size == 5
        assert entry.mtime is not None
        entry2 = await ws.stat(wp, context=_CTX)
        assert entry2.size == entry.size

    async def test_stat_not_found(self, ws: LocalFilesystemWorkspace) -> None:
        """不存在路径抛 ``WorkspaceNotFoundError``。"""
        with pytest.raises(WorkspaceNotFoundError):
            await ws.stat(ws.resolve_path("no.txt"), context=_CTX)

    async def test_stat_on_directory(self, ws: LocalFilesystemWorkspace, tmp_path: Path) -> None:
        """目录 stat：``is_dir=True``、``is_file=False``。"""
        (tmp_path / "sub").mkdir()
        entry = await ws.stat(ws.resolve_path("sub"))
        assert entry.is_dir is True
        assert entry.is_file is False


class TestRead:
    """``read`` happy-path + 常见错误。"""

    async def test_read_full_file(self, ws: LocalFilesystemWorkspace, tmp_path: Path) -> None:
        """整文件字节读取（未指定行范围）。"""
        (tmp_path / "c.txt").write_bytes(b"abc\n")
        wp = ws.resolve_path("c.txt")
        assert await ws.read(wp) == b"abc\n"
        assert await ws.read(wp, context=_CTX) == b"abc\n"

    async def test_read_line_range(self, ws: LocalFilesystemWorkspace, tmp_path: Path) -> None:
        """闭区间行范围 [1, 2] 返回前两行，不带尾部换行。"""
        (tmp_path / "d.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")
        wp = ws.resolve_path("d.txt")
        out = await ws.read(wp, start_line=1, end_line=2)
        assert out == b"line1\nline2"

    async def test_read_binary_with_line_range_raises_decode_failed(
        self, ws: LocalFilesystemWorkspace, tmp_path: Path
    ) -> None:
        """二进制 + 行范围：``UnicodeDecodeError`` → ``WorkspaceIoError(decode_failed)``。"""
        (tmp_path / "bin.dat").write_bytes(b"\xff\xfe\xfd\x00")
        wp = ws.resolve_path("bin.dat")
        with pytest.raises(WorkspaceIoError) as ei:
            await ws.read(wp, start_line=1, end_line=1)
        assert ei.value.reason == "decode_failed"

    async def test_read_not_found(self, ws: LocalFilesystemWorkspace) -> None:
        """不存在文件抛 ``WorkspaceNotFoundError``。"""
        with pytest.raises(WorkspaceNotFoundError):
            await ws.read(ws.resolve_path("no.txt"))


class TestWrite:
    """``write`` happy-path。"""

    async def test_write_returns_byte_count(
        self, ws: LocalFilesystemWorkspace, tmp_path: Path
    ) -> None:
        """返回值严格等于 ``len(content)``。"""
        wp = ws.resolve_path("e.txt")
        n = await ws.write(wp, b"hello world")
        assert n == 11
        assert (tmp_path / "e.txt").read_bytes() == b"hello world"

    async def test_write_creates_parent_dirs(
        self, ws: LocalFilesystemWorkspace, tmp_path: Path
    ) -> None:
        """``write`` 自动递归创建父级目录。"""
        wp = ws.resolve_path("deep/nested/tree/f.txt")
        n = await ws.write(wp, b"x", context=_CTX)
        assert n == 1
        assert (tmp_path / "deep" / "nested" / "tree" / "f.txt").read_bytes() == b"x"


class TestListDir:
    """``list_dir`` happy-path + 常见错误。"""

    async def test_list_dir_non_recursive(
        self, ws: LocalFilesystemWorkspace, tmp_path: Path
    ) -> None:
        """非递归只列一层子条目。"""
        (tmp_path / "a.txt").write_bytes(b"A")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "inner.txt").write_bytes(b"I")
        root = ws.resolve_path("/")
        entries = await ws.list_dir(root, recursive=False)
        names = sorted(e.path.to_posix() for e in entries)
        assert names == ["/a.txt", "/sub"]

    async def test_list_dir_recursive(self, ws: LocalFilesystemWorkspace, tmp_path: Path) -> None:
        """递归 DFS 列出所有子条目。"""
        (tmp_path / "a.txt").write_bytes(b"A")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "inner.txt").write_bytes(b"I")
        root = ws.resolve_path("/")
        entries = await ws.list_dir(root, recursive=True, context=_CTX)
        names = sorted(e.path.to_posix() for e in entries)
        assert "/a.txt" in names
        assert "/sub" in names
        assert "/sub/inner.txt" in names

    async def test_list_dir_empty_directory(
        self, ws: LocalFilesystemWorkspace, tmp_path: Path
    ) -> None:
        """空目录返回空列表。"""
        (tmp_path / "empty").mkdir()
        entries = await ws.list_dir(ws.resolve_path("empty"))
        assert entries == []

    async def test_list_dir_not_found(self, ws: LocalFilesystemWorkspace) -> None:
        """不存在目录抛 ``WorkspaceNotFoundError``。"""
        with pytest.raises(WorkspaceNotFoundError):
            await ws.list_dir(ws.resolve_path("nope"))


class TestDelete:
    """``delete`` happy-path + 常见错误。"""

    async def test_delete_file(self, ws: LocalFilesystemWorkspace, tmp_path: Path) -> None:
        """删除普通文件。"""
        (tmp_path / "g.txt").write_bytes(b"G")
        wp = ws.resolve_path("g.txt")
        await ws.delete(wp, context=_CTX)
        assert not (tmp_path / "g.txt").exists()

    async def test_delete_directory(self, ws: LocalFilesystemWorkspace, tmp_path: Path) -> None:
        """删除整棵目录子树。"""
        (tmp_path / "h").mkdir()
        (tmp_path / "h" / "inner.txt").write_bytes(b"I")
        await ws.delete(ws.resolve_path("h"))
        assert not (tmp_path / "h").exists()

    async def test_delete_not_found(self, ws: LocalFilesystemWorkspace) -> None:
        """不存在路径抛 ``WorkspaceNotFoundError``。"""
        with pytest.raises(WorkspaceNotFoundError):
            await ws.delete(ws.resolve_path("no"))


class TestMaterializeCwd:
    """``materialize_cwd`` happy-path + 常见错误。

    本方法**同步**、无 ``context`` 参数（``LocallyMaterializable`` 协议
    约束）。返回的宿主绝对路径字符串只应用于子进程 ``cwd``。
    """

    def test_materialize_directory_returns_host_path(
        self, ws: LocalFilesystemWorkspace, tmp_path: Path
    ) -> None:
        (tmp_path / "work").mkdir()
        cwd = ws.materialize_cwd(ws.resolve_path("work"))
        assert cwd == str(tmp_path.resolve() / "work")

    def test_materialize_non_directory_raises(
        self, ws: LocalFilesystemWorkspace, tmp_path: Path
    ) -> None:
        (tmp_path / "f.txt").write_bytes(b"F")
        with pytest.raises(WorkspaceIoError) as ei:
            ws.materialize_cwd(ws.resolve_path("f.txt"))
        assert ei.value.reason == "not_a_directory"


class TestContextPassthroughIsObservabilityOnly:
    """纯观测透传红线：``context`` 变化不改变 I/O 行为与返回值。

    本类 smoke 式遍历每个 I/O 方法，分别传 ``None`` / 空字典 / 含白名单
    字段 / 含未知字段的 ``context``，断言每种情况返回结果一致。
    """

    async def test_exists_same_output_under_various_contexts(
        self, ws: LocalFilesystemWorkspace, tmp_path: Path
    ) -> None:
        (tmp_path / "x.txt").write_bytes(b"x")
        wp = ws.resolve_path("x.txt")
        r1 = await ws.exists(wp)
        r2 = await ws.exists(wp, context=None)
        r3 = await ws.exists(wp, context={})
        r4 = await ws.exists(wp, context={"tool_name": "read_file"})
        r5 = await ws.exists(wp, context={"unknown_key": "value"})
        assert r1 is r2 is r3 is r4 is r5 is True

    async def test_read_same_output_under_various_contexts(
        self, ws: LocalFilesystemWorkspace, tmp_path: Path
    ) -> None:
        (tmp_path / "r.txt").write_bytes(b"content")
        wp = ws.resolve_path("r.txt")
        r1 = await ws.read(wp)
        r2 = await ws.read(wp, context={"tool_name": "read_file", "trace_id": "abc"})
        r3 = await ws.read(wp, context={"secret": "password123"})
        assert r1 == r2 == r3 == b"content"

    async def test_write_same_bytes_under_various_contexts(
        self, ws: LocalFilesystemWorkspace
    ) -> None:
        wp1 = ws.resolve_path("w1.txt")
        wp2 = ws.resolve_path("w2.txt")
        n1 = await ws.write(wp1, b"hello")
        n2 = await ws.write(wp2, b"hello", context={"tool_name": "write_file"})
        assert n1 == n2 == 5
