## 2026-06-07 领域层任务 1.1-1.8
- Attempt 1: PASS by spec_evaluator.
- Changed: domain/run value objects, exceptions, state machine, ports, and focused domain tests.
- Verification: `cd epsilon-boot && env PYTHONPATH=src uv run --frozen pytest test/domain/run -q` -> 24 passed.

## 2026-06-07 配置任务 4.1-4.2
- Attempt 1: PASS by spec_evaluator.
- Changed: infrastructure/run RunRuntimeConfig and focused config tests.
- Verification: `cd epsilon-boot && env PYTHONPATH=src uv run --frozen pytest test/infrastructure/run/test_run_config_unit.py -q` -> 13 passed.

## 2026-06-07 应用服务任务 2.1-2.2
- Attempt 1: FAIL by spec_evaluator.
- Blocking: resume_approval_run used fake owner_id="approval_resume" with worker mark_* methods, conflicting with lease owner boundary. Upstream design/tasks need an approval-specific store method or queued-only policy before implementation can pass.
- Attempt 2: PASS by spec_evaluator.
- Changed: added domain ApprovalResumeStoreResult and RunStorePort.resolve_approval_resume, then updated RunApplicationService.resume_approval_run to use the approval-specific store path without fake worker owner.
- Verification: `cd epsilon-boot && env PYTHONPATH=src uv run --frozen pytest test/domain/run/test_run_ports_unit.py test/application/run/test_run_application_service_unit.py -q` -> 24 passed.

## 2026-06-07 检查点 2
- Attempt 1: PASS by coordinator verification.
- Verification: `cd epsilon-boot && env PYTHONPATH=src uv run --frozen pytest` -> 1958 passed, 2 skipped.

## 2026-06-07 执行协调器任务 3.1-3.2
- Attempt 1: PASS by spec_evaluator.
- Changed: added RunExecutionCoordinator/RunExecutionOutcome and focused unit tests for chat/task initial and continue execution paths.
- Verification: `cd epsilon-boot && env PYTHONPATH=src uv run --frozen pytest test/application/run/test_run_execution_coordinator_unit.py -q` -> 9 passed.

## 2026-06-07 本地文件存储任务 5.1-5.2
- Attempt 1: FAIL by spec_evaluator.
- Blocking: concurrency tests used asyncio gather over async methods with synchronous bodies, so claim_next and idempotent create were not truly exercised under concurrent file-lock contention.
- Attempt 2: PASS by spec_evaluator.
- Changed: added LocalFileRunStoreAdapter with snapshot/event/idempotency-index persistence, and strengthened contract tests with real OS-thread concurrency for claim and idempotent create.
- Verification: `cd epsilon-boot && env PYTHONPATH=src uv run --frozen pytest test/infrastructure/run/test_local_file_run_store_adapter_unit.py -q` -> 10 passed.

## 2026-06-07 Redis 存储任务 6.1-6.2
- Attempt 1: PASS by spec_evaluator.
- Changed: added RedisRunStoreAdapter with transactional snapshot, queue, running-set, idempotency-index, and event-list behavior plus fakeredis contract tests.
- Verification: `cd epsilon-boot && env PYTHONPATH=src uv run --frozen pytest test/infrastructure/run/test_redis_run_store_adapter_unit.py -q` -> 11 passed.

## 2026-06-07 Worker 任务 7.1-7.2
- Attempt 1: FAIL by spec_evaluator.
- Blocking: awaiting_approval without approval_id could persist an unrecoverable empty approval_id, and tests lacked before-segment cancel plus heartbeat stop-condition coverage.
- Attempt 2: PASS by spec_evaluator.
- Changed: added RunWorker/RunWorkerManager and tests; fixed missing approval_id to fail safely and strengthened cancel/heartbeat coverage.
- Verification: `cd epsilon-boot && env PYTHONPATH=src uv run --frozen pytest test/infrastructure/run/test_run_worker_unit.py -q` -> 15 passed.

## 2026-06-07 检查点 7
- Attempt 1: PASS by coordinator verification.
- Verification: `cd epsilon-boot && env PYTHONPATH=src uv run --frozen pytest` -> 2003 passed, 2 skipped.

## 2026-06-07 容器装配任务 8.1-8.2
- Attempt 1: PASS by spec_evaluator.
- Changed: wired Run store/event store/application service/execution coordinator/worker manager into container_config with FILE/REDIS backend dispatch and worker lifecycle tests.
- Verification: `cd epsilon-boot && env PYTHONPATH=src uv run --frozen pytest test/application/test_run_container_wiring_unit.py test/application/test_container_config_backend_dispatch.py -q` -> 10 passed.

