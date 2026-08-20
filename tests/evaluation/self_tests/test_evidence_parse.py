"""证据解析自测。

覆盖点：
- `parse_reference()` 合法 4 种形式（code_lines 区间、code_lines 单行、
  path_only、config_key）能正确分发到对应 `EvidenceKind`；
- 非法输入一律抛 `EvidenceFormatError`（空字符串、纯空白、含空格、
  通配符、仅目录、零/负行号、start > end）。

对应 Property：Property 4；对应需求：3.2、3.3。
"""

from __future__ import annotations

import pytest

from tests.evaluation.errors import EvidenceFormatError
from tests.evaluation.evidence import EvidenceKind, parse_reference


@pytest.mark.parametrize(
    "raw,expected_kind,expected_start,expected_end",
    [
        ("epsilon-boot/src/foo.py:10-42", EvidenceKind.CODE_LINES, 10, 42),
        ("epsilon-boot/src/foo.py:L10-L42", EvidenceKind.CODE_LINES, 10, 42),
        ("epsilon-boot/src/foo.py:L10", EvidenceKind.CODE_LINES, 10, 10),
        ("epsilon-boot/src/foo.py:10", EvidenceKind.CODE_LINES, 10, 10),
        ("docs/steering/ddd-architecture.md", EvidenceKind.PATH_ONLY, None, None),
        (
            "epsilon-boot/config.properties:MODEL_DEFAULT",
            EvidenceKind.CONFIG_KEY,
            None,
            None,
        ),
    ],
)
def test_parse_reference_valid_forms(
    raw: str,
    expected_kind: EvidenceKind,
    expected_start: int | None,
    expected_end: int | None,
) -> None:
    """合法输入应正确分发到对应的 EvidenceKind。"""

    ref = parse_reference(raw, "desc")
    assert ref.kind == expected_kind
    assert ref.line_start == expected_start
    assert ref.line_end == expected_end
    assert ref.description == "desc"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "foo bar.py:1",            # 含空格
        "foo/*.py:1",               # 通配符
        "foo/:1",                   # 仅目录
        "foo.py:-1",                # 负数在格式层就不匹配
        "foo.py:0",                 # 零行号
        "foo.py:10-5",              # start > end
        "foo.py:",                  # 冒号后为空
        "foo.py:abc",               # 行号非数字
        "foo.py:L10-",              # 不完整区间
    ],
)
def test_parse_reference_invalid_raises(raw: str) -> None:
    """非法输入必须抛 EvidenceFormatError。"""

    with pytest.raises(EvidenceFormatError):
        parse_reference(raw, "desc")


def test_parse_reference_config_key_requires_nondigit_suffix() -> None:
    """config.properties 后跟纯数字时应归入 CODE_LINES，非纯数字（如键名）归入 CONFIG_KEY。"""

    code = parse_reference("epsilon-boot/config.properties:42", "x")
    assert code.kind == EvidenceKind.CODE_LINES
    assert code.line_start == 42 and code.line_end == 42

    key = parse_reference("epsilon-boot/config.properties:MODEL_DEFAULT", "x")
    assert key.kind == EvidenceKind.CONFIG_KEY


def test_parse_reference_non_string_raises() -> None:
    """非字符串输入也应以 EvidenceFormatError 形式拒绝，避免类型混淆。"""

    with pytest.raises(EvidenceFormatError):
        parse_reference(123, "x")  # type: ignore[arg-type]
