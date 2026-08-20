"""npm 到 bun 迁移属性测试模块。

使用 Hypothesis 对 npm 到 bun 迁移的核心属性进行验证：
- 属性 1：package.json 内容不变性 —— bun install 不应修改 package.json 的内容
"""

import json
import os
import re
import shutil
import subprocess
import tempfile

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

# ── 项目路径常量 ──

# 前端项目目录（相对于仓库根目录）
_CLIENT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "epsilon-client")
_PACKAGE_JSON_PATH = os.path.join(_CLIENT_DIR, "package.json")


# ── Hypothesis 策略：生成合法的 package.json 内容 ──

# npm 包名策略：小写字母、数字、连字符，符合 npm 命名规范
_npm_package_name_st = st.from_regex(r"[a-z][a-z0-9\-]{0,20}", fullmatch=True)

# 语义化版本号策略
_semver_st = st.builds(
    lambda major, minor, patch: f"{major}.{minor}.{patch}",
    major=st.integers(min_value=0, max_value=99),
    minor=st.integers(min_value=0, max_value=99),
    patch=st.integers(min_value=0, max_value=99),
)

# 版本范围策略：精确版本或带前缀的范围
_version_range_st = st.one_of(
    _semver_st,
    _semver_st.map(lambda v: f"^{v}"),
    _semver_st.map(lambda v: f"~{v}"),
    _semver_st.map(lambda v: f">={v}"),
)

# 依赖字典策略：0~5 个依赖项
_dependencies_st = st.dictionaries(
    keys=_npm_package_name_st,
    values=_version_range_st,
    min_size=0,
    max_size=5,
)

# scripts 字典策略：常见的 npm scripts
_script_name_st = st.sampled_from(
    [
        "dev",
        "build",
        "start",
        "lint",
        "test",
        "format",
        "clean",
        "prebuild",
        "postbuild",
        "prepare",
    ]
)
_script_command_st = st.from_regex(r"[a-z][a-z0-9 \-]{0,30}", fullmatch=True)
_scripts_st = st.dictionaries(
    keys=_script_name_st,
    values=_script_command_st,
    min_size=0,
    max_size=5,
)

# 完整的 package.json 策略
_package_json_st = st.fixed_dictionaries(
    {
        "name": _npm_package_name_st,
        "version": _semver_st,
        "private": st.booleans(),
    },
    optional={
        "description": st.text(min_size=0, max_size=50),
        "scripts": _scripts_st,
        "dependencies": _dependencies_st,
        "devDependencies": _dependencies_st,
    },
)


# ── Property 1: package.json 内容不变性 ──
# Feature: npm-to-bun-migration, Property 1: package.json 内容不变性


