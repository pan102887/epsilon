"""受控文件工具的输出格式化辅助模块。

保持迁移前文件读取工具一致的输出格式（宽度 4 的右对齐行号 + ` | `
分隔符 + 行内容），便于 ReadFileTool 的成功返回消息在观感上对 LLM
透明一致。

依赖白名单：仅纯字符串处理。**禁止** import ``os`` / ``pathlib`` / ``open``。
"""

from __future__ import annotations


def _render_with_line_numbers(text: str, *, start_line: int = 1) -> str:
    """将文本按行切分并拼接带行号前缀的字符串。

    每行前缀形如 ``"   1 | "``（行号宽度 4、右对齐、后接 ` | `），与迁移前
    文件读取工具的输出保持一致。行号从 ``start_line`` 起
    递增（闭区间、1 起），便于与 ``Workspace.read(start_line=..., end_line=...)``
    的闭区间行范围语义无缝拼接。

    输入文本使用 ``str.splitlines()`` 切分，不保留尾随换行；空输入
    返回空串。

    Args:
        text: 已 decode 的文本内容。
        start_line: 首行的行号（1 起、闭区间），默认 ``1``。

    Returns:
        带行号前缀的多行文本，末尾不含额外换行。
    """
    if not text:
        return ""
    lines = text.splitlines()
    return "\n".join(f"{start_line + i:4d} | {line}" for i, line in enumerate(lines))
