"""Artifact 查询路由单元测试。"""

import importlib.util
import json
import pathlib
from unittest.mock import AsyncMock

import pytest

from domain.agent.ports import ArtifactStorePort
from domain.agent.trace_value_objects import ArtifactTrace


def _load_artifacts_module():
    """直接加载 artifacts 路由模块，绕过 application 包初始化。"""

    artifacts_path = (
        pathlib.Path(__file__).resolve().parents[3]
        / "src"
        / "application"
        / "api"
        / "routers"
        / "artifacts.py"
    )
    spec = importlib.util.spec_from_file_location(
        "test_artifacts_router_module", str(artifacts_path)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_artifacts_module = _load_artifacts_module()


@pytest.mark.asyncio
async def test_list_artifacts_returns_session_items() -> None:
    """验证 artifact 列表接口按 session 返回摘要记录。"""

    store = AsyncMock(spec=ArtifactStorePort)
    store.list_artifacts.return_value = [
        ArtifactTrace(
            session_id="s1",
            logical_path="reports/result.md",
            artifact_type="file",
            timestamp_epoch=100.0,
            size_bytes=12,
            content_summary="done",
            source_tool="write_file",
        )
    ]

    response = await _artifacts_module.list_artifacts("s1", artifact_store=store)

    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["object"] == "list"
    assert body["session_id"] == "s1"
    assert body["data"][0]["kind"] == "artifact"
    assert body["data"][0]["logical_path"] == "reports/result.md"
    store.list_artifacts.assert_awaited_once_with("s1")


@pytest.mark.asyncio
async def test_list_artifacts_empty_when_disabled() -> None:
    """Artifact store 关闭时返回空列表。"""

    response = await _artifacts_module.list_artifacts("s1", artifact_store=None)

    assert response.status_code == 200
    assert json.loads(response.body) == {"object": "list", "data": []}
