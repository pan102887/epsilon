"""上下文构建端口签名静态测试。"""

import inspect
import typing

from domain.chat.context import BaseMessage
from domain.chat.ports import ContextBuilderPort
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.ports import ModelAccessPort


def test_context_builder_port_build_is_async() -> None:
    """上下文构建端口 build 必须支持异步调用。"""
    assert inspect.iscoroutinefunction(ContextBuilderPort.build)


def test_context_builder_port_build_accepts_messages_argument() -> None:
    """上下文构建端口 build 必须接收完整消息列表参数。"""
    signature = inspect.signature(ContextBuilderPort.build)

    messages = signature.parameters["messages"]

    assert messages.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert messages.default is inspect.Parameter.empty


def test_context_builder_port_build_has_model_keyword_options() -> None:
    """上下文构建端口 build 暴露当前模型访问上下文关键字参数。"""
    signature = inspect.signature(ContextBuilderPort.build)

    assert signature.parameters["model_access"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["model_access"].default is None
    assert signature.parameters["model"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["model"].default is None


def test_context_builder_port_build_returns_result_object() -> None:
    """上下文构建端口 build 返回 ContextBuilderResult。"""
    hints = typing.get_type_hints(
        ContextBuilderPort.build,
        globalns={
            "BaseMessage": BaseMessage,
            "ModelAccessPort": ModelAccessPort,
            "ContextBuilderResult": ContextBuilderResult,
        },
    )

    assert hints["return"] is ContextBuilderResult
