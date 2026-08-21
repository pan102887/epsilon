"""Run HTTP router 单元测试。"""

from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from unittest.mock import AsyncMock

import pytest
from starlette.responses import Response

from domain.run.exceptions import (
    RunCancelUnavailableError,
    RunCheckpointStoreUnavailableError,
    RunContinuationUnavailableError,
    RunEventReplayExpiredError,
    RunIdempotencyConflictError,
    RunNotFoundError,
    RunQueueFullError,
    RunRecoveryUnavailableError,
)
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
        "test_runs_router_module",
        str(runs_path),
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot(
    *,
    run_id: str = "run-1",
    kind: RunKind = RunKind.CHAT,
    status: RunStatus = RunStatus.QUEUED,
    can_continue: bool = False,
) -> RunSnapshot:
    """构造测试快照。"""

    payload = RunPayload(
        kind=kind,
        session_id="s1",
        chat={"message": "hello"} if kind is RunKind.CHAT else None,
        task={"goal": "ship"} if kind is RunKind.TASK else None,
        model="test-model",
    )
    return RunSnapshot(
        run_id=run_id,
        kind=kind,
        status=status,
        payload=payload,
        client_request_id="req-1",
        payload_hash=payload.stable_hash(),
        result={"content": "done"} if status is RunStatus.SUCCEEDED else None,
        error={"message": "failed"} if status is RunStatus.FAILED else None,
        approval_id="approval-1" if status is RunStatus.AWAITING_APPROVAL else None,
        segment_metadata={"segment_index": 1, "segment_count": 2},
        latest_event_cursor=3,
        can_continue=can_continue,
        terminal_reason="completed" if status is RunStatus.SUCCEEDED else None,
        lease=None,
        created_at=_NOW,
        updated_at=_NOW,
        version=1,
    )


def _event(cursor: int, event_type: RunEventType) -> RunEvent:
    """构造测试事件。"""

    return RunEvent(
        run_id="run-1",
        cursor=cursor,
        event_type=event_type,
        payload={"cursor": cursor},
        created_at=_NOW,
    )


def _json_response_body(response: Response) -> dict[str, object]:
    """解析 JSONResponse body。"""
    return cast(dict[str, object], json.loads(bytes(response.body)))


class _SseResponse(Protocol):
    body_iterator: AsyncIterator[dict[str, object] | bytes | str]


async def _sse_text(response: _SseResponse) -> str:
    """读取 EventSourceResponse 的文本内容。"""
    parts: list[str] = []
    async for item in response.body_iterator:
        if isinstance(item, dict):
            if "event" in item:
                parts.append(f"event: {item['event']}")
            if "data" in item:
                parts.append(f"data: {item['data']}")
            continue
        parts.append(item.decode("utf-8") if isinstance(item, bytes) else str(item))
    return "\n".join(parts)


@pytest.mark.asyncio
async def test_create_chat_run_converts_request_to_application_service() -> None:
    """验证创建 chat run 只调用应用服务。"""

    service = AsyncMock()
    service.create_run.return_value = _snapshot()
    module = _load_runs_module()

    response = await module.create_run(
        module.RunCreateRequestBody(
            kind="chat",
            client_request_id="req-chat",
            chat=module.ChatRunCreateBody(session_id="s1", message=" hello ", model="m1"),
        ),
        service,
    )

    body = response.model_dump(mode="json")
    assert body["run_id"] == "run-1"
    assert body["status"] == "queued"
    request = service.create_run.await_args.args[0]
    assert request.payload.kind is RunKind.CHAT
    assert request.payload.chat == {"message": "hello"}
    assert request.client_request_id == "req-chat"


@pytest.mark.asyncio
async def test_create_task_run_converts_request_to_application_service() -> None:
    """验证创建 task run 载荷转换。"""

    service = AsyncMock()
    service.create_run.return_value = _snapshot(kind=RunKind.TASK)
    module = _load_runs_module()

    await module.create_run(
        module.RunCreateRequestBody(
            kind="task",
            task=module.TaskRunCreateBody(
                session_id="s1",
                goal=" ship it ",
                input_data={"k": "v"},
                constraints=["c1"],
            ),
        ),
        service,
    )

    request = service.create_run.await_args.args[0]
    assert request.payload.kind is RunKind.TASK
    assert request.payload.task["goal"] == "ship it"
    assert request.payload.task["input_data"] == {"k": "v"}


@pytest.mark.asyncio
async def test_create_invalid_payload_returns_400() -> None:
    """验证 payload 校验错误映射为 400。"""

    service = AsyncMock()
    module = _load_runs_module()

    response = await module.create_run(module.RunCreateRequestBody(kind="chat"), service)

    assert response.status_code == 400
    assert _json_response_body(response)["code"] == 61008
    service.create_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_idempotency_conflict_returns_409() -> None:
    """验证幂等冲突映射为 409。"""

    service = AsyncMock()
    service.create_run.side_effect = RunIdempotencyConflictError("req-1")
    module = _load_runs_module()

    response = await module.create_run(
        module.RunCreateRequestBody(
            kind="chat",
            client_request_id="req-1",
            chat=module.ChatRunCreateBody(session_id="s1", message="hello"),
        ),
        service,
    )

    assert response.status_code == 409
    assert _json_response_body(response)["code"] == 61010


@pytest.mark.asyncio
async def test_create_queue_full_returns_429() -> None:
    """验证队列满映射为 429。"""

    service = AsyncMock()
    service.create_run.side_effect = RunQueueFullError("max_queued_runs", 1)
    module = _load_runs_module()

    response = await module.create_run(
        module.RunCreateRequestBody(
            kind="chat",
            chat=module.ChatRunCreateBody(session_id="s1", message="hello"),
        ),
        service,
    )

    assert response.status_code == 429
    assert _json_response_body(response)["code"] == 61002


