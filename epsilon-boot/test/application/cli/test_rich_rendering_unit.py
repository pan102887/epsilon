"""TUI 富文本渲染测试。"""

from __future__ import annotations

from io import StringIO

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

from application.cli.rich_rendering import render_markdown_body, render_plain_body


def _render_to_text(markdown: Markdown) -> str:
    output = StringIO()
    console = Console(file=output, force_terminal=False, width=80)
    console.print(markdown)
    return output.getvalue()


def test_render_markdown_body_renders_markdown_constructs() -> None:
    """Markdown 输出应交给 Rich Markdown 处理。"""

    rendered = _render_to_text(render_markdown_body("# Title\n\n- item\n\n```py\nprint(1)\n```"))

    assert "Title" in rendered
    assert "item" in rendered
    assert "print(1)" in rendered


def test_render_markdown_body_converts_common_html_elements() -> None:
    """常见 HTML 标签应转换为终端可读的 Markdown 结构。"""

    rendered = _render_to_text(
        render_markdown_body(
            "<h2>Plan</h2><p><strong>Next</strong> step</p>"
            "<ul><li>read</li><li>write</li></ul>"
            '<p><a href="https://example.com">link</a></p>'
        )
    )

    assert "Plan" in rendered
    assert "Next" in rendered
    assert "read" in rendered
    assert "write" in rendered
    assert "link" in rendered


def test_render_plain_body_does_not_interpret_markdown() -> None:
    """工具参数和错误输出仍可按纯文本展示。"""

    renderable = render_plain_body("**not bold**")

    assert isinstance(renderable, Text)
    assert renderable.plain == "**not bold**"
