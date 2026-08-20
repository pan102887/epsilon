"""``LocalFilesystemWorkspace`` 的字节级公共实现（基础设施私有）。

本模块承载 ``read_file`` / ``write_file`` / ``edit_file`` / ``tree`` 的
**字节层核心**，为 ``LocalFilesystemWorkspace`` 的各 I/O 方法提供复用。
这些函数曾位于历史 ``common.tools.common_tools`` 公共入口；该入口已删除，
避免 ``common`` 反向依赖 ``infrastructure``。设计决策见
``docs/spec/workspace/design.md`` §组件与接口 2：

- Port 对外只暴露 ``bytes``；UTF-8 编解码在工具层完成。
- 本模块的四个函数均工作在**字节层**（或字节与文本分层之间），不承担
  面向 LLM 的错误消息拼装，异常以原生 ``FileNotFoundError`` /
  ``UnicodeDecodeError`` / ``OSError`` 抛出，由调用方（``LocalFilesystemWorkspace``）
  翻译为领域错误。

模块函数清单：

- :func:`_read_bytes_in_range`
- :func:`_write_bytes_atomically`
- :func:`_edit_with_fallback_match`
- :func:`_render_tree`
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

# ── 常量 ──

_DEFAULT_TREE_IGNORE: frozenset[str] = frozenset({".git", "__pycache__", ".venv"})
"""``_render_tree`` 默认忽略的目录/文件名集合，与旧 ``common_tools.tree`` 一致。"""


# ── read ──


def _read_bytes_in_range(
    host_path: Path,
    start_line: int | None,
    end_line: int | None,
) -> bytes:
    """按可选的 UTF-8 行范围读取文件字节内容。

    - 未指定行范围（``start_line`` 与 ``end_line`` 均为 ``None``）时直接
      返回 ``host_path.read_bytes()``，**不做 UTF-8 解码**，适用于二进制
      文件整文件读取。
    - 指定行范围时，先 ``read_text(encoding="utf-8")``，按 ``splitlines()``
      切片闭区间行号，再重新编码为 ``bytes`` 返回。解码失败原生抛出
      ``UnicodeDecodeError``，由上层翻译为 ``WorkspaceIoError(decode_failed)``。

    行号语义（闭区间、1 起）：

    - ``start_line=1``、``end_line=3`` → 返回第 1、2、3 行。
    - ``start_line=None`` → 从第 1 行开始。
    - ``end_line=None`` 或超出文件尾 → 读到文件末尾。

    Args:
        host_path: 宿主文件绝对路径。
        start_line: 起始行号（1-起始，闭区间），``None`` 表示从文件开头。
        end_line: 结束行号（闭区间），``None`` 或超过文件总行数时读到末尾。

    Returns:
        文件内容的字节切片。未指定行范围时与 ``read_bytes`` 等价。

    Raises:
        FileNotFoundError: 文件不存在（``host_path.read_bytes`` 或
            ``read_text`` 抛出）。
        UnicodeDecodeError: 指定了行范围但文件无法 UTF-8 解码。
        OSError: 其他 I/O 错误（权限不足等）。
    """
    if start_line is None and end_line is None:
        # 整文件字节读取；不做 UTF-8 解码（支持二进制文件）。
        return host_path.read_bytes()

    # 行范围模式：必须 UTF-8 解码。
    text = host_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    start_idx = (start_line or 1) - 1
    if start_idx < 0:
        start_idx = 0
    end_idx = end_line if end_line is not None else len(lines)
    # 闭区间：splice 时使用 end_idx（python 切片上界开区间 → 语义闭）
    selected = lines[start_idx:end_idx]
    return "\n".join(selected).encode("utf-8")


# ── write ──


def _write_bytes_atomically(host_path: Path, content: bytes) -> int:
    """原子写入字节内容，自动创建父目录。

    算法：

    1. ``host_path.parent.mkdir(parents=True, exist_ok=True)``；
    2. ``tempfile.NamedTemporaryFile(dir=parent, delete=False)`` 创建同目录
       临时文件 → 写入 ``content`` → 刷新 → 关闭；
    3. ``os.replace(tmp, host_path)`` 做 POSIX rename，保证同一卷上的
       rename 原子性。失败时尝试删除临时文件，再向上抛出。

    本函数**不做**符号链接守卫、不做路径越界检查，这些职责由调用方
    （``LocalFilesystemWorkspace``）在调用前完成。

    Args:
        host_path: 目标宿主文件绝对路径。
        content: 待写入的字节内容。

    Returns:
        实际写入的字节数（等于 ``len(content)``）。

    Raises:
        OSError: 跨设备 rename（``errno.EXDEV``）、权限不足、磁盘满等底层
            错误原样抛出，由上层翻译为 ``WorkspaceIoError``。
    """
    parent = host_path.parent
    parent.mkdir(parents=True, exist_ok=True)

    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=str(parent),
            delete=False,
            prefix=".workspace_tmp_",
            suffix=host_path.suffix or "",
        ) as tmp:
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_name = tmp.name
        os.replace(tmp_name, host_path)
        tmp_name = None  # 成功后置空，避免 finally 误删。
    finally:
        if tmp_name is not None:
            # 临时文件清理失败不覆盖主异常。
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
    return len(content)


# ── edit ──


def _edit_with_fallback_match(
    current_bytes: bytes,
    old_content: bytes,
    new_content: bytes,
) -> bytes | None:
    """对 ``current_bytes`` 做"首个匹配片段替换"（精确 + 行级去空白回退）。

    两阶段匹配（与旧 ``common_tools.edit_file`` 等价）：

    1. **精确字节匹配**：``current_bytes.find(old_content)``；若找到则返回
       首次匹配替换后的新字节。
    2. **行级去空白回退**：仅在 UTF-8 可解码时启用。把 ``current_bytes``
       / ``old_content`` 各按行拆分，每行 ``.strip()`` 后逐行比较，找到
       首个窗口匹配后，把原始行范围替换为 ``new_content``（按原始分隔
       方式重组）。

    若两阶段都未匹配，返回 ``None``，调用方据此抛 ``WorkspaceIoError(
    reason="no_match")``。

    Args:
        current_bytes: 当前文件的完整字节内容。
        old_content: 待替换的原始片段（字节）。
        new_content: 替换后的新片段（字节）。

    Returns:
        匹配成功时返回新的完整字节内容；完全无匹配时返回 ``None``。

    Raises:
        ValueError: ``old_content`` 为空字节串（对齐旧
            ``common_tools.edit_file`` 对 ``old_str == ""`` 的拒绝语义）。
    """
    if old_content == b"":
        raise ValueError("old_content 不能为空")

    # 阶段一：精确字节匹配。
    pos = current_bytes.find(old_content)
    if pos != -1:
        return current_bytes[:pos] + new_content + current_bytes[pos + len(old_content) :]

    # 阶段二：行级去空白回退。仅在 UTF-8 可解码时启用。
    try:
        current_text = current_bytes.decode("utf-8")
        old_text = old_content.decode("utf-8")
        new_text = new_content.decode("utf-8")
    except UnicodeDecodeError:
        return None

    file_lines = current_text.splitlines(keepends=True)
    old_lines = old_text.splitlines()
    stripped_old = [line.strip() for line in old_lines]
    file_lines_stripped = [line.rstrip("\n\r").strip() for line in file_lines]

    match_start: int | None = None
    if stripped_old:
        for i in range(len(file_lines_stripped) - len(stripped_old) + 1):
            candidate = file_lines_stripped[i : i + len(stripped_old)]
            if candidate == stripped_old:
                match_start = i
                break

    if match_start is None:
        return None

    match_end = match_start + len(stripped_old)
    before = "".join(file_lines[:match_start])
    after = "".join(file_lines[match_end:])
    new_full = before + new_text + after
    return new_full.encode("utf-8")


# ── tree ──


def _render_tree(
    directory: Path,
    prefix: str = "",
    ignore: frozenset[str] | set[str] | None = None,
) -> str:
    """以 ASCII 树形结构展示目录内容，与旧 ``common_tools.tree`` 行为等价。

    输出示例::

        ├── src
        │   ├── main.py
        │   └── utils.py
        └── README.md

    Args:
        directory: 待渲染的目录路径（绝对路径）。
        prefix: 缩进前缀（递归内部使用）。
        ignore: 需忽略的目录/文件名集合，默认忽略 ``.git``、``__pycache__``、
            ``.venv``。

    Returns:
        树形结构字符串；路径不存在或非目录或无权限时返回以 "错误：" 开头的
        提示字符串（与旧实现保持完全兼容，避免调用方语义漂移）。
    """
    if not directory.exists():
        return f"错误：目录不存在 - {directory}"
    if not directory.is_dir():
        return f"错误：路径不是目录 - {directory}"
    effective_ignore: set[str] | frozenset[str] = (
        ignore if ignore is not None else _DEFAULT_TREE_IGNORE
    )
    lines: list[str] = []
    try:
        entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except PermissionError:
        return f"错误：无权限访问目录 - {directory}"
    entries = [e for e in entries if e.name not in effective_ignore]
    for i, entry in enumerate(entries):
        connector = "└── " if i == len(entries) - 1 else "├── "
        lines.append(f"{prefix}{connector}{entry.name}")
        if entry.is_dir():
            extension = "    " if i == len(entries) - 1 else "│   "
            lines.append(_render_tree(entry, prefix + extension, effective_ignore))
    return "\n".join(lines)
