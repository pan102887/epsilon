"""指标 3 元测试：验证固定 ``L=30, S=3, N=10`` 组合下的滑动窗口不变量。

验证策略：
    - 直接构造 ``L=30, S=3, N=10`` 的消息序列；
    - 调用真实 :class:`SlidingWindowCompactionAdapter.compact_messages`（与指标样本
      共用一套被测单元）；
    - 断言压缩后总长度 ``= 3 + 10 = 13``、SystemMessage 计数为 3、
      非 system 消息顺序与原始后 10 条一致。

对应 Property 8（SystemMessage 无损保留），并验证"压缩后非 system 数
``= min(L - S, N)`` 且保持原始顺序"不变量。
"""

from __future__ import annotations

import pytest

from domain.chat.context import (
    AssistantMessage,
    BaseMessage,
    SystemMessage,
    UserMessage,
)
from infrastructure.chat.sliding_window_compaction_adapter import (
    SlidingWindowCompactionAdapter,
)


def _build_fixed_messages() -> list[BaseMessage]:
    """构造 ``L=30, S=3`` 的固定消息序列。

    - 前 3 条为 :class:`SystemMessage`，content 依次为 ``"system-00"`` /
      ``"system-01"`` / ``"system-02"``；
    - 其余 27 条交错 :class:`UserMessage` / :class:`AssistantMessage`，
      content 形如 ``"user-00"`` / ``"assistant-01"``，保证逐字符可比较。

    Returns:
        长度为 30 的消息列表。
    """

    messages: list[BaseMessage] = [
        SystemMessage(content=f"system-{i:02d}") for i in range(3)
    ]
    for idx in range(27):
        if idx % 2 == 0:
            messages.append(UserMessage(content=f"user-{idx:02d}"))
        else:
            messages.append(AssistantMessage(content=f"assistant-{idx:02d}"))
    return messages


@pytest.mark.evaluation_self
def test_compact_preserves_system_messages_and_last_n_non_system() -> None:
    """断言 ``L=30, S=3, N=10`` 压缩结果满足三项判据。

    断言集：
        - 压缩后总长度 = ``3 + 10 = 13``；
        - SystemMessage 数量恒为 3；
        - 非 system 消息为原非 system 子列的末尾 10 条，按原始顺序；
        - SystemMessage 内容与原序列前 3 条逐字符相等（无损保留）。
    """

    messages = _build_fixed_messages()
    non_system_original = [m for m in messages if m.role != "system"]

    adapter = SlidingWindowCompactionAdapter(max_messages=10)
    compacted = adapter.compact_messages(messages)

    assert len(compacted) == 13

    compacted_system = [m for m in compacted if m.role == "system"]
    assert len(compacted_system) == 3
    assert [m.content for m in compacted_system] == [
        f"system-{i:02d}" for i in range(3)
    ]

    compacted_non_system = [m for m in compacted if m.role != "system"]
    assert len(compacted_non_system) == 10
    assert [m.content for m in compacted_non_system] == [
        m.content for m in non_system_original[-10:]
    ]
