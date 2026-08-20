"""pytest 根 conftest：评测标记与 ``sample_sink`` fixture。

本文件由 pytest 在收集 ``tests/evaluation`` 目录下测试时自动加载，负责：

1. 注册 ``evaluation`` 与 ``evaluation_self`` 两个标记，避免运行时
   报出 "PytestUnknownMarkWarning"；
2. 提供 session 作用域的 :func:`sample_sink` fixture，由指标评测用例
   （阶段 3 的 ``tests/evaluation/metrics/test_*.py``）注入；
3. 在 ``pytest_configure`` 阶段读取 ``tests/evaluation/config/eval.toml``，
   将参数挂在 ``config._eval_params`` 上，供指标用例与元测试共享。

本 conftest 不依赖业务源码；仅使用 Python 标准库与 pytest 插件 API。
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

from tests.evaluation.runner.sample_sink import SampleSink, reset_sample_sink

_CONFTEST_DIR = Path(__file__).resolve().parent
_EVAL_TOML = _CONFTEST_DIR / "config" / "eval.toml"


def pytest_configure(config: pytest.Config) -> None:
    """pytest 启动阶段钩子。

    - 注册自定义标记 ``evaluation``（评测样本）与 ``evaluation_self``
      （评测代码自身的元测试），便于 ``-m`` 选择。
    - 解析 ``tests/evaluation/config/eval.toml``，将结果字典挂在
      ``config._eval_params`` 上；解析失败时静默设置为空字典，不阻止
      测试继续运行（自测与指标样本会直接使用默认值）。
    """

    config.addinivalue_line(
        "markers", "evaluation: 标记评测样本，由 scripts/evaluation/run_eval.py 收集"
    )
    config.addinivalue_line(
        "markers",
        "evaluation_self: 标记评测代码自身的元测试（验证指标实现正确性）",
    )

    params: dict[str, object] = {}
    if _EVAL_TOML.exists():
        try:
            with _EVAL_TOML.open("rb") as fh:
                params = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError):
            params = {}
    # 使用私有属性避免与 pytest 内部命名冲突；以 "_eval_" 前缀标记。
    config._eval_params = params  # type: ignore[attr-defined]


@pytest.fixture(scope="session")
def sample_sink() -> SampleSink:
    """会话级评测样本收集器 fixture。

    每次 pytest session 启动时自动清空进程级 :class:`SampleSink`，
    确保不同 session 之间样本不串扰。

    Returns:
        已被清空的 :class:`SampleSink` 实例。
    """

    return reset_sample_sink()


@pytest.fixture(scope="session")
def eval_params(pytestconfig: pytest.Config) -> dict[str, object]:
    """返回 ``tests/evaluation/config/eval.toml`` 解析结果。

    Args:
        pytestconfig: pytest 自带的 config fixture。

    Returns:
        解析后的参数字典；解析失败时为空字典。
    """

    return getattr(pytestconfig, "_eval_params", {})


# --- sys.path 兜底 --------------------------------------------------------
# 评测桩（``tests/evaluation/stubs/``）需要从 ``epsilon-boot/src`` 导入
# 领域值对象与异常类；开发环境通过 ``uv run`` 自动注入 ``pythonpath=["src"]``，
# 但在仓库根以原生 pytest 执行时需要显式兜底。仅在 ``src`` 目录存在时追加，
# 避免污染其它测试的导入顺序。
_BACKEND_SRC = _CONFTEST_DIR.parent.parent / "epsilon-boot" / "src"
if _BACKEND_SRC.exists() and str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))
