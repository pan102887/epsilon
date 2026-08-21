"""``Tool.timeout_seconds`` 属性单元测试模块。

覆盖 PR-3 任务 3.8：

(a) ``Tool`` 默认子类 ``timeout_seconds`` 返回 ``None``；
(b) 子类 override ``> 0`` 后返回值生效；
(c) 既有抽象方法 ``name`` / ``description`` / ``parameters`` / ``execute`` 签名不变。
"""

from __future__ import annotations

import inspect
from typing import Any

from domain.agent.tools import Tool, ToolExecutionResult


class _MinimalTool(Tool):
    """最小工具实现：不覆盖 ``timeout_seconds``。"""

    @property
    def name(self) -> str:
        return "minimal"

    @property
    def description(self) -> str:
        return "minimal tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        return ToolExecutionResult(content="ok")


class _TimedTool(Tool):
    """工具子类：override ``timeout_seconds=0.5``。"""

    @property
    def name(self) -> str:
        return "timed"

    @property
    def description(self) -> str:
        return "timed tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def timeout_seconds(self) -> float | None:
        return 0.5

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        return ToolExecutionResult(content="ok")


def test_default_timeout_seconds_is_none() -> None:
    """(a) ``Tool`` 默认子类 ``timeout_seconds`` 返回 ``None``。"""
    assert _MinimalTool().timeout_seconds is None


def test_override_timeout_seconds_takes_effect() -> None:
    """(b) 子类 override ``> 0`` 后返回值生效。"""
    assert _TimedTool().timeout_seconds == 0.5


def test_existing_abstract_signatures_unchanged() -> None:
    """(c) 既有抽象成员签名保持不变。"""
    # name / description / parameters：均为 @property 抽象成员
    for prop_name in ("name", "description", "parameters"):
        assert prop_name in Tool.__abstractmethods__ or hasattr(Tool, prop_name)

    # execute：协程函数签名（self, **kwargs)
    assert inspect.iscoroutinefunction(Tool.execute)
    sig = inspect.signature(Tool.execute)
    param_names = list(sig.parameters.keys())
    assert param_names[0] == "self"
    # 末位允许 VAR_KEYWORD（**kwargs）
    assert any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())


def test_timeout_seconds_is_property_not_abstract() -> None:
    """``timeout_seconds`` 是带默认实现的 property，不强制子类实现。"""
    # 不在 abstractmethods 中
    assert "timeout_seconds" not in Tool.__abstractmethods__
    # 默认子类可正常实例化
    _MinimalTool()