## 2026-06-07 TUI/agent adapter 任务 10.1-10.4
- Attempt 1: FAIL by spec_evaluator.
- Blocking: `/runs` returned only a placeholder instead of listing known Run snapshots with run_id/status/can_continue/latest cursor/error summary.
- Attempt 2: PASS by spec_evaluator.
- Changed: added RunApplicationService-backed CLI runtime methods, Run slash commands, TUI Run rendering/watch/cancel behavior, known-run listing, and focused TUI adapter tests.
- Verification: `cd epsilon-boot && env PYTHONPATH=src uv run --frozen pytest test/application/cli/test_runtime.py test/application/cli/test_commands.py test/application/cli/test_tui_run_view.py test/application/cli/test_tui_textual.py test/application/cli/test_tui_hitl_approval.py -q` -> 27 passed.

## 2026-06-07 检查点 11
- Attempt 1: FAIL by coordinator verification.
- Blocking: `test_run_container_wiring_unit.py` used package-imported class identity while the test loads `container_config.py` through `spec_from_file_location`, causing full-suite registry assertions to compare different class objects.
- Attempt 2: PASS by coordinator verification.
- Changed: adjusted the wiring test to assert against the class objects used by the loaded container_config module.
- Verification: `cd epsilon-boot && env PYTHONPATH=src uv run --frozen pytest` -> 2021 passed, 2 skipped.

## 2026-06-07 收尾任务 12.1-12.5
- Attempt 1: PASS by spec_evaluator.
- Changed: added phase-three backend integration tests, observability metrics/logging tests, Run runtime config defaults, docs/plan stage-three boundaries, and architecture static tests.
- Verification:
  - `cd epsilon-boot && env PYTHONPATH=src uv run --frozen pytest test/application/test_long_task_phase3_integration.py -q` -> 5 passed.
  - `cd epsilon-boot && env PYTHONPATH=src uv run --frozen pytest test/application/test_long_task_phase3_observability_unit.py test/infrastructure/run/test_run_worker_unit.py test/application/run/test_run_application_service_unit.py -q` -> 38 passed.
  - `cd epsilon-boot && env PYTHONPATH=src uv run --frozen pytest test/application/test_long_task_phase3_architecture_static.py -q` -> 5 passed.

## 2026-06-07 最终验证 12.6 / 检查点 12
- Attempt 1: PASS by coordinator verification.
- Verification: `cd epsilon-boot && env PYTHONPATH=src uv run --frozen pytest` -> 2036 passed, 2 skipped.
- Scope note: optional FastAPI/Web/frontend starred tasks 9.* and 11.* were not implemented per the project decision to prioritize core agent runtime quality; no frontend lint/build command was required for this batch.

## 2026-06-07 可选 FastAPI adapter 任务 9.1-9.3
- Attempt 1: PASS by coordinator verification.
- Changed: added optional Run FastAPI router, backward-compatible router export, server registration, and focused HTTP adapter tests.
- Verification:
  - `cd epsilon-boot && env PYTHONPATH=src uv run --frozen pytest test/application/routers/test_runs_router_unit.py -q` -> 11 passed.
  - `cd epsilon-boot && env PYTHONPATH=src uv run --frozen pytest test/application/test_long_task_phase3_architecture_static.py test/application/routers/test_runs_router_unit.py -q` -> 16 passed.

## 2026-06-07 可选前端任务 11.1-11.4 / 最终复验
- Attempt 1: PASS by coordinator verification.
- Changed: added Run API client types/functions, Run event streaming with replay_expired fallback, useRun hook, Run View/Event List components, explicit background-run entry points in Chat/Task UI, page integration, and frontend static contract tests.
- Verification:
  - `cd epsilon-boot && env PYTHONPATH=src uv run --frozen pytest test/application/routers/test_runs_router_unit.py test/application/test_long_task_phase3_architecture_static.py test/application/test_long_task_phase3_frontend_contract_static.py -q` -> 20 passed.
  - `cd epsilon-client && npm run lint` -> passed.
  - `cd epsilon-client && npm run build` -> passed after rerun outside sandbox because Turbopack helper process needed local port binding.
  - `cd epsilon-boot && env PYTHONPATH=src uv run --frozen pytest` -> 2051 passed, 2 skipped.
