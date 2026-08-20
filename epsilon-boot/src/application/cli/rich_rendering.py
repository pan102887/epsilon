"""TUI 富文本渲染辅助。"""

from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser

from rich.markdown import Markdown
from rich.text import Text

_TAG_PATTERN = re.compile(r"</?[a-zA-Z][^>]*>")


def render_markdown_body(body: str) -> Markdown:
    """把模型输出渲染为终端友好的 Markdown。

    Rich Markdown 不会执行 HTML。本函数先把常见 HTML 块/内联标签降级成
    Markdown/纯文本，再交给 Rich 渲染，覆盖模型常输出的 ``<h*>``、``<p>``、
    ``<ul>/<li>``、``<pre>/<code>``、``<a>`` 等结构。
    """

    return Markdown(_html_to_markdown(body), code_theme="monokai", hyperlinks=True)


def render_plain_body(body: str) -> Text:
    """按纯文本渲染，不解释 Markdown。"""

    return Text(body)


def _html_to_markdown(body: str) -> str:
    if not _TAG_PATTERN.search(body):
        return body
    parser = _HtmlToMarkdownParser()
    parser.feed(body)
    parser.close()
    return parser.output()


class _HtmlToMarkdownParser(HTMLParser):
    """把有限 HTML 子集转换为 Markdown。

    该转换器不是浏览器渲染器，只负责把 LLM 常见 HTML 输出变成终端里可读、
    可继续交给 Markdown 渲染的文本。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._parts: list[str] = []
        self._href_stack: list[str | None] = []
        self._list_depth = 0
        self._pre_depth = 0
        self._code_depth = 0
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        attrs_map = dict(attrs)
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(tag[1])
            self._ensure_blank_line()
            self._parts.append("#" * level + " ")
        elif tag == "p":
            self._ensure_blank_line()
        elif tag == "br":
            self._parts.append("\n")
        elif tag in {"ul", "ol"}:
            self._list_depth += 1
            self._ensure_newline()
        elif tag == "li":
            self._ensure_newline()
            self._parts.append("  " * max(self._list_depth - 1, 0) + "- ")
        elif tag == "blockquote":
            self._ensure_blank_line()
            self._parts.append("> ")
        elif tag == "pre":
            self._pre_depth += 1
            self._ensure_blank_line()
            self._parts.append("```\n")
        elif tag == "code" and not self._pre_depth:
            self._code_depth += 1
            self._parts.append("`")
        elif tag in {"strong", "b"}:
            self._parts.append("**")
        elif tag in {"em", "i"}:
            self._parts.append("*")
        elif tag == "a":
            self._href_stack.append(attrs_map.get("href"))
            self._parts.append("[")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style"}:
            self._skip_depth = max(self._skip_depth - 1, 0)
            return
        if self._skip_depth:
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "blockquote"}:
            self._ensure_blank_line()
        elif tag in {"ul", "ol"}:
            self._list_depth = max(self._list_depth - 1, 0)
            self._ensure_newline()
        elif tag == "li":
            self._ensure_newline()
        elif tag == "pre":
            self._pre_depth = max(self._pre_depth - 1, 0)
            self._ensure_newline()
            self._parts.append("```\n")
        elif tag == "code" and self._code_depth:
            self._code_depth = max(self._code_depth - 1, 0)
            self._parts.append("`")
        elif tag in {"strong", "b"}:
            self._parts.append("**")
        elif tag in {"em", "i"}:
            self._parts.append("*")
        elif tag == "a":
            href = self._href_stack.pop() if self._href_stack else None
            self._parts.append(f"]({href})" if href else "]")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def handle_entityref(self, name: str) -> None:
        if not self._skip_depth:
            self._parts.append(unescape(f"&{name};"))

    def handle_charref(self, name: str) -> None:
        if not self._skip_depth:
            self._parts.append(unescape(f"&#{name};"))

    def output(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "".join(self._parts)).strip()

    def _ensure_newline(self) -> None:
        if self._parts and not self._parts[-1].endswith("\n"):
            self._parts.append("\n")

    def _ensure_blank_line(self) -> None:
        text = "".join(self._parts)
        if not text:
            return
        if text.endswith("\n\n"):
            return
        if text.endswith("\n"):
            self._parts.append("\n")
        else:
            self._parts.append("\n\n")
