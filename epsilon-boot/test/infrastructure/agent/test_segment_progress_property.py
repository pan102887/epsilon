"""分段执行进展分析属性测试。"""

from __future__ import annotations

import json

from hypothesis import given
from hypothesis import strategies as st

from infrastructure.agent.segmented_progress import normalized_tool_call_digest


@given(
    tool_name=st.text(min_size=1, max_size=20),
    payload=st.dictionaries(
        keys=st.text(min_size=1, max_size=10),
        values=st.one_of(st.integers(), st.text(max_size=20), st.booleans()),
        min_size=1,
        max_size=5,
    ),
)
def test_digest_is_stable_for_equivalent_json_objects(
    tool_name: str,
    payload: dict[str, object],
) -> None:
    """等价 JSON 对象无论键顺序如何都应生成相同摘要。"""
    left = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    right = json.dumps(dict(reversed(list(payload.items()))), ensure_ascii=False)

    assert normalized_tool_call_digest(tool_name, left) == normalized_tool_call_digest(
        tool_name,
        right,
    )


@given(
    tool_name=st.text(min_size=1, max_size=20),
    raw_arguments=st.text(max_size=100),
)
def test_digest_is_deterministic_for_any_raw_argument(
    tool_name: str,
    raw_arguments: str,
) -> None:
    """任意原始参数字符串的摘要必须稳定。"""
    assert normalized_tool_call_digest(tool_name, raw_arguments) == normalized_tool_call_digest(
        tool_name,
        raw_arguments,
    )
