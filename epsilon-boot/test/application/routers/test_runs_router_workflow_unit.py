"""Run router workflow 字段透传单元测试。"""

from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from domain.run.exceptions import RunUnknownWorkflowError
from domain.run.value_objects import (
    RunEvent,
    RunEventType,
    RunKind,
    RunPayload,
    RunSnapshot,
    RunStatus,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _load_runs_module() -> Any:
    """直接加载兼容 Run 路由模块。"""

    runs_path = (
        pathlib.Path(__file__).resolve().parents[3] / "src" / "application" / "routers" / "runs.py"
    )
    spec = importlib.util.spec_from_file_location(
        "test_runs_router_workflow_module",
        str(runs_path),
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot() -> RunSnapshot:
    """构造带 workflow 字段的测试快照。"""

    payload = RunPayload(
        kind=RunKind.TASK,
        session_id="s1",
        task={"goal": "ship workflow"},
        model="test-model",
    )
    return RunSnapshot(
        run_id="run-1",
        kind=RunKind.TASK,
        status=RunStatus.PAUSED,
        payload=payload,
        client_request_id="req-1",
        payload_hash=payload.stable_hash(),
        result={"content": "phase done"},
        error=None,
        approval_id=None,
        segment_metadata={"segment_index": 1},
        latest_event_cursor=3,
        can_continue=True,
        terminal_reason="workflow_phase_completed",
        lease=None,
        created_at=_NOW,
        updated_at=_NOW,
        version=1,
        workflow_name="code_change",
        workflow_run_state={
            "workflow_name": "code_change",
            "current_phase": "execute",
            "phase_history": [{"phase": "plan", "status": "succeeded"}],
        },
        collaboration_summary={
            "delegation_count": 1,
            "recent_steps": [{"action": "delegation", "target_agent": "reviewer"}],
        },
    )


@pytest.mark.asyncio
async def test_create_run_request_passes_workflow_name_to_service() -> None:
    """创建请求中的 workflow_name 应只透传到应用服务。"""

    module = _load_runs_module()
    service = AsyncMock()
    service.create_run.return_value = _snapshot()

    response = await module.create_run(
        module.RunCreateRequestBody(
            kind="task",
            workflow_name="code_change",
            task=module.TaskRunCreateBody(session_id="s1", goal=" ship it "),
        ),
        service,
    )

    request = service.create_run.await_args.args[0]
    assert request.workflow_name == "code_change"
    assert request.payload.task["goal"] == "ship it"
    assert response.workflow_name == "code_change"


@pytest.mark.asyncio
async def test_snapshot_response_includes_workflow_fields() -> None:
    """Run 快照响应应透传 workflow 和协作摘要字段。"""

    module = _load_runs_module()
    service = AsyncMock()
    service.get_run.return_value = _snapshot()

    response = await module.get_run("run-1", service)

    body = response.model_dump(mode="json")
    assert body["workflow_name"] == "code_change"
    assert body["workflow_run_state"]["current_phase"] == "execute"
    assert body["collaboration_summary"]["delegation_count"] == 1
    assert body["collaboration_summary"]["latest_steps"] == [
        {"action": "delegation", "target_agent": "reviewer"}
    ]
    assert "recent_steps" not in body["collaboration_summary"]


@pytest.mark.asyncio
async def test_workflow_event_types_are_serialized_as_strings() -> None:
    """新增 workflow/collaboration 事件应沿用事件 payload 渲染路径。"""

    module = _load_runs_module()
    service = AsyncMock()
    service.list_events.return_value = [
        RunEvent(
            run_id="run-1",
            cursor=1,
            event_type=RunEventType.WORKFLOW_SELECTED,
            payload={"workflow_name": "code_change"},
            created_at=_NOW,
        ),
        RunEvent(
            run_id="run-1",
            cursor=2,
            event_type=RunEventType.COLLABORATION_STEP_RECORDED,
            payload={"action": "delegation"},
            created_at=_NOW,
        ),
    ]

    response = await module.get_run_events("run-1", None, 100, service)

    events = [event.model_dump(mode="json") for event in response.events]
    assert events[0]["event_type"] == "workflow_selected"
    assert events[1]["event_type"] == "collaboration_step_recorded"
    assert events[1]["payload"] == {"action": "delegation"}


@pytest.mark.asyncio
async def test_explicit_unknown_workflow_maps_to_400() -> None:
    """显式未知 workflow 的业务错误应映射为 400。"""

    module = _load_runs_module()
    service = AsyncMock()
    service.create_run.side_effect = RunUnknownWorkflowError("missing")

    response = await module.create_run(
        module.RunCreateRequestBody(
            kind="task",
            workflow_name="missing",
            task=module.TaskRunCreateBody(goal="ship"),
        ),
        service,
    )

    assert response.status_code == 400
    assert json.loads(response.body)["code"] == 61017


def test_runs_router_does_not_import_workflow_runtime_components() -> None:
    """HTTP adapter 不得直接依赖 workflow selector/orchestrator。"""

    router_path = (
        pathlib.Path(__file__).resolve().parents[3]
        / "src"
        / "application"
        / "api"
        / "routers"
        / "runs.py"
    )
    tree = ast.parse(router_path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    forbidden = {
        "infrastructure.run.static_workflow_selector",
        "application.run.workflow_orchestrator",
    }
    assert imports.isdisjoint(forbidden)
    text = router_path.read_text(encoding="utf-8")
    assert "StaticWorkflowSelector" not in text
    assert "WorkflowRunOrchestrator" not in text
    assert "collaboration_limit" not in text