@pytest.mark.asyncio
async def test_get_run_not_found_returns_404() -> None:
    """验证查询不存在映射为 404。"""

    service = AsyncMock()
    service.get_run.side_effect = RunNotFoundError("missing")
    module = _load_runs_module()

    response = await module.get_run("missing", service)

    assert response.status_code == 404
    assert _json_response_body(response)["code"] == 61001


@pytest.mark.asyncio
async def test_get_run_response_includes_checkpoint_recovery_fields() -> None:
    """Run 快照响应应暴露 checkpoint/recovery 观察字段。"""

    service = AsyncMock()
    service.get_run.return_value = replace(
        _snapshot(),
        latest_checkpoint_id="chk_000001",
        recoverable=True,
        recovery_attempt_count=2,
        last_recovery_error={"reason": "schema_mismatch"},
    )
    module = _load_runs_module()

    response = await module.get_run("run-1", service)

    body = response.model_dump(mode="json")
    assert body["latest_checkpoint_id"] == "chk_000001"
    assert body["recoverable"] is True
    assert body["recovery_attempt_count"] == 2
    assert body["last_recovery_error"] == {"reason": "schema_mismatch"}


@pytest.mark.asyncio
async def test_get_run_response_includes_guardrail_fields() -> None:
    """Run 快照响应应透传任务分类和 guardrail 摘要字段。"""

    service = AsyncMock()
    service.get_run.return_value = replace(
        _snapshot(),
        task_classification="tool_task",
        guardrail_summary={
            "mode": "observe",
            "action": "observe",
            "reason": "tool_risk_gate_required",
        },
    )
    module = _load_runs_module()

    response = await module.get_run("run-1", service)

    body = response.model_dump(mode="json")
    assert body["task_classification"] == "tool_task"
    assert body["guardrail_summary"] == {
        "mode": "observe",
        "action": "observe",
        "reason": "tool_risk_gate_required",
    }


@pytest.mark.asyncio
async def test_checkpoint_recovery_errors_map_to_http_status() -> None:
    """checkpoint/recovery BizException 应映射到明确 HTTP 状态。"""

    recovery_service = AsyncMock()
    recovery_service.get_run.side_effect = RunRecoveryUnavailableError(
        "run-1",
        "pending_tool_replay_blocked",
    )
    store_service = AsyncMock()
    store_service.get_run.side_effect = RunCheckpointStoreUnavailableError(
        "latest_checkpoint",
        "redis timeout",
    )
    module = _load_runs_module()

    recovery_response = await module.get_run("run-1", recovery_service)
    store_response = await module.get_run("run-1", store_service)

    assert recovery_response.status_code == 409
    assert _json_response_body(recovery_response)["code"] == 61013
    assert store_response.status_code == 503
    assert _json_response_body(store_response)["code"] == 61016


@pytest.mark.asyncio
async def test_get_events_returns_cursor_response() -> None:
    """验证事件轮询响应包含 cursor。"""

    service = AsyncMock()
    service.list_events.return_value = [
        _event(2, RunEventType.RUN_QUEUED),
        _event(3, RunEventType.RUN_SUCCEEDED),
    ]
    module = _load_runs_module()

    response = await module.get_run_events("run-1", 1, 2, service)

    body = response.model_dump(mode="json")
    assert body["latest_cursor"] == 3
    assert [event["cursor"] for event in body["events"]] == [2, 3]
    service.list_events.assert_awaited_once_with("run-1", 1, 2)


@pytest.mark.asyncio
async def test_get_events_replay_expired_returns_409() -> None:
    """验证事件 replay 过期的 polling 响应。"""

    service = AsyncMock()
    service.list_events.side_effect = RunEventReplayExpiredError("run-1", 1)
    module = _load_runs_module()

    response = await module.get_run_events("run-1", 1, 100, service)

    assert response.status_code == 409
    assert _json_response_body(response)["code"] == 61007


@pytest.mark.asyncio
async def test_stream_events_replay_expired_sends_control_event() -> None:
    """验证 SSE replay_expired 控制事件。"""

    class FakeService:
        """fake Run service。"""

        def stream_events(
            self,
            _run_id: str,
            _after_cursor: int | None,
        ) -> AsyncIterator[RunEvent]:
            async def gen():
                raise RunEventReplayExpiredError("run-1", 1)
                yield _event(1, RunEventType.RUN_QUEUED)

            return gen()

    module = _load_runs_module()
    response = await module.stream_run_events("run-1", 1, FakeService())

    text = await _sse_text(response)
    assert "event: replay_expired" in text
    assert '"fallback": "polling"' in text


@pytest.mark.asyncio
async def test_cancel_continue_error_mappings() -> None:
    """验证 cancel/continue 409 映射。"""

    service = AsyncMock()
    service.request_cancel.side_effect = RunCancelUnavailableError("run-1", "terminal")
    service.continue_run.side_effect = RunContinuationUnavailableError("run-1", "not paused")
    module = _load_runs_module()

    cancel_response = await module.cancel_run("run-1", service)
    continue_response = await module.continue_run(
        "run-1",
        module.RunContinueRequestBody(model="m1"),
        service,
    )

    assert cancel_response.status_code == 409
    assert continue_response.status_code == 409
    service.continue_run.assert_awaited_once_with("run-1", "m1")


def test_runs_router_does_not_import_run_infrastructure() -> None:
    """Run router 不得直接依赖 infrastructure.run。"""

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

    assert not any(
        module == "infrastructure.run" or module.startswith("infrastructure.run.")
        for module in imports
    )
