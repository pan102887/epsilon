"""交付路径守卫自测（Property 1）。

通过 ``git diff --name-only HEAD`` 收集当前工作区相对 HEAD 的变更路径，
断言全部落在 spec-ai-evaluation 允许的四类前缀下：

- ``docs/evaluation/``
- ``tests/evaluation/``
- ``scripts/evaluation/``
- ``docs/spec/spec-ai-evaluation/``  —— 本特性自身的 tasks.md /
  review-log.md 等控制文件在生成器循环中会被修改，属于允许例外。

非 git 仓库 / 无 HEAD 时 ``pytest.skip`` —— 该守卫是 CI 层面的最终检查，
本地首次 clone 情况下可容忍。

对应 Property 1；需求 7.2。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.evaluation_self


_ALLOWED_PREFIXES: tuple[str, ...] = (
    "docs/evaluation/",
    "tests/evaluation/",
    "scripts/evaluation/",
    "docs/spec/spec-ai-evaluation/",
)


def _repo_root() -> Path:
    """推断仓库根目录（``tests/evaluation/self_tests/`` → 仓库根）。"""

    return Path(__file__).resolve().parents[3]


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """调用 git 子命令并捕获输出。"""

    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def test_delivery_paths_are_whitelisted() -> None:
    """所有变更路径必须以四类白名单前缀之一开头。"""

    root = _repo_root()

    # 非 git 仓库或 git 不可用时跳过。
    which = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if which.returncode != 0:
        pytest.skip("非 git 仓库或 git 不可用，跳过交付路径守卫。")

    # 无 HEAD（新仓库首个提交前）时跳过。
    has_head = _run_git(["rev-parse", "--verify", "HEAD"], root)
    if has_head.returncode != 0:
        pytest.skip("仓库无 HEAD（尚未首个提交），跳过交付路径守卫。")

    # 已跟踪文件：git diff --name-only HEAD
    tracked = _run_git(["diff", "--name-only", "HEAD"], root)
    if tracked.returncode != 0:
        pytest.skip(f"git diff 失败，跳过：{tracked.stderr.strip()}")

    # 未跟踪新增：git ls-files --others --exclude-standard
    untracked = _run_git(
        ["ls-files", "--others", "--exclude-standard"], root
    )
    if untracked.returncode != 0:
        pytest.skip(f"git ls-files 失败，跳过：{untracked.stderr.strip()}")

    changed: list[str] = []
    for line in (tracked.stdout + untracked.stdout).splitlines():
        line = line.strip()
        if not line:
            continue
        changed.append(line)

    violations = [
        p for p in changed if not any(p.startswith(prefix) for prefix in _ALLOWED_PREFIXES)
    ]
    assert not violations, (
        f"检测到不在白名单下的变更路径：{violations}；"
        f"允许前缀 {_ALLOWED_PREFIXES}"
    )
