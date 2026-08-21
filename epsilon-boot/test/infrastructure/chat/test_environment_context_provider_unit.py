"""环境上下文提供器单元测试。"""

from datetime import datetime

import pytest

from infrastructure.chat.environment_context_provider import (
    StaticEnvironmentContextProvider,
    UnsafeEnvironmentContextError,
    assert_no_host_absolute_path,
)


def _fixed_clock() -> datetime:
    """返回固定时间，确保环境上下文日期稳定。"""
    return datetime(2026, 6, 2, 12, 0, 0)


def test_static_provider_builds_safe_codex_style_environment_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """静态 provider 输出固定日期、安全工作区提示和路径披露边界。"""
    env_secret_value = "env-secret-value-for-context-test"
    secret_examples = ("sk-test-secret", "ghp_test_secret", "access_token=test")
    monkeypatch.setenv("CONTEXT_ENGINEERING_SECRET", env_secret_value)

    text = StaticEnvironmentContextProvider(clock=_fixed_clock).build()

    assert text.startswith("<environment_context>")
    assert text.endswith("</environment_context>")
    assert "current_date: 2026-06-02" in text
    assert "workspace: workspace:/" in text
    assert (
        "path_policy: Use workspace-relative POSIX paths. Do not expose host absolute paths."
    ) in text
    assert "/mnt/c" not in text
    assert "/home" not in text
    assert "C:\\" not in text
    assert env_secret_value not in text
    for secret_example in secret_examples:
        assert secret_example not in text


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "/mnt/c/source/x",
        "/home/user/x",
        r"C:\source\x",
    ],
)
def test_assert_no_host_absolute_path_rejects_unsafe_paths_without_leaking(
    unsafe_path: str,
) -> None:
    """路径安全断言应拒绝常见宿主绝对路径，异常消息不得回显原路径。"""
    with pytest.raises(UnsafeEnvironmentContextError) as exc_info:
        assert_no_host_absolute_path(f"workspace: {unsafe_path}")

    assert unsafe_path not in str(exc_info.value)


def test_static_provider_rejects_unsafe_workspace_label_without_leaking() -> None:
    """workspace_label 含宿主绝对路径时 build 应 fail-fast。"""
    unsafe_label = "/mnt/c/source/x"
    provider = StaticEnvironmentContextProvider(
        clock=_fixed_clock,
        workspace_label=unsafe_label,
    )

    with pytest.raises(UnsafeEnvironmentContextError) as exc_info:
        provider.build()

    assert unsafe_label not in str(exc_info.value)
