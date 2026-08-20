# 交付总结：CI 补齐前端、后端、安全和构建门禁

## Feature Slug

`ci-quality-gates`

## 最终产物

- `docs/spec/ci-quality-gates/requirement.md`：从 `docs/plan2.md` Task 5 抽取的需求文档。
- `docs/spec/ci-quality-gates/design.md`：技术设计文档，包含 CI job 结构、脚本接口、错误传播、验证策略和范围约束。
- `docs/spec/ci-quality-gates/tasks.md`：实现计划；所有任务与子任务均已完成。
- `docs/spec/ci-quality-gates/review-log.md`：评审记录；最终 evaluator verdict 为 PASS。
- `.github/workflows/ci.yml`：新增独立 `frontend` job，保留既有后端 `test` job。
- `epsilon-client/package.json`：新增 `typecheck` 脚本。
- `docs/development.md`：前端本地命令块补充 TypeScript 类型检查命令。

## 关键设计决策

- 前端类型检查统一暴露为 `bun run typecheck`，脚本命令精确为 `tsc --noEmit`。
- CI 保留原后端 `test` job 名称、OS matrix、`uv sync --frozen` 与 `uv run pytest -m "not benchmark"`，避免破坏既有状态检查和后端依赖管理规范。
- CI 新增独立 `frontend` job，不声明 `needs: test`，使前端和后端门禁可并行反馈。
- 前端 job 使用 Bun 冻结安装、lint、typecheck、build 顺序：`bun install --frozen-lockfile` → `bun run lint` → `bun run typecheck` → `bun run build`。
- 未新增独立安全扫描器；本任务中的安全门禁仍由后端 pytest 已收集的静态安全测试覆盖。
- 严格限制实现变更范围为 CI、前端脚本和开发文档；未修改业务源码、配置、依赖声明或 lockfile。

## 验证覆盖

已通过本地验证：

```bash
env -C /home/jupeter/source/epsilon/epsilon-client bun run typecheck
env -C /home/jupeter/source/epsilon/epsilon-client bun run lint
env -C /home/jupeter/source/epsilon/epsilon-client bun run build
```

验证结果：三条前端命令均通过。`bun run build` 仅出现 Next.js workspace-root warning，原因是仓库外存在 `/home/jupeter/package-lock.json`，该 warning 不影响构建结果。

已完成范围验证：

```bash
git diff --name-only
git diff -- .github/workflows/ci.yml epsilon-client/package.json docs/development.md
```

跟踪文件 diff 仅包含：

- `.github/workflows/ci.yml`
- `docs/development.md`
- `epsilon-client/package.json`

Evaluator verdict：PASS，可接受并已将 `docs/spec/ci-quality-gates/tasks.md` 全部勾选完成。

## 后续事项

- 在 push 或 pull request 后观察 GitHub Actions `ci` workflow，确认新增 `frontend` job 与既有后端 `test` job 都按预期运行。
- 若 CI 环境也出现 Next.js workspace-root warning，可按 Next.js 提示显式配置 `outputFileTracingRoot`；该项不是本 Task 5 的必要交付。
- Task 6/7/8/9/10 仍保持独立后续范围：后端静态检查、评估回归、前端 API 契约校验、前端自动化测试和运维可靠性不在本次变更中实现。
