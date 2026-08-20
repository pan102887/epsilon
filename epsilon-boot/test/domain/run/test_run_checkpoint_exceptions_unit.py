"""Run checkpoint 异常单元测试模块。"""

from __future__ import annotations

from common.exceptions import BizException
from domain.run.exceptions import (
    RunCheckpointPayloadTooLargeError,
    RunCheckpointSchemaError,
    RunCheckpointStoreUnavailableError,
    RunCheckpointWriteError,
    RunRecoveryUnavailableError,
    RunToolReplayBlockedError,
)


def _checkpoint_exceptions() -> list[BizException]:
    return [
        RunCheckpointWriteError("run-1", "cp-1", "io_error"),
        RunCheckpointSchemaError("run-1", "cp-1", 2, "不兼容"),
        RunRecoveryUnavailableError("run-1", "unsafe pending tool"),
        RunToolReplayBlockedError("run-1", "web_search", "key-abcd1234", "requires manual review"),
        RunCheckpointPayloadTooLargeError("run-1", "cp-1", 300_000, 262_144),
        RunCheckpointStoreUnavailableError("save_checkpoint", "redis timeout"),
    ]


def test_checkpoint_exceptions_inherit_biz_exception() -> None:
    for exc in _checkpoint_exceptions():
        assert isinstance(exc, BizException)


def test_checkpoint_exception_codes_are_stable_and_unique() -> None:
    codes = [exc.code for exc in _checkpoint_exceptions()]

    assert codes == list(range(61011, 61017))
    assert len(set(codes)) == len(codes)


def test_checkpoint_exception_messages_are_locatable() -> None:
    messages = [exc.message for exc in _checkpoint_exceptions()]

    assert any("检查点写入失败" in message and "run-1" in message for message in messages)
    assert any(
        "检查点 schema 不兼容" in message and "schema_version=2" in message for message in messages
    )
    assert any("运行 run-1 不可自动恢复" in message for message in messages)
    assert any(
        "工具结果不可自动重放" in message and "web_search" in message for message in messages
    )
    assert any("检查点载荷过大" in message and "300000" in message for message in messages)
    assert any(
        "检查点存储不可用" in message and "save_checkpoint" in message for message in messages
    )


def test_checkpoint_exception_messages_do_not_leak_sensitive_payload() -> None:
    secret_payload = (
        '{"messages":[{"role":"user","content":"secret prompt with token sk-test-123"}],'
        '"tool_args":{"password":"p@ssw0rd","api_key":"abc"}}'
    )

    exceptions = [
        RunCheckpointWriteError("run-1", "cp-1", secret_payload),
        RunCheckpointSchemaError("run-1", "cp-1", 9, secret_payload),
        RunRecoveryUnavailableError("run-1", secret_payload),
        RunToolReplayBlockedError("run-1", "write_file", "key-secret", secret_payload),
        RunCheckpointPayloadTooLargeError("run-1", "cp-1", 999_999, 1_000),
        RunCheckpointStoreUnavailableError("save_checkpoint", secret_payload),
    ]

    for exc in exceptions:
        assert "secret prompt" not in exc.message
        assert "sk-test-123" not in exc.message
        assert "password" not in exc.message
        assert "api_key" not in exc.message
        assert secret_payload not in exc.message
