"""后台 Run worker manager 实现。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from domain.run.ports import RunEventStorePort, RunStorePort
from domain.run.value_objects import RunEventType, RunStatus
from infrastructure.run.run_config import RunRuntimeConfig
from infrastructure.run.run_worker import RunWorker, run_log_extra
from infrastructure.run.worker_contracts import (
    RunRecoverySweep,
    RunRuntimeMetricsSink,
    RunSegmentExecutor,
)

logger = logging.getLogger(__name__)


class RunWorkerManager:
    """管理一组后台 RunWorker 和过期 lease sweep 任务。"""

    def __init__(
        self,
        *,
        run_store: RunStorePort,
        event_store: RunEventStorePort,
        executor: RunSegmentExecutor,
        config: RunRuntimeConfig,
        poll_interval_seconds: float | None = None,
        owner_prefix: str | None = None,
        metrics: RunRuntimeMetricsSink | None = None,
        recovery_sweep: RunRecoverySweep | None = None,
    ) -> None:
        self._run_store = run_store
        self._event_store = event_store
        self._executor = executor
        self._config = config
        self._poll_interval_seconds = (
            poll_interval_seconds
            if poll_interval_seconds is not None
            else config.heartbeat_interval_seconds
        )
        self._owner_prefix = owner_prefix or f"run-manager-{uuid.uuid4().hex}"
        self._metrics = metrics
        self._recovery_sweep = recovery_sweep
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._workers: list[RunWorker] = []

    @property
    def tasks(self) -> tuple[asyncio.Task[None], ...]:
        """返回当前后台任务，供测试确认生命周期。"""

        return tuple(self._tasks)

    async def start(self) -> None:
        """启动 worker 循环和 lost sweep 循环。"""

        if self._tasks:
            return
        self._stop_event.clear()
        self._wake_event.clear()
        self._workers = [
            RunWorker(
                run_store=self._run_store,
                event_store=self._event_store,
                executor=self._executor,
                lease_seconds=self._config.lease_seconds,
                heartbeat_interval_seconds=self._config.heartbeat_interval_seconds,
                auto_continue_paused_runs=self._config.auto_continue_paused_runs,
                auto_continue_max_segments=self._config.auto_continue_max_segments,
                owner_id=f"{self._owner_prefix}-{index}",
                metrics=self._metrics,
            )
            for index in range(self._config.worker_count)
        ]
        self._tasks = [asyncio.create_task(self._worker_loop(worker)) for worker in self._workers]
        self._tasks.append(asyncio.create_task(self._lost_sweep_loop()))

    async def stop(self) -> None:
        """优雅停止所有后台任务，避免测试或关闭流程泄露任务。"""

        if not self._tasks:
            return
        self._stop_event.set()
        self.wake_up()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._workers.clear()
        self._wake_event.clear()

    def wake_up(self) -> None:
        """唤醒正在轮询等待的 worker 和 sweep 循环。"""

        self._wake_event.set()

    async def _worker_loop(self, worker: RunWorker) -> None:
        while not self._stop_event.is_set():
            try:
                did_work = await worker.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                did_work = False

            if did_work:
                continue
            await self._wait_for_wake(self._poll_interval_seconds)

    async def _lost_sweep_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                now = datetime.now(UTC)
                if self._should_use_checkpoint_recovery():
                    assert self._recovery_sweep is not None
                    recovered_runs = await self._recovery_sweep.sweep_expired_leases(now=now)
                    if self._metrics is not None:
                        self._metrics.increment_lost(
                            len(
                                [
                                    snapshot
                                    for snapshot in recovered_runs
                                    if snapshot.status is RunStatus.LOST
                                ]
                            )
                        )
                else:
                    await self._run_stage_three_lost_sweep(now)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Run recovery sweep failed")

            await self._wait_for_wake(self._config.lost_sweep_interval_seconds)

    def _should_use_checkpoint_recovery(self) -> bool:
        return (
            self._config.checkpoint_enabled
            and self._config.checkpoint_auto_recovery_enabled
            and self._recovery_sweep is not None
        )

    async def _run_stage_three_lost_sweep(self, now: datetime) -> None:
        lost_runs = await self._run_store.mark_lost_expired_leases(now=now)
        if self._metrics is not None:
            self._metrics.increment_lost(len(lost_runs))
        for snapshot in lost_runs:
            await self._event_store.append_event(
                snapshot.run_id,
                RunEventType.RUN_LOST,
                {
                    "status": RunStatus.LOST.value,
                    "terminal_reason": snapshot.terminal_reason or "lease_expired",
                },
            )
            logger.warning(
                "Run marked lost by lease sweep",
                extra=run_log_extra(
                    snapshot,
                    worker_id=self._owner_prefix,
                    terminal_reason=snapshot.terminal_reason or "lease_expired",
                ),
            )

    async def _wait_for_wake(self, timeout_seconds: float) -> None:
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=timeout_seconds)
        except TimeoutError:
            return
        finally:
            self._wake_event.clear()
