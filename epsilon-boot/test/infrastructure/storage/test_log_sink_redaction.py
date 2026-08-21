"""本地文件日志脱敏与装配单元测试。

覆盖 SensitiveRedactionFilter 对 api_key/authorization/token/cookie 等敏感取值
脱敏为 ****；configure_local_file_logging 默认经 resolve(USER) 落
~/.epsilon/<project-hash>/logs/epsilon.log（不落项目工作区）；to_file=False 时返回
None 且不挂 handler（Property 9）。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from infrastructure.storage.local_file_log_sink import (
    SensitiveRedactionFilter,
    configure_local_file_logging,
)
from infrastructure.storage.local_file_tier_resolver import LocalFileTierResolver
from infrastructure.storage.log_sink_config import LogSinkConfig

_SENSITIVE_KEYS = frozenset(
    {"api_key", "authorization", "token", "cookie", "secret"}
)


@pytest.fixture
def cleanup_root_handlers() -> Iterator[None]:
    """记录装配前 root logger 的 handler，测试结束后移除新增项，避免污染其他测试。"""
    root = logging.getLogger()
    before = list(root.handlers)
    yield
    for handler in list(root.handlers):
        if handler not in before:
            root.removeHandler(handler)
            handler.close()


def _make_record(msg: str, *args: object) -> logging.LogRecord:
    """构造一条 LogRecord 供 Filter 就地脱敏测试使用。"""
    return logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


def test_redacts_api_key_in_message() -> None:
    """api_key=xxx 形式的取值被脱敏为 ****。"""
    filt = SensitiveRedactionFilter(_SENSITIVE_KEYS)
    record = _make_record("calling with api_key=sk-secret-value-123 done")
    assert filt.filter(record) is True
    assert "sk-secret-value-123" not in record.getMessage()
    assert "****" in record.getMessage()


def test_redacts_json_authorization() -> None:
    """JSON 形式 "authorization":"Bearer x" 的取值被脱敏。"""
    filt = SensitiveRedactionFilter(_SENSITIVE_KEYS)
    record = _make_record('headers {"authorization":"Bearer topsecret"}')
    assert filt.filter(record) is True
    assert "topsecret" not in record.getMessage()
    assert "****" in record.getMessage()


def test_redacts_token_and_cookie() -> None:
    """token=... 与 cookie=... 均被脱敏。"""
    filt = SensitiveRedactionFilter(_SENSITIVE_KEYS)
    record = _make_record("token=abc123def cookie=session=zzz999")
    assert filt.filter(record) is True
    message = record.getMessage()
    assert "abc123def" not in message
    assert "zzz999" not in message
    assert "****" in message


def test_redacts_sensitive_value_in_args() -> None:
    """敏感取值出现在格式化 args 中时同样被脱敏。"""
    filt = SensitiveRedactionFilter(_SENSITIVE_KEYS)
    record = _make_record("request %s", "api_key=leaked-key-999")
    assert filt.filter(record) is True
    assert "leaked-key-999" not in record.getMessage()
    assert "****" in record.getMessage()


def test_filter_always_returns_true_and_keeps_non_sensitive() -> None:
    """无敏感字段的消息不被改动，且 filter 恒返回 True。"""
    filt = SensitiveRedactionFilter(_SENSITIVE_KEYS)
    record = _make_record("plain message without secrets")
    assert filt.filter(record) is True
    assert record.getMessage() == "plain message without secrets"


def test_empty_sensitive_keys_is_noop() -> None:
    """空敏感词表时 Filter 不做任何替换且恒返回 True。"""
    filt = SensitiveRedactionFilter(frozenset())
    record = _make_record("api_key=still-here")
    assert filt.filter(record) is True
    assert "still-here" in record.getMessage()


def test_configure_logging_writes_to_user_tier(
    tmp_path: Path,
    cleanup_root_handlers: None,
) -> None:
    """默认经 resolve(USER) 落 <user_base>/.epsilon/<hash>/logs/epsilon.log（不落项目区）。"""
    user_base = tmp_path / "home"
    project_base = tmp_path / "workspace"
    resolver = LocalFileTierResolver(project_base=project_base, user_base=user_base)
    config = LogSinkConfig(to_file=True)

    handler = configure_local_file_logging(resolver, config, _SENSITIVE_KEYS)

    assert handler is not None
    expected = (
        user_base.resolve()
        / ".epsilon"
        / resolver.project_hash()
        / "logs"
        / "epsilon.log"
    )
    assert Path(handler.baseFilename) == expected  # type: ignore[attr-defined]
    # 日志不落项目工作区 .epsilon。
    assert not (project_base / ".epsilon" / "logs").exists()
    assert handler in logging.getLogger().handlers


def test_configure_logging_redacts_on_disk(
    tmp_path: Path,
    cleanup_root_handlers: None,
) -> None:
    """经装配的 handler 写盘内容中敏感取值被脱敏为 ****（Property 9）。"""
    resolver = LocalFileTierResolver(
        project_base=tmp_path / "workspace", user_base=tmp_path / "home"
    )
    handler = configure_local_file_logging(
        resolver, LogSinkConfig(to_file=True), _SENSITIVE_KEYS
    )
    assert handler is not None

    record = _make_record("auth api_key=super-secret-token")
    handler.handle(record)
    handler.flush()

    content = Path(handler.baseFilename).read_text(encoding="utf-8")  # type: ignore[attr-defined]
    assert "super-secret-token" not in content
    assert "****" in content


def test_configure_logging_disabled_returns_none(tmp_path: Path) -> None:
    """to_file=False 时返回 None 且不向 root logger 追加 handler。"""
    resolver = LocalFileTierResolver(
        project_base=tmp_path / "workspace", user_base=tmp_path / "home"
    )
    root = logging.getLogger()
    before = list(root.handlers)

    handler = configure_local_file_logging(
        resolver, LogSinkConfig(to_file=False), _SENSITIVE_KEYS
    )

    assert handler is None
    assert list(root.handlers) == before
