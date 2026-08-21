"""全局测试夹具：将单元测试与真实配置源彻底隔离。

本模块提供一个 ``autouse`` 夹具，在**每个**测试用例执行前把配置加载链路
中的所有「外部可漂移来源」屏蔽掉，使配置类默认只读取**代码内声明的字段默认值**：

被屏蔽的外部来源（对应 :mod:`common.configuration` 的加载优先级）：
1. 宿主/CI 环境变量 —— 清空所有配置类 ``env_prefix`` 覆盖到的前缀变量；
2. ``config.properties`` 主配置文件 —— 重定向到不存在的临时路径；
3. ``config.local.properties`` 本地覆盖文件 —— 重定向到不存在的临时路径；
4. ``.env`` 文件 —— 项目当前无此文件，屏蔽 env 与 properties 后其亦不生效。

隔离动机（软件工程「Hermetic Testing」原则）：
单元测试断言的「默认值」应是**代码契约**，而非「当前 config.properties 的快照」。
在隔离前，一旦有人调整 ``config.properties`` 内容、或 CI 环境注入了同名前缀的
环境变量，大量直接实例化配置类（如 ``ChatConfig()``）并断言具体值的测试就会误红。
隔离后，配置文件与运行环境如何变化都不影响单测结果。

对「需要验证从外部源加载」的测试完全兼容：
- 本夹具在测试函数体**之前**执行，用例体内的 ``monkeypatch.setenv(...)`` 会在
  隔离之后再注入，仍然生效；
- 用 ``tmp_path`` + 显式 ``properties_path`` 构造 ``PropertiesFileSettingsSource``
  的测试自行提供数据源，不受本夹具影响。

如个别测试确需读取仓库内真实配置文件（如集成校验），可在该用例上标注
``@pytest.mark.real_config`` 显式退出隔离。
"""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, TypeVar, cast

import pytest

_ConfigT = TypeVar("_ConfigT")


class _RequestWithNode(Protocol):
    node: pytest.Item


# 覆盖 src/ 下所有 PropertiesBaseSettings 子类使用的 env_prefix。
# 新增配置类若引入新前缀，需同步补充此元组，否则该前缀的宿主环境变量不会被清理，
# 隔离将出现静默漏洞。为防止手工维护漂移，
# ``test/infrastructure/configuration/test_config_prefix_registry.py`` 提供守卫测试：
# 自动发现全项目所有 PropertiesBaseSettings 子类的 env_prefix 并断言与本元组一致，
# 任何遗漏/多余都会立即使该测试失败。
_CONFIG_ENV_PREFIXES: tuple[str, ...] = (
    "AGENT_",
    "AGENT_GUARDRAILS_",
    "ARTIFACT_",
    "CHAT_",
    "DB_",
    "GATEWAY_",
    "HITL_",
    "HTTP_REQUEST_",
    "ID_VALIDATION_",
    "LOCAL_PERSISTENCE_",
    "LOGGING_REQUEST_",
    "LOGGING_RESPONSE_",
    "MCP_",
    "MODEL_ROUTER_",
    "OTEL_",
    "PROMPT_",
    "PYTHON_EXEC_",
    "REDIS_",
    "RUN_",
    "RUN_WORKFLOW_",
    "SERVER_",
    "SESSION_REDIS_",
    "SESSION_STORE_",
    "SHELL_EXEC_",
    "TASK_AGENT_",
    "TAVILY_",
    "TOOL_CB_",
    "TRACE_",
    "WEB_FETCH_",
    "WORKSPACE_",
    "EPSILON_LOG_",
)
CONFIG_ENV_PREFIXES = _CONFIG_ENV_PREFIXES


