"""PythonExecConfig 单元测试与属性测试模块。

验证 PythonExecConfig 的配置读取行为：
- 默认值正确性：未设置环境变量时各字段使用预期默认值
- env_prefix 正确性：通过 PYTHON_EXEC_ 前缀的环境变量能正确覆盖配置
- get_allowed_modules() 合并逻辑：空值返回默认白名单，有值时返回并集
- Property 7: 配置模块合并属性测试
"""

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, settings

from infrastructure.tools.python_exec.python_exec_config import (
    DEFAULT_ALLOWED_MODULES,
    PythonExecConfig,
)

# ── 单元测试 ──


class TestPythonExecConfigDefaults:
    """验证 PythonExecConfig 未设置环境变量时的默认值。"""

    def test_default_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """enabled 字段的安全默认值应为 False。

        实例值随 config.properties 主配置源加载（现已默认开启为 True），
        故此处校验字段默认而非实例值，两者语义分离。
        """
        monkeypatch.delenv("PYTHON_EXEC_ENABLED", raising=False)
        assert PythonExecConfig.model_fields["enabled"].default is False

    def test_default_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """timeout 默认值应为 30。"""
        monkeypatch.delenv("PYTHON_EXEC_TIMEOUT", raising=False)
        config = PythonExecConfig()
        assert config.timeout == 30

    def test_default_max_output_size(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """max_output_size 默认值应为 51200。"""
        monkeypatch.delenv("PYTHON_EXEC_MAX_OUTPUT_SIZE", raising=False)
        config = PythonExecConfig()
        assert config.max_output_size == 51200

    def test_default_max_memory_mb(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """max_memory_mb 默认值应为 256。"""
        monkeypatch.delenv("PYTHON_EXEC_MAX_MEMORY_MB", raising=False)
        config = PythonExecConfig()
        assert config.max_memory_mb == 256

    def test_default_working_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """working_dir 默认值应为空字符串。"""
        monkeypatch.delenv("PYTHON_EXEC_WORKING_DIR", raising=False)
        config = PythonExecConfig()
        assert config.working_dir == ""

    def test_default_allowed_modules(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """allowed_modules 默认值应为空字符串。"""
        monkeypatch.delenv("PYTHON_EXEC_ALLOWED_MODULES", raising=False)
        config = PythonExecConfig()
        assert config.allowed_modules == ""


class TestPythonExecConfigEnvPrefix:
    """验证 PythonExecConfig 通过 PYTHON_EXEC_ 前缀环境变量正确读取配置。"""

    def test_env_prefix_reads_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """通过 PYTHON_EXEC_ENABLED 环境变量设置 enabled 字段。"""
        monkeypatch.setenv("PYTHON_EXEC_ENABLED", "true")
        config = PythonExecConfig()
        assert config.enabled is True

    def test_env_prefix_reads_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """通过 PYTHON_EXEC_TIMEOUT 环境变量设置 timeout 字段。"""
        monkeypatch.setenv("PYTHON_EXEC_TIMEOUT", "60")
        config = PythonExecConfig()
        assert config.timeout == 60

    def test_env_prefix_reads_max_output_size(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """通过 PYTHON_EXEC_MAX_OUTPUT_SIZE 环境变量设置 max_output_size 字段。"""
        monkeypatch.setenv("PYTHON_EXEC_MAX_OUTPUT_SIZE", "102400")
        config = PythonExecConfig()
        assert config.max_output_size == 102400

    def test_env_prefix_reads_max_memory_mb(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """通过 PYTHON_EXEC_MAX_MEMORY_MB 环境变量设置 max_memory_mb 字段。"""
        monkeypatch.setenv("PYTHON_EXEC_MAX_MEMORY_MB", "512")
        config = PythonExecConfig()
        assert config.max_memory_mb == 512

    def test_env_prefix_reads_working_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """通过 PYTHON_EXEC_WORKING_DIR 环境变量设置 working_dir 字段。"""
        monkeypatch.setenv("PYTHON_EXEC_WORKING_DIR", "/tmp/sandbox")
        config = PythonExecConfig()
        assert config.working_dir == "/tmp/sandbox"

    def test_env_prefix_reads_allowed_modules(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """通过 PYTHON_EXEC_ALLOWED_MODULES 环境变量设置 allowed_modules 字段。"""
        monkeypatch.setenv("PYTHON_EXEC_ALLOWED_MODULES", "numpy,pandas")
        config = PythonExecConfig()
        assert config.allowed_modules == "numpy,pandas"


class TestEnvPrefix:
    """验证 PythonExecConfig 的 env_prefix 配置。"""

    def test_env_prefix_is_python_exec(self) -> None:
        """model_config 中 env_prefix 应为 'PYTHON_EXEC_'。"""
        assert PythonExecConfig.model_config.get("env_prefix") == "PYTHON_EXEC_"


class TestDefaultAllowedModulesConstant:
    """验证 DEFAULT_ALLOWED_MODULES 常量包含所有 17 个必需模块。"""

    def test_contains_all_required_modules(self) -> None:
        """DEFAULT_ALLOWED_MODULES 应包含需求文档中指定的全部 17 个标准库模块。"""
        required = {
            "math",
            "json",
            "re",
            "datetime",
            "collections",
            "itertools",
            "functools",
            "string",
            "textwrap",
            "decimal",
            "fractions",
            "statistics",
            "random",
            "hashlib",
            "base64",
            "csv",
            "io",
        }
        assert required == DEFAULT_ALLOWED_MODULES

    def test_count_is_17(self) -> None:
        """DEFAULT_ALLOWED_MODULES 应恰好包含 17 个模块。"""
        assert len(DEFAULT_ALLOWED_MODULES) == 17


class TestGetAllowedModules:
    """验证 get_allowed_modules() 方法的合并逻辑。"""

    def test_empty_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """allowed_modules 为空时，返回 DEFAULT_ALLOWED_MODULES。"""
        monkeypatch.delenv("PYTHON_EXEC_ALLOWED_MODULES", raising=False)
        config = PythonExecConfig()
        result = config.get_allowed_modules()
        assert result == DEFAULT_ALLOWED_MODULES

    def test_whitespace_only_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """allowed_modules 仅含空白时，返回 DEFAULT_ALLOWED_MODULES。"""
        monkeypatch.setenv("PYTHON_EXEC_ALLOWED_MODULES", "  ")
        config = PythonExecConfig()
        result = config.get_allowed_modules()
        assert result == DEFAULT_ALLOWED_MODULES

    def test_single_module_merged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """单个额外模块应与默认白名单合并。"""
        monkeypatch.setenv("PYTHON_EXEC_ALLOWED_MODULES", "numpy")
        config = PythonExecConfig()
        result = config.get_allowed_modules()
        assert "numpy" in result
        assert DEFAULT_ALLOWED_MODULES.issubset(result)

    def test_multiple_modules_merged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """多个额外模块应与默认白名单合并。"""
        monkeypatch.setenv("PYTHON_EXEC_ALLOWED_MODULES", "numpy,pandas,scipy")
        config = PythonExecConfig()
        result = config.get_allowed_modules()
        assert {"numpy", "pandas", "scipy"}.issubset(result)
        assert DEFAULT_ALLOWED_MODULES.issubset(result)

    def test_duplicate_module_no_effect(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """重复的模块名（已在默认白名单中）不影响结果。"""
        monkeypatch.setenv("PYTHON_EXEC_ALLOWED_MODULES", "math,json")
        config = PythonExecConfig()
        result = config.get_allowed_modules()
        assert result == DEFAULT_ALLOWED_MODULES

    def test_whitespace_and_empty_entries_handled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """逗号分隔列表中的空白和空条目应被正确过滤。"""
        monkeypatch.setenv("PYTHON_EXEC_ALLOWED_MODULES", " numpy , , pandas , ")
        config = PythonExecConfig()
        result = config.get_allowed_modules()
        assert {"numpy", "pandas"}.issubset(result)
        assert DEFAULT_ALLOWED_MODULES.issubset(result)
        assert "" not in result

    def test_returns_frozenset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """返回值类型应为 frozenset。"""
        monkeypatch.setenv("PYTHON_EXEC_ALLOWED_MODULES", "numpy")
        config = PythonExecConfig()
        result = config.get_allowed_modules()
        assert isinstance(result, frozenset)


# ── 属性测试 ──

# Feature: python-script-sandbox, Property 7: 配置模块合并
# **Validates: Requirements 3.2**

# Python 标识符策略：生成合法的 Python 标识符作为模块名
_python_identifier_st = st.from_regex(r"[a-z][a-z0-9_]{0,19}", fullmatch=True)

# 模块名列表策略：0~10 个合法 Python 标识符
_module_list_st = st.lists(_python_identifier_st, min_size=0, max_size=10)


@settings(
    max_examples=100,
    deadline=5000,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(modules=_module_list_st)
def test_config_module_merging(
    monkeypatch: pytest.MonkeyPatch,
    modules: list[str],
) -> None:
    """属性测试：验证 get_allowed_modules() 返回 DEFAULT_ALLOWED_MODULES 与输入列表的并集。

    对于任意合法 Python 标识符列表，将其以逗号拼接后设置为 PYTHON_EXEC_ALLOWED_MODULES，
    get_allowed_modules() 的返回值应等于 DEFAULT_ALLOWED_MODULES 与该列表的并集。

    Args:
        monkeypatch: pytest 的 monkeypatch fixture，用于安全地设置环境变量。
        modules: Hypothesis 生成的随机合法 Python 标识符列表。
    """
    csv_value = ",".join(modules)
    monkeypatch.setenv("PYTHON_EXEC_ALLOWED_MODULES", csv_value)

    config = PythonExecConfig()
    result = config.get_allowed_modules()

    # 期望值：默认白名单与输入模块列表（去除空白后）的并集
    expected = DEFAULT_ALLOWED_MODULES | frozenset(m.strip() for m in modules if m.strip())

    assert result == expected, (
        f"合并结果不一致:\n"
        f"  输入模块: {modules}\n"
        f"  期望: {sorted(expected)}\n"
        f"  实际: {sorted(result)}"
    )
    # 验证返回类型为 frozenset
    assert isinstance(result, frozenset)
    # 验证默认白名单始终是结果的子集
    assert DEFAULT_ALLOWED_MODULES.issubset(result)
