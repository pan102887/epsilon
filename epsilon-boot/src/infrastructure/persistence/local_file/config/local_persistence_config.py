"""本地持久化通用配置。

对应 ``LOCAL_PERSISTENCE_*`` 前缀，驱动 ``LocalFileSessionContextAdapter``
及其共享工具的运行参数。本期会话**无 TTL**（需求 2.补），因此本类
**不**定义 ``session_ttl_seconds`` / ``reaper_interval_seconds`` 字段。

为了实现需求 2.补.5 "遗留 TTL 键必须触发 fail-fast"：

- ``model_config`` 声明 ``extra="forbid"``，若模型构造参数中出现未知字段
  则 pydantic 直接抛 ``ValidationError``；
- 在 ``model_validator(mode="before")`` 中扫描 ``os.environ`` 中所有
  ``LOCAL_PERSISTENCE_*`` 前缀的键，若命中已废弃的黑名单键则抛
  ``ValueError``，由 pydantic 翻译为 ``ValidationError``。（单纯的
  ``env_prefix`` 环境变量源不会把未知键喂给模型，因此不会自然触发
  ``extra="forbid"`` 分支，故需显式黑名单校验。）

需求：5.1、5.2、5.3、5.9、2.补.4、2.补.5、11.6、12.5。
"""

import os
from typing import Any, ClassVar

from pydantic import model_validator
from pydantic_settings import SettingsConfigDict

from common.configuration import PropertiesBaseSettings, create_config

# 已废弃的配置键黑名单（需求 2.补.4、2.补.5）。外部环境中若出现这些键，
# 启动期直接拒绝，避免"老配置悄悄失效"的静默降级。
_DEPRECATED_ENV_KEYS: frozenset[str] = frozenset(
    {
        "LOCAL_PERSISTENCE_SESSION_TTL_SECONDS",
        "LOCAL_PERSISTENCE_REAPER_INTERVAL_SECONDS",
    }
)


class LocalPersistenceConfig(PropertiesBaseSettings):
    """对应 ``LOCAL_PERSISTENCE_*`` 配置前缀。

    Attributes:
        root: 本地持久化根目录。默认**空串**，表示启用 USER tier 默认迁移——
            装配期（``_init_local_persistence``）若检测到 ``root`` 为空且会话
            后端非 redis，则解析为 ``~/.epsilon/persistence/<project-hash>/``
            （ADR-0006）。**显式配置优先**：一旦设置了非空 ``LOCAL_PERSISTENCE_ROOT``
            则尊重该值、不做迁移；``SESSION_STORE_BACKEND=redis`` 时本项不生效。
            解析出的路径运行期由启动流程规范化为绝对路径，并默认避开 cwd workspace。
        create_if_missing: 目录不存在时是否自动创建（含父级），默认 ``True``。
        fsync_on_write: 写入后是否调用 ``os.fsync`` 以换取断电一致性,
            默认 ``True``。
        lock_acquire_timeout_ms: 文件锁获取超时（毫秒），默认 ``5000``。
        tmp_sweep_max_age_seconds: ``TmpFileSweeper`` 清理 ``"*.tmp-*"``
            残留的 ``mtime`` 阈值秒数，默认 ``3600``。**仅作用于半写 tmp
            文件**，不影响会话 JSON 的生命周期（本期会话无 TTL）。

    反向约束（需求 2.补.5）：

    - 本类**不**定义 ``session_ttl_seconds`` / ``reaper_interval_seconds``；
    - 启动期扫描 ``os.environ`` 中的已废弃 TTL / Reaper 键，命中即以
      ``ValidationError`` 拒绝启动；
    - ``hot_reload = False`` 保证进程生命周期内配置不可变。
    """

    hot_reload: ClassVar[bool] = False

    model_config = SettingsConfigDict(
        env_prefix="LOCAL_PERSISTENCE_",
        extra="forbid",
    )

    root: str = ""
    create_if_missing: bool = True
    fsync_on_write: bool = True
    lock_acquire_timeout_ms: int = 5000
    tmp_sweep_max_age_seconds: int = 3600

    @model_validator(mode="before")
    @classmethod
    def _reject_deprecated_ttl_env(cls, values: Any) -> Any:
        """若当前进程环境变量中出现已废弃的 TTL / Reaper 键，直接拒绝启动。

        ``env_prefix`` 环境变量源只把匹配到的"已知字段"传进模型，未知 env
        key 会被静默忽略，导致 ``extra="forbid"`` 无法拦截遗留配置。此处
        在构造阶段扫描 ``os.environ``，命中即抛 ``ValueError``（由 pydantic
        转译为 ``ValidationError``）。

        Args:
            values: pydantic 传入的原始输入（通常为字典）。

        Returns:
            原样返回 ``values``，仅做副作用（抛错）。

        Raises:
            ValueError: 当环境中存在黑名单键时抛出，错误消息为中文，明确
                指示该键在本期已废弃。
        """
        for deprecated_key in _DEPRECATED_ENV_KEYS:
            if deprecated_key in os.environ:
                raise ValueError(
                    f"已废弃的配置键 {deprecated_key} 被显式注入；"
                    "本期会话后端无 TTL / 无后台过期回收（需求 2.补）。"
                    "请从 env / config.properties 中移除该键。"
                )
        return values


local_persistence_config = create_config(LocalPersistenceConfig)
"""全局本地持久化配置实例；进程生命周期内不可变。"""
