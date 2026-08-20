"""后台 Run 运行时配置模块。

基于项目统一的 ``PropertiesBaseSettings`` 与 ``create_config`` 读取
``RUN_`` 前缀配置项。配置主源为 ``config.properties``，环境变量仅用于覆盖。
"""

from pydantic import model_validator
from pydantic_settings import SettingsConfigDict

from common.configuration import ConfigurationError, PropertiesBaseSettings, create_config
from domain.run import CheckpointRetentionPolicy, EventRetentionPolicy, RunCapacityPolicy


class RunRuntimeConfig(PropertiesBaseSettings):
    """阶段三后台 Run 运行时配置。

    Attributes:
        worker_enabled: 是否启动后台 Run worker，对应 ``RUN_WORKER_ENABLED``。
        worker_count: 后台 worker 数量，对应 ``RUN_WORKER_COUNT``。
        lease_seconds: worker 领取 Run 后的租约秒数，对应 ``RUN_LEASE_SECONDS``。
        heartbeat_interval_seconds: worker 刷新租约的间隔秒数，对应
            ``RUN_HEARTBEAT_INTERVAL_SECONDS``，必须小于 ``lease_seconds``。
        max_queued_runs: 最大排队 Run 数，对应 ``RUN_MAX_QUEUED_RUNS``。
        max_running_runs: 最大运行中 Run 数，对应 ``RUN_MAX_RUNNING_RUNS``。
        event_max_count: 单个 Run 保留的最大事件数，对应 ``RUN_EVENT_MAX_COUNT``。
        event_ttl_seconds: 事件保留 TTL 秒数，对应 ``RUN_EVENT_TTL_SECONDS``。
        event_stream_wait_seconds: 事件流长轮询等待秒数，对应
            ``RUN_EVENT_STREAM_WAIT_SECONDS``。
        lost_sweep_interval_seconds: 过期租约扫描间隔秒数，对应
            ``RUN_LOST_SWEEP_INTERVAL_SECONDS``。
        checkpoint_enabled: 是否启用阶段四 checkpoint，对应
            ``RUN_CHECKPOINT_ENABLED``。
        checkpoint_auto_recovery_enabled: 是否自动恢复过期租约运行，对应
            ``RUN_CHECKPOINT_AUTO_RECOVERY_ENABLED``。
        checkpoint_max_recovery_attempts: 自动恢复最大尝试次数，对应
            ``RUN_CHECKPOINT_MAX_RECOVERY_ATTEMPTS``。
        checkpoint_max_count: 单个 Run 保留 checkpoint 数量，对应
            ``RUN_CHECKPOINT_MAX_COUNT``。
        checkpoint_ttl_seconds: checkpoint 保留 TTL 秒数，对应
            ``RUN_CHECKPOINT_TTL_SECONDS``。
        checkpoint_max_payload_bytes: 单条 checkpoint 最大 payload 字节数，对应
            ``RUN_CHECKPOINT_MAX_PAYLOAD_BYTES``。
        checkpoint_tool_ledger_max_count: 单个 Run 保留工具账本数量，对应
            ``RUN_CHECKPOINT_TOOL_LEDGER_MAX_COUNT``。
        guardrail_runtime_convergence_enabled: 是否启用 Run guardrail 运行时收敛写路径，
            对应 ``RUN_GUARDRAIL_RUNTIME_CONVERGENCE_ENABLED``。
        auto_continue_paused_runs: 是否自动把可继续的 paused Run 重新入队，对应
            ``RUN_AUTO_CONTINUE_PAUSED_RUNS``。
        auto_continue_max_segments: 单个 Run 自动继续的最大段数，对应
            ``RUN_AUTO_CONTINUE_MAX_SEGMENTS``。
    """

    model_config = SettingsConfigDict(env_prefix="RUN_")

    worker_enabled: bool = True
    worker_count: int = 1
    lease_seconds: int = 60
    heartbeat_interval_seconds: int = 10
    max_queued_runs: int = 100
    max_running_runs: int = 2
    event_max_count: int = 1000
    event_ttl_seconds: int = 86400
    event_stream_wait_seconds: float = 15.0
    lost_sweep_interval_seconds: int = 30
    checkpoint_enabled: bool = True
    checkpoint_auto_recovery_enabled: bool = True
    checkpoint_max_recovery_attempts: int = 3
    checkpoint_max_count: int = 200
    checkpoint_ttl_seconds: int = 604800
    checkpoint_max_payload_bytes: int = 262144
    checkpoint_tool_ledger_max_count: int = 1000
    guardrail_runtime_convergence_enabled: bool = True
    auto_continue_paused_runs: bool = True
    auto_continue_max_segments: int = 20

    @model_validator(mode="after")
    def _validate_run_runtime_config(self) -> "RunRuntimeConfig":
        """校验后台 Run 配置，非法时 fail-fast 拒绝启动。"""
        positive_fields = {
            "RUN_WORKER_COUNT": self.worker_count,
            "RUN_LEASE_SECONDS": self.lease_seconds,
            "RUN_HEARTBEAT_INTERVAL_SECONDS": self.heartbeat_interval_seconds,
            "RUN_MAX_QUEUED_RUNS": self.max_queued_runs,
            "RUN_MAX_RUNNING_RUNS": self.max_running_runs,
            "RUN_EVENT_MAX_COUNT": self.event_max_count,
            "RUN_EVENT_TTL_SECONDS": self.event_ttl_seconds,
            "RUN_EVENT_STREAM_WAIT_SECONDS": self.event_stream_wait_seconds,
            "RUN_LOST_SWEEP_INTERVAL_SECONDS": self.lost_sweep_interval_seconds,
            "RUN_CHECKPOINT_MAX_COUNT": self.checkpoint_max_count,
            "RUN_CHECKPOINT_TTL_SECONDS": self.checkpoint_ttl_seconds,
            "RUN_CHECKPOINT_MAX_PAYLOAD_BYTES": self.checkpoint_max_payload_bytes,
            "RUN_CHECKPOINT_TOOL_LEDGER_MAX_COUNT": self.checkpoint_tool_ledger_max_count,
            "RUN_AUTO_CONTINUE_MAX_SEGMENTS": self.auto_continue_max_segments,
        }
        for key, value in positive_fields.items():
            if value <= 0:
                raise ConfigurationError(f"{key} 必须为正数")

        if self.checkpoint_max_recovery_attempts < 0:
            raise ConfigurationError("RUN_CHECKPOINT_MAX_RECOVERY_ATTEMPTS 不得小于 0")

        if self.heartbeat_interval_seconds >= self.lease_seconds:
            raise ConfigurationError("RUN_HEARTBEAT_INTERVAL_SECONDS 必须小于 RUN_LEASE_SECONDS")
        return self

    def to_capacity_policy(self) -> RunCapacityPolicy:
        """将运行时容量配置转换为领域层容量策略。"""
        return RunCapacityPolicy(
            max_queued_runs=self.max_queued_runs,
            max_running_runs=self.max_running_runs,
        )

    def to_event_retention_policy(self) -> EventRetentionPolicy:
        """将运行时事件配置转换为领域层事件保留策略。"""
        return EventRetentionPolicy(
            max_event_count=self.event_max_count,
            ttl_seconds=self.event_ttl_seconds,
        )

    def to_checkpoint_retention_policy(self) -> CheckpointRetentionPolicy:
        """将 checkpoint 配置转换为领域层保留策略。"""
        return CheckpointRetentionPolicy(
            max_checkpoint_count=self.checkpoint_max_count,
            ttl_seconds=self.checkpoint_ttl_seconds,
            max_payload_bytes=self.checkpoint_max_payload_bytes,
            max_tool_ledger_count=self.checkpoint_tool_ledger_max_count,
        )


run_runtime_config = create_config(RunRuntimeConfig)
"""全局后台 Run 运行时配置实例，通过项目配置工厂创建。"""
