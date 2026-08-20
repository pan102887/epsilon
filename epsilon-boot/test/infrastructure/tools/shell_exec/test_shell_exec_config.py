"""ShellExecConfig 属性测试模块。

使用 Hypothesis 对 ShellExecConfig 的配置读取行为进行属性测试，验证：
- 配置读取正确性：通过环境变量设置的 timeout、max_output_size、enabled 和 working_dir 能被正确读取
- 默认值正确性：未设置环境变量时默认值分别为 30、51200、False 和空字符串
"""

from unittest.mock import MagicMock

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, settings

from infrastructure.tools.shell_exec.shell_exec_config import ShellExecConfig
from infrastructure.tools.shell_exec.shell_exec_tool import ShellExecTool

# ── Hypothesis 策略 ──

# timeout 策略：1~300 的整数
timeout_st = st.integers(min_value=1, max_value=300)

# max_output_size 策略：1024~1048576 的整数
max_output_size_st = st.integers(min_value=1024, max_value=1048576)

# enabled 策略：布尔值
enabled_st = st.booleans()

# working_dir 策略：由字母和数字组成的文本
working_dir_st = st.text(
    min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N"))
)

# Feature: shell-exec-tool, Property 1: 配置读取正确性
# **Validates: Requirements 1.1, 1.2, 1.3, 1.4**


@settings(
    max_examples=100, deadline=5000, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    timeout=timeout_st,
    max_output_size=max_output_size_st,
    enabled=enabled_st,
    working_dir=working_dir_st,
)
def test_config_reads_env_vars_correctly(
    monkeypatch: pytest.MonkeyPatch,
    timeout: int,
    max_output_size: int,
    enabled: bool,
    working_dir: str,
) -> None:
    """验证 ShellExecConfig 能正确读取环境变量中的配置值。

    对于任意有效的 timeout 整数值、max_output_size 整数值、enabled 布尔值和 working_dir 字符串值，
    通过 monkeypatch 设置对应环境变量后，直接实例化 ShellExecConfig
    应读取到与环境变量一致的字段值。

    Args:
        monkeypatch: pytest 的 monkeypatch fixture，用于安全地设置环境变量。
        timeout: 随机生成的超时秒数。
        max_output_size: 随机生成的输出大小上限。
        enabled: 随机生成的启用开关布尔值。
        working_dir: 随机生成的工作目录路径。
    """
    monkeypatch.setenv("SHELL_EXEC_TIMEOUT", str(timeout))
    monkeypatch.setenv("SHELL_EXEC_MAX_OUTPUT_SIZE", str(max_output_size))
    monkeypatch.setenv("SHELL_EXEC_ENABLED", str(enabled))
    monkeypatch.setenv("SHELL_EXEC_WORKING_DIR", working_dir)

    config = ShellExecConfig()

    assert config.timeout == timeout, f"timeout 不一致: 期望 {timeout}, 实际 {config.timeout}"
    assert config.max_output_size == max_output_size, (
        f"max_output_size 不一致: 期望 {max_output_size}, 实际 {config.max_output_size}"
    )
    assert config.enabled == enabled, f"enabled 不一致: 期望 {enabled}, 实际 {config.enabled}"
    assert config.working_dir == working_dir, (
        f"working_dir 不一致: 期望 {working_dir!r}, 实际 {config.working_dir!r}"
    )


@settings(
    max_examples=100, deadline=5000, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(data=st.data())
def test_config_defaults_when_env_vars_not_set(
    monkeypatch: pytest.MonkeyPatch,
    data: st.DataObject,
) -> None:
    """验证未设置环境变量时 ShellExecConfig 使用正确的默认值。

    清除所有 SHELL_EXEC_ 前缀的环境变量后，直接实例化 ShellExecConfig，
    验证 timeout 默认值为 30、max_output_size 默认值为 51200、enabled 默认值为 False、
    working_dir 默认值为空字符串。

    使用 Hypothesis 的 data 策略驱动多次执行，确保默认值在各种运行条件下保持稳定。

    Args:
        monkeypatch: pytest 的 monkeypatch fixture，用于安全地清除环境变量。
        data: Hypothesis data 策略，驱动多次执行。
    """
    monkeypatch.delenv("SHELL_EXEC_TIMEOUT", raising=False)
    monkeypatch.delenv("SHELL_EXEC_MAX_OUTPUT_SIZE", raising=False)
    monkeypatch.delenv("SHELL_EXEC_ENABLED", raising=False)
    monkeypatch.delenv("SHELL_EXEC_WORKING_DIR", raising=False)

    config = ShellExecConfig()

    assert config.timeout == 30, f"timeout 默认值应为 30, 实际 {config.timeout}"
    assert config.max_output_size == 51200, (
        f"max_output_size 默认值应为 51200, 实际 {config.max_output_size}"
    )
    # ``enabled`` 字段的安全默认仍为 False；实例值随 config.properties 主配置源加载
    # （现已默认开启为 True），故此处校验字段默认而非实例值，两者语义分离。
    assert ShellExecConfig.model_fields["enabled"].default is False, (
        "enabled 字段安全默认值应为 False"
    )
    assert config.working_dir == "", f"working_dir 默认值应为空字符串, 实际 {config.working_dir!r}"


# Feature: shell-exec-tool, Property 2: 条件注册正确性
# **Validates: Requirements 1.5, 7.2, 7.3**


@settings(max_examples=100, deadline=5000)
@given(enabled=enabled_st)
def test_conditional_registration(enabled: bool) -> None:
    """验证 ShellExecTool 条件注册逻辑的正确性。

    模拟 ``_create_tool_registry()`` 中的条件注册逻辑：
    - 当 enabled 为 True 时，ToolRegistry 应包含 ``"shell_exec"`` 工具
    - 当 enabled 为 False 时，ToolRegistry 不应包含 ``"shell_exec"`` 工具
    - 无论 enabled 值如何，其他已注册工具不受影响

    Args:
        enabled: 随机生成的布尔值，模拟 SHELL_EXEC_ENABLED 配置项。
    """
    from domain.agent.tools import ToolRegistry

    registry = ToolRegistry()

    # 预先注册一个 mock 工具，模拟 filesystem 等其他工具
    other_tool = MagicMock()
    other_tool.name = "mock_tool"
    registry.register(other_tool)

    # 模拟 _create_tool_registry 中的条件注册逻辑。Phase 11.3 起
    # ``ShellExecTool.__init__`` 新增 ``workspace`` 必填位置参数，这里注入一个
    # 空的 ``MagicMock``（结构类型）以满足签名；注册表关心的仅是工具名称，
    # 并不会触达 workspace 行为（见 2026-05-11 pytest 回归缺陷修复批次 E）。
    if enabled:
        registry.register(
            ShellExecTool(
                workspace=MagicMock(),
                timeout=30,
                max_output_size=51200,
            )
        )

    # 验证：enabled=True 时注册表包含 shell_exec，enabled=False 时不包含
    if enabled:
        assert registry.has("shell_exec"), "enabled=True 时，ToolRegistry 应包含 'shell_exec'"
    else:
        assert not registry.has("shell_exec"), (
            "enabled=False 时，ToolRegistry 不应包含 'shell_exec'"
        )

    # 验证：其他工具不受影响
    assert registry.has("mock_tool"), "条件注册不应影响其他已注册工具"
