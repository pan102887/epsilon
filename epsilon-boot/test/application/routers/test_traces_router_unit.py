"""结构化 Agent 追踪查询路由单元测试。

针对 traces.py 路由模块的 ``GET /api/traces`` 与 ``GET /api/traces/{session_id}``
编写单元测试。通过 FastAPI dependency_overrides 机制替换 TraceStorePort 的
DI 注入，使用 AsyncMock 控制返回值，验证：

- 列表接口按 limit 查询并返回摘要数组；
- 详情接口返回完整 steps；
- trace 不存在时返回 404；
- 追踪关闭（trace_store 为 None）时列表返回空数组、详情返回 404。

参照 test_health.py 的做法：预先 mock prometheus_client 并用 importlib 直接
加载路由模块，避免触发 application 包 __init__.py 的完整初始化副作用。
"""

import importlib.util
import inspect
import json
import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.ports import TraceStorePort
from domain.agent.trace_value_objects import (
    ModelCallTrace,
    SessionTrace,
    ToolCallTrace,
)

# 与 test_health 相同：在导入路由前 mock prometheus_client，规避平台兼容问题。
if "prometheus_client" not in sys.modules:
    _mock_prom = MagicMock()
    _mock_prom.CONTENT_TYPE_LATEST = "text/plain"
    _mock_prom.generate_latest = MagicMock(return_value=b"")
    sys.modules["prometheus_client"] = _mock_prom


def _load_traces_module():
    """直接加载 traces 路由模块，绕过 application 包的 __init__.py。"""
    traces_path = (
        pathlib.Path(__file__).resolve().parents[3]
        / "src"
        / "application"
        / "api"
        / "routers"
        / "traces.py"
    )
    spec = importlib.util.spec_from_file_location("test_traces_module", str(traces_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_traces_module = _load_traces_module()
router = _traces_module.router


def _get_dependency(route_fn_name: str):
    """从指定路由函数签名中提取 trace_store 的 Depends 内部依赖函数。

    FastAPI 的 dependency_overrides 要求使用与路由定义中完全相同的依赖函数对象
    作为 key；由于 inject() 每次调用返回不同闭包，必须从路由函数参数默认值提取。
    """
    route_fn = getattr(_traces_module, route_fn_name)
    sig = inspect.signature(route_fn)
    depends_obj = sig.parameters["trace_store"].default
    return depends_obj.dependency


_list_dep = _get_dependency("list_traces")
_get_dep = _get_dependency("get_trace")


# ── GET /api/traces ──


@pytest.mark.asyncio
async def test_list_traces_returns_summaries() -> None:
    """验证列表接口返回摘要数组，并按 limit 调用底层 store。"""
    mock_store = AsyncMock(spec=TraceStorePort)
    mock_store.list_traces.return_value = [
        SessionTrace(
            session_id="s1",
            started_at_epoch=100.0,
            steps=[],
            metadata={"step_count": 3},
        ),
    ]

    response = await _traces_module.list_traces(limit=5, trace_store=mock_store)

    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["object"] == "list"
    assert len(body["data"]) == 1
    assert body["data"][0] == {
        "session_id": "s1",
        "started_at_epoch": 100.0,
        "step_count": 3,
    }
    mock_store.list_traces.assert_awaited_once_with(limit=5)


def test_list_traces_rejects_out_of_range_limit() -> None:
    """验证 limit 参数保留 FastAPI Query 的 [1, 200] 校验约束。"""
    query = inspect.signature(_traces_module.list_traces).parameters["limit"].default
    metadata = query.metadata

    assert any(getattr(item, "ge", None) == 1 for item in metadata)
    assert any(getattr(item, "le", None) == 200 for item in metadata)


@pytest.mark.asyncio
async def test_list_traces_empty_when_tracing_disabled() -> None:
    """验证追踪关闭（store 为 None）时列表返回空数组。"""
    response = await _traces_module.list_traces(trace_store=None)

    assert response.status_code == 200
    assert json.loads(response.body) == {"object": "list", "data": []}


# ── GET /api/traces/{session_id} ──


@pytest.mark.asyncio
async def test_get_trace_returns_full_steps() -> None:
    """验证详情接口返回完整 steps 与元数据。"""
    mock_store = AsyncMock(spec=TraceStorePort)
    mock_store.get_session_trace.return_value = SessionTrace(
        session_id="s1",
        started_at_epoch=100.0,
        steps=[
            ModelCallTrace(
                round_num=1,
                model="glm-4",
                prompt_id="p1",
                input_tokens=10,
                output_tokens=20,
                latency_ms=12.5,
                timestamp_epoch=100.0,
            ),
            ToolCallTrace(
                round_num=1,
                tool_name="read_file",
                tool_call_id="tc1",
                arguments_summary="{...}",
                result_summary="ok",
                success=True,
                latency_ms=3.0,
                timestamp_epoch=101.0,
            ),
        ],
    )

    response = await _traces_module.get_trace("s1", trace_store=mock_store)

    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["session_id"] == "s1"
    assert body["step_count"] == 2
    assert body["steps"][0]["kind"] == "model_call"
    assert body["steps"][0]["model"] == "glm-4"
    assert body["steps"][1]["kind"] == "tool_call"
    assert body["steps"][1]["tool_name"] == "read_file"
    mock_store.get_session_trace.assert_awaited_once_with("s1")


@pytest.mark.asyncio
async def test_get_trace_not_found_returns_404() -> None:
    """验证 trace 不存在时返回 404。"""
    mock_store = AsyncMock(spec=TraceStorePort)
    mock_store.get_session_trace.return_value = None

    response = await _traces_module.get_trace("missing", trace_store=mock_store)

    assert response.status_code == 404
    assert "missing" in json.loads(response.body)["detail"]


@pytest.mark.asyncio
async def test_get_trace_404_when_tracing_disabled() -> None:
    """验证追踪关闭（store 为 None）时详情返回 404。"""
    response = await _traces_module.get_trace("s1", trace_store=None)

    assert response.status_code == 404