@pytest.fixture(autouse=True)
def isolate_config_sources(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """将配置加载与真实文件/宿主环境隔离，使配置类只读代码默认值。

    标注了 ``@pytest.mark.real_config`` 的用例会跳过隔离，按真实配置源加载。

    Args:
        request: pytest 请求对象，用于检测 ``real_config`` 标记。
        monkeypatch: 用于清理环境变量并重定向配置文件路径，测试结束自动回滚。
        tmp_path_factory: 生成一个（不创建文件的）临时路径，作为不存在的配置源。
    """
    node = cast(_RequestWithNode, request).node
    if node.get_closest_marker("real_config") is not None:
        # 显式选择使用真实配置源的用例，不做隔离。
        return

    # 1) 清空所有配置前缀的宿主/CI 环境变量，阻断环境泄漏。
    import os

    for key in list(os.environ):
        if key.startswith(_CONFIG_ENV_PREFIXES):
            monkeypatch.delenv(key, raising=False)

    # 2) 将 properties 主配置与本地覆盖文件重定向到不存在的路径，
    #    使 PropertiesFileSettingsSource 返回空 dict，配置回落到代码默认值。
    #    _parse_properties_file 对不存在的文件返回 {}，不会报错。
    missing_dir = tmp_path_factory.mktemp("isolated_config")
    monkeypatch.setattr(
        "common.configuration.configuration_utils._PROPERTIES_FILE",
        missing_dir / "config.properties",
        raising=True,
    )
    monkeypatch.setattr(
        "common.configuration.configuration_utils._LOCAL_PROPERTIES_FILE",
        missing_dir / "config.local.properties",
        raising=True,
    )


@pytest.fixture
def config_factory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Callable[..., object]:
    """返回一个「干净配置源」工厂：仅从显式给定的 properties 内容加载配置类。

    解决方案 C：让「测试从 config.properties 加载某值」的用例无需每处手写
    ``tmp_path`` 写文件 + ``monkeypatch.setattr`` 重定向，改为声明式地给出配置内容。
    工厂会把 ``config.properties`` 主配置源重定向到一个临时文件（写入调用方给定的
    ``properties`` 文本），并把本地覆盖源指向不存在的路径；未在文本中出现的字段
    自然回落到代码默认值。

    与全局 ``isolate_config_sources`` 夹具协同：本工厂在用例体内二次调用
    ``monkeypatch.setattr``，覆盖隔离夹具设置的「空路径」，指向本次的临时文件。
    环境变量仍处于隔离夹具清理后的干净状态，故加载结果只由传入文本与代码默认值决定。

    用法::

        def test_loads_custom_trigger(config_factory):
            cfg = config_factory(ChatConfig, "chat.compaction_trigger_tokens=4096")
            assert cfg.compaction_trigger_tokens == 4096

    Args:
        monkeypatch: 用于重定向配置文件路径常量，测试结束自动回滚。
        tmp_path: 每个用例独立的临时目录，存放本次的 properties 文件。

    Returns:
        工厂函数 ``make(config_cls, properties="")``：以给定 properties 文本
        （Java Properties 格式，空串表示纯代码默认值）实例化 ``config_cls`` 并返回。
    """

    def _make(config_cls: type[_ConfigT], properties: str = "") -> _ConfigT:
        """以给定 properties 文本实例化配置类。

        Args:
            config_cls: 待实例化的 ``PropertiesBaseSettings`` 子类。
            properties: Java Properties 格式配置文本，默认空串（纯代码默认值）。

        Returns:
            仅从给定文本与代码默认值加载的配置实例。
        """
        props_file = tmp_path / "config.properties"
        props_file.write_text(properties, encoding="utf-8")
        monkeypatch.setattr(
            "common.configuration.configuration_utils._PROPERTIES_FILE",
            props_file,
            raising=True,
        )
        monkeypatch.setattr(
            "common.configuration.configuration_utils._LOCAL_PROPERTIES_FILE",
            tmp_path / "nonexistent.local.properties",
            raising=True,
        )
        return config_cls()

    return _make


def pytest_configure(config: pytest.Config) -> None:
    """注册自定义标记，避免 ``--strict-markers`` 下报未知标记警告。

    Args:
        config: pytest 配置对象。
    """
    config.addinivalue_line(
        "markers",
        "real_config: 该用例使用仓库内真实配置源（config.properties/.env），"
        "跳过 isolate_config_sources 隔离夹具。",
    )
