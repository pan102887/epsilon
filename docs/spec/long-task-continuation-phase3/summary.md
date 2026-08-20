# 阶段三实现总结

## 完成范围

阶段三核心路径已完成：新增 `domain.run` 领域模型、状态机、异常与 Port；新增 `RunApplicationService`、`RunExecutionCoordinator`；实现本地文件与 Redis Run/Event Store；实现 `RunWorker` 与 `RunWorkerManager`；完成容器装配、TUI/agent adapter、可观测性、配置样例、集成测试和架构边界测试。

可选 adapter 已补齐：FastAPI Run router 作为薄 HTTP adapter 调用共享 `RunApplicationService`；Web 前端新增 Run API client、SSE replay fallback、Run View 和显式“后台运行”入口。TUI adapter 仍直接调用共享应用服务，不经 HTTP。

## 关键变更路径

- `epsilon-boot/src/domain/run/`
- `epsilon-boot/src/application/run/`
- `epsilon-boot/src/infrastructure/run/`
- `epsilon-boot/src/application/cli/runtime.py`
- `epsilon-boot/src/application/cli/commands.py`
- `epsilon-boot/src/application/cli/tui.py`
- `epsilon-boot/src/application/container_config.py`
- `epsilon-boot/src/application/api/routers/runs.py`
- `epsilon-client/src/lib/chat-api.ts`
- `epsilon-client/src/hooks/use-run.ts`
- `epsilon-client/src/components/run/`
- `epsilon-boot/config.properties`
- `docs/plan.md`

## 验证结果

最终全量验证在 `epsilon-boot/` 下执行：

```bash
env PYTHONPATH=src uv run --frozen pytest
```

结果：`2051 passed, 2 skipped in 139.38s`。

前端验证：

```bash
npm run lint
npm run build
```

结果：均通过。`npm run build` 首次在沙箱内因 Turbopack helper 进程本地端口绑定受限失败，提升权限重跑后通过。

## 剩余风险

- 阶段三不提供 checkpoint recovery；服务重启或 lease 过期后，未完成 Run 按设计进入 `lost`。
- FastAPI/Web 已作为可选薄 adapter 接入；后续如需生产化，可继续补端到端浏览器交互测试和更细的视觉回归。
