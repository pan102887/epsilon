"""本地文件存储等级解析器模块。

把 StorageTier 映射到具体本地目录（PROJECT→<workspace>/.epsilon/、
USER→~/.epsilon/<project-hash>/），并对各子目录提供一致的“不存在时创建”
策略。属纯 infrastructure 实现细节，仅本模块知晓 .epsilon、~、WORKSPACE_ROOT。
project_hash() 为全仓库唯一的 project-hash 生成点，供会话主状态与 USER
tier 日志复用。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from domain.storage.storage_tier import StorageTier

_EPSILON_DIR_NAME = ".epsilon"
"""本地运行目录（Epsilon_Home）的顶层隐藏目录名。"""

_SUBDIRS: tuple[str, ...] = ("sessions", "traces", "artifacts", "logs")
"""本地运行目录下的标准子目录集合（会话摘要 / 追踪 / 产物 / 日志）。"""


@dataclass(frozen=True)
class ResolvedTierLayout:
    """某个 tier 解析后的本地目录布局。

    封装某一 StorageTier 解析出的顶层运行目录 home，并对各子目录提供统一的
    “不存在时创建”策略；不可变，供多个写入方共享同一解析结果。

    Attributes:
        home: 该 tier 的顶层运行目录（Epsilon_Home）。
    """

    home: Path

    def subdir(self, name: str, *, create: bool = True) -> Path:
        """返回指定子目录路径；create=True 时不存在则创建（含父级）。

        Args:
            name: 子目录名称（如 "traces"）。
            create: 为 True 时以 mkdir(parents=True, exist_ok=True) 幂等创建。

        Returns:
            子目录的绝对路径。
        """
        target = self.home / name
        if create:
            target.mkdir(parents=True, exist_ok=True)
        return target

    def sessions_dir(self, *, create: bool = True) -> Path:
        """会话摘要 / 恢复索引子目录（Sessions_Dir）。"""
        return self.subdir("sessions", create=create)

    def traces_dir(self, *, create: bool = True) -> Path:
        """结构化 trace 子目录（Traces_Dir，与既有 .epsilon/traces 等价）。"""
        return self.subdir("traces", create=create)

    def artifacts_dir(self, *, create: bool = True) -> Path:
        """任务产物子目录（Artifacts_Dir）。"""
        return self.subdir("artifacts", create=create)

    def logs_dir(self, *, create: bool = True) -> Path:
        """TUI/CLI 本地文件日志子目录（Logs_Dir）。"""
        return self.subdir("logs", create=create)


class LocalFileTierResolver:
    """StorageTier → 本地目录解析器。

    把逻辑存储等级映射到具体本地目录，是本地文件后端唯一知晓物理路径字面量
    （.epsilon、~、WORKSPACE_ROOT）的地方。对同一实例与基点，解析结果确定性。

    Args:
        project_base: PROJECT tier 基点（通常为 WORKSPACE_ROOT，空时由装配方
            传入进程 CWD）。将被规范化为绝对路径。
        user_base: USER tier 基点，默认 Path.home()。将被规范化为绝对路径。
    """

    def __init__(self, project_base: Path, user_base: Path | None = None) -> None:
        self._project_base = project_base.resolve()
        self._user_base = (user_base or Path.home()).resolve()

    def resolve(self, tier: StorageTier) -> ResolvedTierLayout:
        """把 tier 映射为 ResolvedTierLayout（确定性）。

        PROJECT 直接落 <project_base>/.epsilon/；USER 落 <user_base>/.epsilon/<project-hash>/，
        其运行产物子目录（logs 等）按 <project-hash> 分区以避免跨项目混淆
        （ADR-0005/0006）。

        Args:
            tier: 待解析的存储等级。

        Returns:
            解析后的目录布局。

        Raises:
            ValueError: tier 为 TENANT（本期无本地实现）时抛出。
        """
        if tier == StorageTier.PROJECT:
            return ResolvedTierLayout(home=self._project_base / _EPSILON_DIR_NAME)
        if tier == StorageTier.USER:
            # USER tier 运行产物按 project-hash 分区：<user_base>/.epsilon/<project-hash>/
            return ResolvedTierLayout(
                home=self._user_base / _EPSILON_DIR_NAME / self.project_hash()
            )
        raise ValueError(
            f"本地文件后端不支持 tier={tier.value}（TENANT 由云端 adapter 负责）"
        )

    def project_hash(self) -> str:
        """基于 PROJECT 基点规范化绝对路径生成确定性 project-hash（sha256 前 16 位）。

        **全仓库唯一的 project-hash 生成点**：会话主状态默认路径
        (`user_persistence_root`) 与 USER tier 运行产物（日志经
        `resolve(USER)`）均复用本方法，保证二者落在同一分区键下。不含原始
        路径明文，避免泄露宿主目录结构（ADR-0005/0006）。

        Returns:
            16 位十六进制字符串。
        """
        digest = hashlib.sha256(str(self._project_base).encode("utf-8")).hexdigest()
        return digest[:16]

    def user_persistence_root(self) -> Path:
        """返回 USER tier 会话主状态默认根：<user_base>/.epsilon/persistence/<project-hash>/。

        与 USER tier 运行产物（`resolve(USER).home` = <user_base>/.epsilon/<project-hash>/）
        共享同一 `project_hash()` 分区键；两者父级布局不同
        （persistence/<hash>/ vs <hash>/）但 hash 一致，便于按项目统一定位与清理。

        Returns:
            会话主状态默认根目录路径。
        """
        return self._user_base / _EPSILON_DIR_NAME / "persistence" / self.project_hash()
