"""环境变量清理属性测试模块。

使用 Hypothesis 对 sanitize_env 函数进行属性测试，验证：
- 敏感且非保留的环境变量被移除
- 平台保留列表中的变量始终保留（即使名称包含敏感关键词）
- 非敏感且非保留的环境变量也被保留

注意：由于 Windows 上 os.environ 是大小写不敏感的映射（os._Environ），
直接使用 patch.dict("os.environ", ...) 会导致键名大小写被归一化，
因此本测试通过 patch.object 将 shell_exec_tool 模块中的 os.environ
替换为普通 dict，以精确控制环境变量的键名大小写。
"""

from unittest.mock import patch

import hypothesis.strategies as st
from hypothesis import given, settings

from infrastructure.tools.shell_exec import shell_exec_tool
from infrastructure.tools.shell_exec.shell_exec_tool import (
    SENSITIVE_KEYWORDS,
    UNIX_PRESERVED_VARS,
    WIN_PRESERVED_VARS,
    sanitize_env,
)

# ── Hypothesis 策略 ──

# 环境变量名值对策略：由字母和数字组成的键值对，
# 排除 null 字符（Windows 上 os.environ 不支持 null 字符）
_env_name_st = st.text(
    min_size=1,
    max_size=30,
    alphabet=st.characters(whitelist_categories=("L", "N")),
)
_env_value_st = st.text(max_size=50)

env_dict_st = st.dictionaries(_env_name_st, _env_value_st)

# platform 策略：三种平台标识
platform_st = st.sampled_from(["linux", "darwin", "win32"])


def _is_sensitive(name: str) -> bool:
    """判断环境变量名是否包含敏感关键词（不区分大小写）。

    Args:
        name: 环境变量名称。

    Returns:
        如果名称中包含任一敏感关键词则返回 True。
    """
    name_upper = name.upper()
    return any(kw in name_upper for kw in SENSITIVE_KEYWORDS)


def _get_preserved(platform: str) -> set[str]:
    """根据平台获取保留变量集合。

    Args:
        platform: 平台标识（linux/darwin/win32）。

    Returns:
        对应平台的保留环境变量名称集合。
    """
    return WIN_PRESERVED_VARS if platform == "win32" else UNIX_PRESERVED_VARS


# Feature: shell-exec-tool, Property 4: 环境变量清理正确性
# **Validates: Requirements 4.1, 4.2, 4.3**


@settings(max_examples=100, deadline=5000)
@given(base_env=env_dict_st, platform=platform_st)
def test_sanitize_env_correctness(
    base_env: dict[str, str],
    platform: str,
) -> None:
    """验证 sanitize_env 对任意环境变量集合的清理正确性。

    生成随机环境变量名值对，并注入已知的敏感变量和保留变量，
    通过 patch.object 替换 shell_exec_tool 模块中的 os.environ 为普通 dict，
    同时 Mock sys.platform，调用 sanitize_env 后验证三条属性：
    1. 敏感且非保留的变量不在结果中
    2. 保留列表中的变量始终在结果中（即使名称包含敏感关键词）
    3. 非敏感且非保留的变量也在结果中

    Args:
        base_env: 随机生成的环境变量名值对字典。
        platform: 随机选取的平台标识。
    """
    preserved = _get_preserved(platform)

    # 构建测试环境变量字典（普通 dict，避免 Windows os.environ 大小写不敏感问题）
    env = dict(base_env)

    # 注入已知的敏感变量（非保留）
    sensitive_var = "MY_API_KEY"
    env[sensitive_var] = "secret_value"

    # 注入已知的保留变量（根据平台选择正确的变量名）
    preserved_var = "PATH" if platform != "win32" else "Path"
    env[preserved_var] = "/usr/bin"

    # 使用 patch.object 替换模块级 os.environ 引用为普通 dict，
    # 避免 Windows 上 os._Environ 的大小写归一化行为
    mock_os = type("MockOs", (), {"environ": env})()
    with (
        patch.object(shell_exec_tool, "os", mock_os),
        patch.object(shell_exec_tool, "sys", type("MockSys", (), {"platform": platform})()),
    ):
        result = sanitize_env()

    # 属性 1: 敏感且非保留的变量被移除
    for name in env:
        if _is_sensitive(name) and name not in preserved:
            assert name not in result, f"敏感且非保留的变量 {name!r} 应被移除，但仍在结果中"

    # 属性 2: 保留列表中的变量始终保留
    for name in env:
        if name in preserved:
            assert name in result, f"保留变量 {name!r} 应始终保留，但不在结果中"
            assert result[name] == env[name], (
                f"保留变量 {name!r} 的值不一致: 期望 {env[name]!r}, 实际 {result[name]!r}"
            )

    # 属性 3: 非敏感且非保留的变量也被保留
    for name in env:
        if not _is_sensitive(name) and name not in preserved:
            assert name in result, f"非敏感非保留变量 {name!r} 应被保留，但不在结果中"
            assert result[name] == env[name], (
                f"非敏感非保留变量 {name!r} 的值不一致: 期望 {env[name]!r}, 实际 {result[name]!r}"
            )
