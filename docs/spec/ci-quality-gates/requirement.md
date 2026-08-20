# 需求文档：CI 补齐前端、后端、安全和构建门禁

## 简介

当前仓库的 CI 已在 `.github/workflows/ci.yml` 中通过后端 `test` job 执行 `uv sync --frozen` 与 `uv run pytest -m "not benchmark"`，但前端 `epsilon-client` 仅在本地文档中提供 `lint` 与 `build` 命令，尚未通过 GitHub Actions 对 PR 自动执行前端 lint、TypeScript 类型检查与 Next.js 构建。质量门禁如果只停留在文档建议，无法在合并前阻断前端类型错误、构建失败或后端冻结依赖测试失败。

本特性从 `docs/plan2.md` 的 Task 5 抽取，目标是在不扩大业务功能面的前提下，把前端 lint/typecheck/build 与后端冻结依赖 pytest 纳入同一 CI 工作流，使其成为 push 与 pull request 的自动质量门禁。后端依赖安装必须继续遵循 `uv` 规范；前端依赖安装与脚本执行必须使用 Bun；安全门禁在本任务中限定为后端 pytest 已覆盖的静态安全测试随 `uv run pytest -m "not benchmark"` 一并执行，不新增独立安全扫描器。

本特性范围内包含：

- 在 `epsilon-client/package.json` 增加 `typecheck` 脚本，命令为 `tsc --noEmit`。
- 在 `.github/workflows/ci.yml` 增加前端 CI job，运行于 `ubuntu-latest`，使用 `oven-sh/setup-bun@v2`、`bun install --frozen-lockfile`、`bun run lint`、`bun run typecheck`、`bun run build`。
- 保持后端 CI job 使用 `uv sync --frozen` 与 `uv run pytest -m "not benchmark"`。
- 在 `docs/development.md` 的前端命令块补充 `bun run typecheck  # TypeScript 类型检查`。
- 本地验证前端 lint/typecheck/build；若受限沙箱导致 Next/Turbopack 本地端口绑定失败，则记录环境限制并要求在正常开发机或 CI 重跑。
- 验证最终 diff 仅触及 CI、前端脚本和开发文档相关文件。

明确不在本特性范围内：

- 不新增后端 ruff、DDD import guard 或其他后端静态检查能力；这些属于后续 Task 6。
- 不接入评估回归、nightly schedule 或基线评估；这些属于后续 Task 7。
- 不新增前端运行时 API schema 校验、Vitest、Playwright、单元测试或 E2E；这些属于后续 Task 8 与 Task 9。
- 不新增 Provider 健康策略、SLO、告警或运维手册；这些属于后续 Task 10。
- 不修改后端业务代码、前端业务源码、依赖 lockfile 或主配置文件。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| CI 工作流 | CI_Workflow | 仓库中的 `.github/workflows/ci.yml`，负责在 push 与 pull request 上运行自动化质量检查。 |
| PR 门禁 | Pull_Request_CI_Gate | GitHub Actions 在 pull request 上形成的合并阻断信号；任一必需 job 失败时应阻止合并。 |
| 前端项目 | Frontend_Project | `epsilon-client` 目录下的 Next.js 16 / React 19 / TypeScript 前端控制台。 |
| 前端脚本集合 | Frontend_Scripts | `epsilon-client/package.json` 中的 `scripts` 对象。 |
| 前端类型检查脚本 | Frontend_Typecheck_Script | `Frontend_Scripts` 中名为 `typecheck` 的脚本，命令必须为 `tsc --noEmit`。 |
| 前端 CI 任务 | Frontend_CI_Job | `CI_Workflow` 中新增的前端 job，运行前端依赖安装、lint、typecheck 与 build。 |
| Bun 冻结安装 | Bun_Frozen_Install | 在 `Frontend_Project` 工作目录执行的 `bun install --frozen-lockfile`，用于确保 CI 依赖解析不漂移。 |
| 前端 Lint 门禁 | Frontend_Lint_Gate | 在 `Frontend_CI_Job` 中执行的 `bun run lint` 检查。 |
| 前端类型检查门禁 | Frontend_Typecheck_Gate | 在 `Frontend_CI_Job` 中执行的 `bun run typecheck` 检查。 |
| 前端构建门禁 | Frontend_Build_Gate | 在 `Frontend_CI_Job` 中执行的 `bun run build` 检查。 |
| 后端 CI 任务 | Backend_CI_Job | `CI_Workflow` 中已有的后端测试 job，工作目录为 `epsilon-boot`。 |
| uv 冻结同步 | UV_Frozen_Sync | 在 `Backend_CI_Job` 中执行的 `uv sync --frozen`，用于确保后端依赖严格匹配锁文件。 |
| 后端 Pytest 门禁 | Backend_Pytest_Gate | 在 `Backend_CI_Job` 中执行的 `uv run pytest -m "not benchmark"` 后端测试检查。 |
| 安全静态检查 | Security_Static_Checks | 已纳入后端 pytest 收集范围的静态安全测试，例如 secret hygiene 或工具安全策略测试；本特性不新增独立安全扫描工具。 |
| 开发文档 | Development_Documentation | `docs/development.md`，记录本地开发、测试与质量门禁命令。 |
| 本地前端验证 | Local_Frontend_Validation | 实现本特性时在 `Frontend_Project` 下执行 `bun run lint`、`bun run typecheck`、`bun run build` 的人工或自动验证活动。 |
| 受限沙箱构建限制 | Restricted_Sandbox_Build_Limitation | 受限运行环境中 Next/Turbopack helper 进程因本地端口绑定权限不足导致 build 无法完成的环境限制。 |
| 变更范围 | Change_Scope | 本特性允许修改的文件集合：`.github/workflows/ci.yml`、`epsilon-client/package.json`、`docs/development.md`。 |
| 修改文件集合 | Modified_Files | 实现本特性后 `git diff` 中出现的实际修改文件。 |

