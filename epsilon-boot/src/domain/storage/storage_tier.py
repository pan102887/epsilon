"""存储等级（StorageTier）领域枚举模块。

定义产物存储的逻辑定位维度，供 TraceStorePort / ArtifactStorePort 及其
读写方使用。本模块仅依赖标准库，不含任何物理路径或后端实现细节。
"""

from __future__ import annotations

from enum import StrEnum


class StorageTier(StrEnum):
    """产物存储等级。

    作为产物（trace/artifact/会话主状态/日志）的逻辑定位维度，
    由基础设施层的解析器映射到具体后端/目录。领域层与写入方只依赖本枚举。

    取值：
        USER: 用户级，跨项目、单用户、强一致。
        PROJECT: 项目级，随工作区/仓库。
        TENANT: 租户级（云端多租户），本期仅预留，不实现对应后端与可见性策略。
    """

    USER = "user"
    PROJECT = "project"
    TENANT = "tenant"
