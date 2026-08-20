"""评测运行期零外部调用守卫。

覆盖点：
- ``EvalRunner.run()`` 在 metrics 目录为空 / 不存在样本的情况下，
  不得触发任何 ``httpx.AsyncClient.request`` / ``openai`` 真实调用；
- 若未来某个样本意外依赖真实 LLM Provider，本测试将以拦截器中断
  并 fail，防止评测静默走真实通道。

对应需求 5.3、需求 12.3；对应设计 "测试策略 — LLM 调用隔离"。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.evaluation.runner.runner import EvalRunner, RunnerConfig


class _ExternalCallDetected(RuntimeError):
    """测试辅助：任何外部调用被拦截时抛出。"""


def _install_httpx_guard(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """拦截 ``httpx`` 的常见调用入口，记录到 ``calls`` 列表。

    返回的列表供断言使用；若拦截到调用即抛 :class:`_ExternalCallDetected`。
    """

    calls: list[str] = []

    try:
        import httpx  # noqa: WPS433
    except ModuleNotFoundError:  # pragma: no cover — 仓库环境必然存在
        return calls

    def _sync_request(self, *args: object, **kwargs: object) -> object:
        calls.append("httpx.Client.request")
        raise _ExternalCallDetected("httpx.Client.request 被评测流程触发")

    async def _async_request(self, *args: object, **kwargs: object) -> object:
        calls.append("httpx.AsyncClient.request")
        raise _ExternalCallDetected("httpx.AsyncClient.request 被评测流程触发")

    monkeypatch.setattr(httpx.Client, "request", _sync_request, raising=True)
    monkeypatch.setattr(httpx.AsyncClient, "request", _async_request, raising=True)

    return calls


def _install_openai_guard(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """拦截 ``openai`` SDK 核心调用入口（如安装）。

    openai 的同步/异步客户端在不同版本下入口不同，这里只拦截最常见的
    ``openai.OpenAI`` 与 ``openai.AsyncOpenAI`` 构造；评测流程若意外
    实例化客户端，本测试立即 fail。
    """

    calls: list[str] = []

    try:
        import openai  # noqa: WPS433
    except ModuleNotFoundError:  # pragma: no cover — 仓库环境必然存在
        return calls

    def _forbid_openai(*args: object, **kwargs: object) -> object:
        calls.append("openai.OpenAI")
        raise _ExternalCallDetected("openai.OpenAI 被评测流程触发")

    def _forbid_async_openai(*args: object, **kwargs: object) -> object:
        calls.append("openai.AsyncOpenAI")
        raise _ExternalCallDetected("openai.AsyncOpenAI 被评测流程触发")

    if hasattr(openai, "OpenAI"):
        monkeypatch.setattr(openai, "OpenAI", _forbid_openai, raising=True)
    if hasattr(openai, "AsyncOpenAI"):
        monkeypatch.setattr(openai, "AsyncOpenAI", _forbid_async_openai, raising=True)

    return calls


def test_runner_makes_no_external_calls_on_empty_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """空样本 Runner.run() 不触发任何外部网络 / SDK 调用。"""

    httpx_calls = _install_httpx_guard(monkeypatch)
    openai_calls = _install_openai_guard(monkeypatch)

    # 指向一个空目录来保证零样本收集；使用临时目录即可。
    empty_metrics_dir = tmp_path / "empty_metrics"
    empty_metrics_dir.mkdir()
    (empty_metrics_dir / "__init__.py").write_text("", encoding="utf-8")

    runner = EvalRunner(
        RunnerConfig(
            output_dir=tmp_path / "out",
            metrics_test_path=empty_metrics_dir,
        )
    )

    result = runner.run()

    # 全部指标均为零值占位；exit_code=0。
    assert result.exit_code == 0
    assert len(result.metrics) >= 1
    assert all(m.sample_count == 0 for m in result.metrics)

    # 任一拦截点被触发即 fail。
    assert httpx_calls == [], f"httpx 外部调用被触发: {httpx_calls}"
    assert openai_calls == [], f"openai 外部调用被触发: {openai_calls}"
