"""上下文压缩端口签名静态测试。"""

import inspect
import typing

from domain.chat.context import BaseMessage
from domain.chat.ports import ContextCompactionPort
from domain.chat.value_objects import ContextCompactionResult
from domain.model_access.ports import ModelAccessPort


def test_context_compaction_port_compact_is_async() -> None:
    """压缩端口 compact 必须支持异步调用。"""
    assert inspect.iscoroutinefunction(ContextCompactionPort.compact)


def test_context_compaction_port_compact_has_model_keyword_options() -> None:
    """压缩端口 compact 暴露当前模型访问上下文关键字参数。"""
    signature = inspect.signature(ContextCompactionPort.compact)

    assert signature.parameters["model_access"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["model_access"].default is None
    assert signature.parameters["model"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["model"].default is None


def test_context_compaction_port_compact_returns_result_object() -> None:
    """压缩端口 compact 返回 ContextCompactionResult。"""
    hints = typing.get_type_hints(
        ContextCompactionPort.compact,
        globalns={
            "BaseMessage": BaseMessage,
            "ModelAccessPort": ModelAccessPort,
            "ContextCompactionResult": ContextCompactionResult,
        },
    )

    assert hints["return"] is ContextCompactionResult
