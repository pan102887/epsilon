"""``_common_impl`` 字节级实现单元测试（行为等价 + 回归保护）。

覆盖范围（对应 tasks 6.4 用例清单）：

- :func:`_read_bytes_in_range`：
  - 无行范围 → 等价于 ``Path.read_bytes()``（支持二进制）；
  - 指定行范围 → 按 1 起始的闭区间切片并 UTF-8 重新编码；
  - 行范围超过文件尾 → 返回剩余行；
  - 对二进制文件 + 行范围 → 抛 ``UnicodeDecodeError``；
  - 不存在 → 抛 ``FileNotFoundError``。
- :func:`_write_bytes_atomically`：
  - 自动创建多层父级目录；
  - 返回字节数等于 ``len(content)``；
  - 成功后文件内容与 ``content`` 按字节相等。
- :func:`_edit_with_fallback_match`：
  - 精确字节匹配；
  - 行级去空白模糊匹配（空白差异）；
  - 完全无匹配 → 返回 ``None``；
  - ``old_content=b""`` → 抛 ``ValueError``。
- :func:`_render_tree`：
  - 混合目录（子目录 / 文件 / 空目录）渲染；
  - 不存在目录 / 非目录路径 → 返回中文错误提示字符串。
"""

from pathlib import Path
from typing import NoReturn

import pytest

from infrastructure.workspace.local_filesystem._common_impl import (
    edit_with_fallback_match as _edit_with_fallback_match,
)
from infrastructure.workspace.local_filesystem._common_impl import (
    read_bytes_in_range as _read_bytes_in_range,
)
from infrastructure.workspace.local_filesystem._common_impl import (
    render_tree as _render_tree,
)
from infrastructure.workspace.local_filesystem._common_impl import (
    write_bytes_atomically as _write_bytes_atomically,
)

# ── _read_bytes_in_range ──


class TestReadBytesInRange:
    """字节级读取 + 可选行范围切片。"""

    def test_whole_file_as_bytes(self, tmp_path: Path) -> None:
        """未指定行范围时等价于 ``read_bytes``，支持二进制。"""
        target = tmp_path / "bin.dat"
        data = bytes(range(256))  # 非 UTF-8 合法序列
        target.write_bytes(data)
        assert _read_bytes_in_range(target, None, None) == data

    def test_line_range_closed_interval(self, tmp_path: Path) -> None:
        """``start_line=2``、``end_line=3`` 返回第 2、3 行（闭区间）。"""
        target = tmp_path / "a.txt"
        target.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")
        out = _read_bytes_in_range(target, 2, 3)
        assert out == b"line2\nline3"

    def test_line_range_from_start(self, tmp_path: Path) -> None:
        """``start_line=None``、``end_line=2`` 视为从第 1 行开始。"""
        target = tmp_path / "a.txt"
        target.write_text("a\nb\nc\n", encoding="utf-8")
        out = _read_bytes_in_range(target, None, 2)
        assert out == b"a\nb"

    def test_line_range_beyond_eof(self, tmp_path: Path) -> None:
        """行范围超出文件尾时返回剩余行。"""
        target = tmp_path / "a.txt"
        target.write_text("a\nb\n", encoding="utf-8")
        out = _read_bytes_in_range(target, 1, 100)
        assert out == b"a\nb"

    def test_binary_with_line_range_raises_unicode_error(self, tmp_path: Path) -> None:
        """指定行范围但文件非 UTF-8 → 原生 ``UnicodeDecodeError``。"""
        target = tmp_path / "bin.dat"
        target.write_bytes(b"\xff\xfe\xfd")
        with pytest.raises(UnicodeDecodeError):
            _read_bytes_in_range(target, 1, 1)

    def test_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        """文件不存在 → 原生 ``FileNotFoundError``。"""
        with pytest.raises(FileNotFoundError):
            _read_bytes_in_range(tmp_path / "nope.txt", None, None)


# ── _write_bytes_atomically ──