@pytest.mark.skipif(shutil.which("bun") is None, reason="bun 未安装，跳过此集成测试")
@settings(max_examples=100, deadline=60_000)
@given(package_json=_package_json_st)
def test_bun_install_does_not_modify_package_json(
    package_json: dict,
) -> None:
    """验证 bun install 不修改 package.json 的内容。

    **Validates: Requirements 1.3**

    对于任意合法的 package.json 内容，在临时目录中写入该文件后执行
    bun install，package.json 的内容应与执行前完全一致。
    这确保 bun 作为包管理器不会擅自修改项目的依赖声明文件。
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        pkg_path = os.path.join(tmp_dir, "package.json")

        # 写入生成的 package.json，使用 indent=2 模拟标准格式
        original_content = json.dumps(package_json, indent=2, ensure_ascii=False) + "\n"
        with open(pkg_path, "w", encoding="utf-8") as f:
            f.write(original_content)

        # 执行 bun install
        result = subprocess.run(
            ["bun", "install"],
            cwd=tmp_dir,
            capture_output=True,
            timeout=30,
        )

        # bun install 可能因为依赖不存在而失败，但不应修改 package.json
        # 无论安装成功与否，package.json 内容都不应被改变
        with open(pkg_path, encoding="utf-8") as f:
            after_content = f.read()

        assert after_content == original_content, (
            f"bun install 修改了 package.json！\n"
            f"执行前:\n{original_content}\n"
            f"执行后:\n{after_content}\n"
            f"bun install 退出码: {result.returncode}"
        )


# ── Hypothesis 策略：生成包含前端和后端命令的 AGENTS.md 内容 ──

# 后端命令策略：包含 `uv` 的行，模拟真实的后端命令
_backend_command_st = st.sampled_from(
    [
        "cd epsilon-boot && uv sync",
        "cd epsilon-boot && uv run python main.py",
        "cd epsilon-boot && uv run pytest",
        "cd epsilon-boot && uv run pytest test/path/to/test_file.py -q",
        "uv add some-package",
        "uv remove old-package",
        "uv run mypy src/",
        "uv pip list",
    ]
)

# 前端命令策略：包含 `npm` 的行，模拟迁移前的前端命令
_frontend_npm_command_st = st.sampled_from(
    [
        "cd epsilon-client && npm install",
        "cd epsilon-client && npm run dev",
        "cd epsilon-client && npm run lint",
        "cd epsilon-client && npm run build",
        "npm install some-package",
        "npm run test",
    ]
)

# 普通文本行策略：不含 npm 或 uv 的普通文档行
_plain_line_st = st.from_regex(r"[A-Za-z][A-Za-z0-9 ,.]{0,60}", fullmatch=True)

# 单行策略：随机选择后端命令行、前端命令行或普通文本行
_agents_md_line_st = st.one_of(
    _backend_command_st.map(lambda cmd: f"- `{cmd}`"),
    _frontend_npm_command_st.map(lambda cmd: f"- `{cmd}`"),
    _plain_line_st,
)

# 完整 AGENTS.md 内容策略：至少包含 1 行后端命令和 1 行前端命令
_agents_md_content_st = st.builds(
    lambda backend_lines, frontend_lines, other_lines: "\n".join(
        backend_lines + frontend_lines + other_lines
    ),
    backend_lines=st.lists(
        _backend_command_st.map(lambda cmd: f"- `{cmd}`"), min_size=1, max_size=5
    ),
    frontend_lines=st.lists(
        _frontend_npm_command_st.map(lambda cmd: f"- `{cmd}`"), min_size=1, max_size=5
    ),
    other_lines=st.lists(_plain_line_st, min_size=0, max_size=5),
)


def _perform_npm_to_bun_replacement(content: str) -> str:
    """对 AGENTS.md 内容执行前端命令的 npm → bun 替换。

    仅替换前端上下文中的 npm 命令：
    - npm install → bun install
    - npm run dev → bun run dev
    - npm run lint → bun run lint

    后端命令（包含 uv 的行）不应受到影响。
    """
    result = re.sub(r"\bnpm install\b", "bun install", content)
    result = re.sub(r"\bnpm run dev\b", "bun run dev", result)
    result = re.sub(r"\bnpm run lint\b", "bun run lint", result)
    result = re.sub(r"\bnpm run build\b", "bun run build", result)
    result = re.sub(r"\bnpm run test\b", "bun run test", result)
    return result


# ── Property 2: 后端命令不变性 ──
# Feature: npm-to-bun-migration, Property 2: 后端命令不变性


@settings(max_examples=100)
@given(content=_agents_md_content_st)
def test_backend_commands_unchanged_after_npm_to_bun_replacement(
    content: str,
) -> None:
    """验证前端命令替换（npm → bun）不影响后端命令行。

    **Validates: Requirements 2.4**

    对于任意包含后端命令（含 uv 的行）和前端命令（含 npm 的行）的
    AGENTS.md 内容，执行 npm → bun 替换后，所有包含 uv 的行应与
    替换前完全一致。这确保文档更新操作仅影响前端命令，不影响后端命令。
    """
    # 记录替换前所有包含 uv 的行（后端命令行）
    original_lines = content.splitlines()
    original_backend_lines = [line for line in original_lines if "uv" in line]

    # 执行前端命令替换
    replaced_content = _perform_npm_to_bun_replacement(content)

    # 提取替换后所有包含 uv 的行
    replaced_lines = replaced_content.splitlines()
    replaced_backend_lines = [line for line in replaced_lines if "uv" in line]

    # 验证后端命令行数量不变
    assert len(replaced_backend_lines) == len(original_backend_lines), (
        f"后端命令行数量发生变化！\n"
        f"替换前: {len(original_backend_lines)} 行\n"
        f"替换后: {len(replaced_backend_lines)} 行"
    )

    # 验证每一行后端命令内容不变
    for i, (orig, repl) in enumerate(
        zip(original_backend_lines, replaced_backend_lines, strict=True)
    ):
        assert orig == repl, f"第 {i + 1} 行后端命令被修改！\n替换前: {orig!r}\n替换后: {repl!r}"