## 需求

### 需求 1：前端提供 TypeScript 类型检查脚本

**用户故事：** 作为前端维护者，我希望 `package.json` 暴露统一的 TypeScript 类型检查脚本，以便本地开发和 CI 使用同一命令发现类型错误。

#### 验收标准

1. THE Frontend_Scripts SHALL define Frontend_Typecheck_Script with command `tsc --noEmit`.
2. WHEN Frontend_Typecheck_Gate executes, THE Frontend_Project SHALL run `bun run typecheck` against `Frontend_Typecheck_Script`.
3. THE Frontend_Typecheck_Script SHALL NOT add runtime dependencies, test scripts, e2e scripts, or unrelated frontend behavior.

### 需求 2：CI 增加前端 lint、typecheck 和 build 门禁

**用户故事：** 作为代码审查者，我希望 PR 自动运行前端 lint、TypeScript 类型检查和生产构建，以便在合并前阻断前端质量回归。

#### 验收标准

1. THE CI_Workflow SHALL define Frontend_CI_Job for `Frontend_Project` on `ubuntu-latest`.
2. THE Frontend_CI_Job SHALL use `oven-sh/setup-bun@v2` before installing frontend dependencies.
3. WHEN Frontend_CI_Job installs dependencies, THE Bun_Frozen_Install SHALL execute `bun install --frozen-lockfile` in `Frontend_Project`.
4. WHEN Frontend_CI_Job validates `Frontend_Project`, THE Frontend_Lint_Gate SHALL execute `bun run lint`.
5. WHEN Frontend_CI_Job validates `Frontend_Project`, THE Frontend_Typecheck_Gate SHALL execute `bun run typecheck`.
6. WHEN Frontend_CI_Job validates `Frontend_Project`, THE Frontend_Build_Gate SHALL execute `bun run build`.
7. IF Frontend_Lint_Gate IN failed state, THEN THE Pull_Request_CI_Gate SHALL fail.
8. IF Frontend_Typecheck_Gate IN failed state, THEN THE Pull_Request_CI_Gate SHALL fail.
9. IF Frontend_Build_Gate IN failed state, THEN THE Pull_Request_CI_Gate SHALL fail.

### 需求 3：后端 CI 保持 uv 冻结依赖测试门禁

**用户故事：** 作为后端维护者，我希望现有后端 CI 继续使用 uv 冻结依赖并运行非 benchmark 测试，以便保持后端依赖可复现和安全静态检查持续生效。

#### 验收标准

1. THE Backend_CI_Job SHALL keep `epsilon-boot` as its run working directory.
2. WHEN Backend_CI_Job installs backend dependencies, THE UV_Frozen_Sync SHALL execute `uv sync --frozen`.
3. WHEN Backend_CI_Job validates backend behavior, THE Backend_Pytest_Gate SHALL execute `uv run pytest -m "not benchmark"`.
4. THE Backend_Pytest_Gate SHALL include Security_Static_Checks that are already collected by pytest without adding a separate security scanner in this feature.
5. IF UV_Frozen_Sync IN failed state, THEN THE Pull_Request_CI_Gate SHALL fail.
6. IF Backend_Pytest_Gate IN failed state, THEN THE Pull_Request_CI_Gate SHALL fail.

### 需求 4：开发文档同步前端类型检查命令和本地验证约束

**用户故事：** 作为本地开发者，我希望开发文档列出与 CI 一致的前端类型检查命令，以便在提交前复现 PR 门禁。

#### 验收标准

1. THE Development_Documentation SHALL list `bun run typecheck  # TypeScript 类型检查` in the frontend command block.
2. WHEN Local_Frontend_Validation is performed, THE Frontend_Project SHALL run `bun run lint`, `bun run typecheck`, and `bun run build`.
3. IF Restricted_Sandbox_Build_Limitation IN triggered state, THEN THE Local_Frontend_Validation SHALL record the environment limitation and require rerun in a normal development environment or CI.
4. THE Development_Documentation SHALL preserve the existing note about Next/Turbopack build limitations in restricted sandboxes.

### 需求 5：变更范围保持为 CI、脚本和文档

**用户故事：** 作为项目维护者，我希望本任务的 diff 只包含门禁相关文件，以便 Task 5 独立交付且不混入后续任务范围。

#### 验收标准

1. THE Change_Scope SHALL allow modifications only to `.github/workflows/ci.yml`, `epsilon-client/package.json`, and `docs/development.md`.
2. FOR ALL Modified_Files, THE Change_Scope SHALL exclude backend source files, backend test files, frontend source files, dependency lockfiles, and configuration files.
3. THE Change_Scope SHALL NOT implement Task 6 backend static checks, Task 7 evaluation CI, Task 8 frontend runtime schema validation, Task 9 frontend tests, or Task 10 operations reliability work.
