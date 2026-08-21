"""聊天路由无效请求属性测试。

使用 Hypothesis 生成缺少必填字段或字段值非法的请求体，
验证 POST /api/chat 端点对所有无效输入均返回 HTTP 400 状态码。

测试覆盖两类无效请求：
1. Pydantic 校验失败：缺少必填字段、字段类型错误等，由 FastAPI RequestValidationError 处理。
2. 领域值对象校验失败：session_id 为空、message 为纯空白字符等，
   由 Router 层捕获 ValueError 并返回 400。

通过 importlib 直接加载 chat 路由模块，避免触发 application 包的
__init__.py 初始化副作用。
"""

import importlib.util
import inspect
import pathlib
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

# mock prometheus_client 以避免 Windows 平台兼容问题
if "prometheus_client" not in sys.modules:
    _mock_prom = MagicMock()
    _mock_prom.CONTENT_TYPE_LATEST = "text/plain"
    _mock_prom.generate_latest = MagicMock(return_value=b"")
    sys.modules["prometheus_client"] = _mock_prom


def _load_chat_module() -> Any:
    """直接加载 chat 路由模块，绕过 application 包的 __init__.py。

    使用 importlib 从文件路径加载 ``src/application/routers/chat.py``，
    避免触发 ``application/__init__.py`` 中 server_app 的完整初始化链。

    Returns:
        chat 路由模块对象
    """
    chat_path = (
        pathlib.Path(__file__).resolve().parents[3] / "src" / "application" / "routers" / "chat.py"
    )
    spec = importlib.util.spec_from_file_location(
        "test_chat_invalid_request_module", str(chat_path)
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_exception_handlers_module() -> Any:
    """直接加载异常处理模块，用于注册统一异常处理器。

    Returns:
        exception_handlers 模块对象
    """
    handlers_path = (
        pathlib.Path(__file__).resolve().parents[3]
        / "src"
        / "application"
        / "exception_handlers.py"
    )
    spec = importlib.util.spec_from_file_location(
        "test_exception_handlers_module", str(handlers_path)
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_chat_module = _load_chat_module()
_exception_handlers_module = _load_exception_handlers_module()
_router = _chat_module.router


def _get_service_dependency():
    """从 chat 路由函数的签名中提取 Chat_Service_Port 的 DI 依赖函数。

    FastAPI 的 dependency_overrides 要求使用与路由定义中完全相同的
    依赖函数对象作为 key。由于 inject() 每次调用返回不同的闭包，
    必须从路由函数的参数默认值中提取原始依赖函数。

    Returns:
        chat 路由中 service 参数的 Depends 内部依赖函数
    """
    chat_fn = _chat_module.chat
    sig = inspect.signature(chat_fn)
    depends_obj = sig.parameters["service"].default
    return depends_obj.dependency


_service_dep = _get_service_dependency()


def _create_test_app() -> FastAPI:
    """创建用于测试的 FastAPI 应用实例。

    构建一个包含 chat 路由和统一异常处理器的最小 FastAPI 应用，
    并通过 dependency_overrides 替换 Chat_Service_Port 的注入为 mock 对象。
    mock 对象在此测试中不会被实际调用，因为请求在校验阶段即被拒绝。

    Returns:
        配置好依赖覆盖和异常处理器的 FastAPI 测试应用
    """
    app = FastAPI()
    _exception_handlers_module.register_exception_handlers(app)
    app.include_router(_router)

    mock_service = AsyncMock()
    app.dependency_overrides[_service_dep] = lambda: mock_service

    return app


_app = _create_test_app()


# --- Hypothesis strategies ---

# 纯空白字符策略：生成仅由空格、制表符、换行符等组成的字符串
_whitespace_strategy = st.from_regex(r"^[\s]+$", fullmatch=True).filter(lambda s: len(s) <= 50)

# 非字符串类型策略：生成非字符串值用于类型错误测试
_non_string_strategy = st.one_of(
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.lists(st.integers(), max_size=3),
    st.none(),
    st.dictionaries(st.text(max_size=5), st.integers(), max_size=2),
)


# Feature: chat-chat-api, Property 9: 无效请求返回 400
@settings(max_examples=100, deadline=None)
@given(
    strategy_index=st.integers(min_value=0, max_value=4),
    whitespace_msg=_whitespace_strategy,
    non_string_val=_non_string_strategy,
    valid_session=st.text(min_size=1, max_size=50).filter(lambda s: s.strip()),
    valid_message=st.text(min_size=1, max_size=200).filter(lambda s: s.strip()),
)
@pytest.mark.asyncio
async def test_invalid_request_returns_400(
    strategy_index: int,
    whitespace_msg: str,
    non_string_val: object,
    valid_session: str,
    valid_message: str,
) -> None:
    """属性测试：无效请求返回 400。

    对任意缺少必填字段或字段值非法的 HTTP 请求体，
    POST /api/chat 端点始终返回 HTTP 400 状态码。

    测试覆盖以下无效场景：
    0. 缺少 session_id 字段
    1. 缺少 message 字段
    2. session_id 为空字符串（领域值对象校验拒绝）
    3. message 为纯空白字符（领域值对象校验拒绝）
    4. session_id 为非字符串类型（Pydantic 类型校验失败）

    Validates: Requirements 5.4
    """
    # 根据 strategy_index 选择不同的无效请求构造方式
    case = strategy_index % 5

    if case == 0:
        # 缺少 session_id
        body = {"message": valid_message}
    elif case == 1:
        # 缺少 message
        body = {"session_id": valid_session}
    elif case == 2:
        # session_id 为空字符串
        body = {"session_id": "", "message": valid_message}
    elif case == 3:
        # message 为纯空白字符
        body = {"session_id": valid_session, "message": whitespace_msg}
    else:
        # session_id 为非字符串类型
        body = {"session_id": non_string_val, "message": valid_message}

    if case in (0, 1, 4):
        with pytest.raises(ValidationError):
            _chat_module.ChatRequestBody.model_validate(body)
        return

    response = await _chat_module.chat(
        _chat_module.ChatRequestBody.model_validate(body),
        service=AsyncMock(),
    )

    assert response.status_code == 400, f"case={case}, body={body!r}, 响应={response.body!r}"
