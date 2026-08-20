"""Run 领域异常单元测试模块。"""

from __future__ import annotations

from common.exceptions import BizException
from domain.run.exceptions import (
    RunCancelUnavailableError,
    RunContinuationUnavailableError,
    RunEventReplayExpiredError,
    RunIdempotencyConflictError,
    RunInvalidTransitionError,
    RunLeaseConflictError,
    RunNotFoundError,
    RunPayloadValidationError,
    RunQueueFullError,
    RunStoreUnavailableError,
)


def _all_exceptions() -> list[BizException]:
    """构造所有 Run 领域异常实例。"""
    return [
        RunNotFoundError("run-1"),
        RunQueueFullError("max_queued_runs", 10),
        RunInvalidTransitionError("queued", "succeeded"),
        RunContinuationUnavailableError("run-1", "状态不是 paused"),
        RunCancelUnavailableError("run-1", "状态已结束"),
        RunLeaseConflictError("run-1", "worker-a"),
        RunEventReplayExpiredError("run-1", 2),
        RunPayloadValidationError("缺少 chat 或 task"),
        RunStoreUnavailableError("create_run", "io_error"),
        RunIdempotencyConflictError("client-1"),
    ]


def test_all_run_exceptions_inherit_biz_exception() -> None:
    """所有 Run 异常都必须继承 BizException。"""
    for exc in _all_exceptions():
        assert isinstance(exc, BizException)


def test_run_exception_codes_are_unique_and_in_reserved_range() -> None:
    """错误码必须唯一并落在 61001 至 61010。"""
    codes = [exc.code for exc in _all_exceptions()]

    assert codes == list(range(61001, 61011))
    assert len(set(codes)) == len(codes)


def test_idempotency_conflict_uses_dedicated_code_61010() -> None:
    """幂等 payload 冲突不能复用状态迁移错误码。"""
    exc = RunIdempotencyConflictError("client-1")

    assert exc.code == 61010
    assert not isinstance(exc, RunInvalidTransitionError)


def test_messages_are_chinese_and_locatable() -> None:
    """异常消息应是中文并包含可定位信息。"""
    messages = [exc.message for exc in _all_exceptions()]

    assert any("不存在" in message for message in messages)
    assert any("容量已满" in message for message in messages)
    assert any("不可从 queued 迁移到 succeeded" in message for message in messages)
    assert any("幂等请求冲突" in message for message in messages)


def test_messages_do_not_include_full_payload_content() -> None:
    """异常消息不得拼接完整 payload 或敏感提示词内容。"""
    exc = RunPayloadValidationError("payload_hash=abc123")

    for forbidden in (
        '"message"',
        '"goal"',
        "secret prompt",
        "{'chat'",
        '{"chat"',
    ):
        assert forbidden not in exc.message