class TestWriteBytesAtomically:
    """原子写入 + 自动创建父目录。"""

    def test_creates_parents(self, tmp_path: Path) -> None:
        """多层父目录应自动创建。"""
        target = tmp_path / "a" / "b" / "c" / "out.txt"
        n = _write_bytes_atomically(target, b"hello")
        assert n == 5
        assert target.read_bytes() == b"hello"

    def test_returns_byte_count(self, tmp_path: Path) -> None:
        """返回的字节数等于 ``len(content)``。"""
        target = tmp_path / "x.bin"
        content = b"\x00\x01\x02\x03\x04\x05"
        assert _write_bytes_atomically(target, content) == len(content)
        assert target.read_bytes() == content

    def test_empty_content(self, tmp_path: Path) -> None:
        """写入空字节串合法；返回 0。"""
        target = tmp_path / "empty.bin"
        assert _write_bytes_atomically(target, b"") == 0
        assert target.read_bytes() == b""

    def test_overwrite_existing(self, tmp_path: Path) -> None:
        """原子 rename 覆盖已有文件。"""
        target = tmp_path / "x.txt"
        target.write_text("old", encoding="utf-8")
        _write_bytes_atomically(target, b"new")
        assert target.read_bytes() == b"new"

    def test_cleans_up_temp_on_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``os.replace`` 失败时应清理临时文件（不遗留 ``.workspace_tmp_`` 垃圾）。"""
        target = tmp_path / "x.txt"

        import os as _os

        real_replace = _os.replace

        def _boom(*args: object, **kwargs: object) -> NoReturn:
            raise OSError("simulated failure")

        monkeypatch.setattr(
            "infrastructure.workspace.local_filesystem._common_impl.os.replace",
            _boom,
        )
        with pytest.raises(OSError):
            _write_bytes_atomically(target, b"abc")
        # 清理后 tmp_path 下不应残留以 .workspace_tmp_ 开头的文件。
        leftover = [p for p in tmp_path.iterdir() if p.name.startswith(".workspace_tmp_")]
        assert leftover == []
        # 恢复。
        monkeypatch.setattr(
            "infrastructure.workspace.local_filesystem._common_impl.os.replace",
            real_replace,
        )


# ── _edit_with_fallback_match ──


class TestEditWithFallbackMatch:
    """两阶段匹配：精确字节 → 行级去空白回退。"""

    def test_exact_byte_match(self) -> None:
        """首个精确字节匹配成功。"""
        src = b"alpha\nbeta\nalpha\ngamma\n"
        out = _edit_with_fallback_match(src, b"alpha", b"ALPHA")
        assert out == b"ALPHA\nbeta\nalpha\ngamma\n"

    def test_fallback_whitespace_insensitive(self) -> None:
        """精确不匹配但行级去空白后一致 → 回退匹配成功。"""
        src = b"    hello world\n  foo bar\n"
        # old_content 缩进/尾空白不同，行级 strip 后应匹配。
        out = _edit_with_fallback_match(src, b"hello world\nfoo bar", b"REPLACED")
        assert out is not None
        assert b"REPLACED" in out
        assert b"hello world" not in out  # 原匹配行已被替换
        assert b"foo bar" not in out

    def test_no_match_returns_none(self) -> None:
        """完全无匹配时返回 ``None``。"""
        src = b"some content here\n"
        assert _edit_with_fallback_match(src, b"not in file", b"ignored") is None

    def test_empty_old_raises_value_error(self) -> None:
        """``old_content=b""`` 拒绝（与旧 ``common_tools.edit_file`` 语义一致）。"""
        with pytest.raises(ValueError):
            _edit_with_fallback_match(b"abc", b"", b"x")

    def test_new_content_empty_is_deletion(self) -> None:
        """``new_content=b""`` 等效删除。"""
        src = b"a\nb\nc\n"
        out = _edit_with_fallback_match(src, b"b\n", b"")
        assert out == b"a\nc\n"

    def test_first_match_only(self) -> None:
        """只替换首次出现的精确匹配（保留后续 occurrences）。"""
        src = b"x-x-x"
        out = _edit_with_fallback_match(src, b"x", b"Y")
        assert out == b"Y-x-x"


# ── _render_tree ──


class TestRenderTree:
    """ASCII 树形渲染。"""

    def test_mixed_tree(self, tmp_path: Path) -> None:
        """混合场景：子目录 + 文件 + 空目录。"""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("", encoding="utf-8")
        (tmp_path / "src" / "utils.py").write_text("", encoding="utf-8")
        (tmp_path / "empty").mkdir()
        (tmp_path / "README.md").write_text("", encoding="utf-8")

        output = _render_tree(tmp_path)
        # 关键断言：包含目录/文件名，且使用 ASCII 连接符。
        assert "src" in output
        assert "main.py" in output
        assert "utils.py" in output
        assert "empty" in output
        assert "README.md" in output
        assert "├──" in output or "└──" in output

    def test_nonexistent_returns_error(self, tmp_path: Path) -> None:
        """不存在路径 → 中文错误提示字符串（与旧实现保持一致）。"""
        out = _render_tree(tmp_path / "nope")
        assert out.startswith("错误：目录不存在")

    def test_not_a_directory_returns_error(self, tmp_path: Path) -> None:
        """非目录路径 → 中文错误提示字符串。"""
        f = tmp_path / "file.txt"
        f.write_text("x", encoding="utf-8")
        out = _render_tree(f)
        assert out.startswith("错误：路径不是目录")

    def test_ignore_set_default(self, tmp_path: Path) -> None:
        """默认忽略 ``.git`` / ``__pycache__`` / ``.venv`` 目录。"""
        (tmp_path / ".git").mkdir()
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / ".venv").mkdir()
        (tmp_path / "keep").mkdir()
        out = _render_tree(tmp_path)
        assert "keep" in out
        assert ".git" not in out
        assert "__pycache__" not in out
        assert ".venv" not in out

    def test_ignore_custom(self, tmp_path: Path) -> None:
        """自定义 ``ignore`` 集合覆盖默认。"""
        (tmp_path / ".git").mkdir()
        (tmp_path / "node_modules").mkdir()
        out = _render_tree(tmp_path, ignore={"node_modules"})
        # 自定义集合生效：.git 现在出现，node_modules 被过滤。
        assert ".git" in out
        assert "node_modules" not in out


class TestCommonImplBehavior:
    """行为回归保护：``_common_impl`` 在典型输入下保持稳定。"""

    def test_write_then_read_roundtrip(self, tmp_path: Path) -> None:
        """``_write_bytes_atomically`` → ``_read_bytes_in_range`` 往返。"""
        target = tmp_path / "x" / "y.txt"
        content = "第一行\n第二行\n第三行\n".encode()
        n = _write_bytes_atomically(target, content)
        assert n == len(content)
        # 整文件读取等价于 read_bytes。
        assert _read_bytes_in_range(target, None, None) == content
        # 行范围读取。
        assert _read_bytes_in_range(target, 2, 2) == "第二行".encode()

    def test_edit_preserves_untouched_regions(self, tmp_path: Path) -> None:
        """编辑后未匹配区域保持字节级不变。"""
        src = b"HEAD\n--anchor--\nTAIL\n"
        out = _edit_with_fallback_match(src, b"--anchor--", b"--REPLACED--")
        assert out is not None
        assert out.startswith(b"HEAD\n")
        assert out.endswith(b"\nTAIL\n")
