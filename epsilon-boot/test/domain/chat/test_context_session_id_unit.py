"""ConversationContext.session_id 字段升级单元测试模块。

验证 v2 把 ``session_id`` 由 ``setattr`` 隐式挂载升级为正式可选字段后的行为：

- 默认值为 ``None``,可直接读取;
- 直接赋值生效,无需 setattr;
- 通过 ``to_dict`` / ``from_dict`` 回环后取值一致;
- 在 ``ChatServiceAdapter`` 入口直接赋值后,``ReActAgentAdapter._save_interrupt``
  通过 ``context.session_id or ""`` 读取,不再依赖 hasattr 兜底。

覆盖需求 5.2 / 5.5 / 5.8 部分。
"""

from domain.chat.context import ConversationContext


class TestSessionIdField:
    """验证 ``session_id`` 字段升级。"""

    def test_default_value_is_none(self) -> None:
        """默认实例的 session_id 应为 None。"""
        ctx = ConversationContext()
        assert ctx.session_id is None

    def test_direct_assignment_takes_effect(self) -> None:
        """直接赋值 ``ctx.session_id = "sess-1"`` 应立即生效。"""
        ctx = ConversationContext()
        ctx.session_id = "sess-1"
        assert ctx.session_id == "sess-1"

    def test_to_dict_omits_when_none(self) -> None:
        """session_id 为默认值 None 时 to_dict 不输出 session_id 键。"""
        ctx = ConversationContext()
        data = ctx.to_dict()
        assert "session_id" not in data

    def test_to_dict_includes_when_set(self) -> None:
        """session_id 非 None 时 to_dict 应输出该键。"""
        ctx = ConversationContext()
        ctx.session_id = "sess-2"
        data = ctx.to_dict()
        assert data["session_id"] == "sess-2"

    def test_from_dict_then_to_dict_roundtrip(self) -> None:
        """to_dict / from_dict 回环后 session_id 取值一致。"""
        ctx = ConversationContext()
        ctx.session_id = "sess-roundtrip"
        restored = ConversationContext.from_dict(ctx.to_dict())
        assert restored.session_id == "sess-roundtrip"

    def test_or_empty_fallback_for_none(self) -> None:
        """``context.session_id or ""`` 在 None 时回退为空串,与 _save_interrupt 兼容。"""
        ctx = ConversationContext()
        assert (ctx.session_id or "") == ""
        ctx.session_id = "sess-not-empty"
        assert (ctx.session_id or "") == "sess-not-empty"
