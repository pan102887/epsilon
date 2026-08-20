# 实现计划：CI 补齐前端、后端、安全和构建门禁

## 概述

本计划把已批准的设计拆分为可逐项执行的实现、验证和检查点任务。实现范围严格限定为 `.github/workflows/ci.yml`、`epsilon-client/package.json`、`docs/development.md` 三个文件：新增前端 `typecheck` 脚本、在既有 `ci` workflow 中追加独立前端 job，并同步开发文档中的本地前端验证命令。

本特性不新增 DDL、索引、数据回填、后端业务源码、前端业务源码、依赖、lockfile、配置项或独立安全扫描器。仓库当前 SQL / 数据库迁移脚本的规范目录为 `epsilon-boot/migrations/`，本计划不需要在该目录新增任何文件。

## Tasks

- [x] 1. 补齐前端 TypeScript 类型检查脚本
  - [x] 1.1 在前端脚本集合中新增 `typecheck`
    - 修改 `epsilon-client/package.json`
    - 在 `scripts` 对象中新增精确键值：`"typecheck": "tsc --noEmit"`
    - 保持既有 `dev`、`build`、`start`、`lint` 脚本命令不变，其中 `lint` 继续为 `eslint .`
    - 不修改 `dependencies`、`devDependencies`、`bun.lock`、`packageManager` 或其他 package 元数据
    - 不新增 `test`、`e2e`、`schema`、`security` 等超出本特性范围的脚本
    - _需求: 1.1, 1.2, 1.3, 5.1, 5.2, 5.3_
  - [x] 1.2 验证前端 `typecheck` 脚本可被 Bun 调用
    - 在 `epsilon-client/` 下执行 `bun run typecheck`
    - 确认该命令调用 `tsc --noEmit`，且不会生成运行时产物或修改 lockfile
    - 若命令失败，记录 TypeScript 错误并保持脚本定义不弱化、不绕过
    - _需求: 1.2, 4.2, 5.2_
  - [x] 1.3 检查前端脚本变更范围
    - 检查 `git diff -- epsilon-client/package.json epsilon-client/bun.lock`
    - 确认 diff 仅包含 `epsilon-client/package.json` 的 `scripts.typecheck` 追加，且 `epsilon-client/bun.lock` 无变化
    - 确认没有新增运行时依赖、测试框架依赖或前端业务源码修改
    - _需求: 1.3, 5.1, 5.2, 5.3_

- [x] 2. 在 CI workflow 中新增独立前端质量门禁
  - [x] 2.1 保留既有后端 `test` job 的 uv 冻结依赖与 pytest 门禁
    - 修改 `./.github/workflows/ci.yml`
    - 保留 workflow 名称 `ci` 以及 `push` / `pull_request` 触发语义
    - 保留后端 job id `test`，不重命名该 job，避免破坏既有状态检查名称
    - 保留 `strategy.fail-fast: false` 与 `matrix.os: [ubuntu-latest, windows-latest]`
    - 保留 `runs-on: ${{ matrix.os }}`
    - 保留 `defaults.run.working-directory: epsilon-boot` 与 `defaults.run.shell: bash`
    - 保留 `astral-sh/setup-uv@v3`
    - 保留 `uv sync --frozen`
    - 保留 `uv run pytest -m "not benchmark"`
    - 不引入 `pip`、`poetry`、`pipenv`、`conda` 或独立安全扫描器
    - _需求: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 5.3_
  - [x] 2.2 新增前端 `frontend` job
    - 修改 `./.github/workflows/ci.yml`
    - 在 `jobs` 下新增 job id `frontend`
    - 设置 `runs-on: ubuntu-latest`
    - 设置 `defaults.run.working-directory: epsilon-client`
    - 设置 `defaults.run.shell: bash`
    - 添加 `Checkout` step，使用 `actions/checkout@v4`
    - 添加 `Install Bun` step，使用 `oven-sh/setup-bun@v2`
    - 添加 `Install frontend dependencies` step，执行 `bun install --frozen-lockfile`
    - 添加 `Run frontend lint` step，执行 `bun run lint`
    - 添加 `Run frontend typecheck` step，执行 `bun run typecheck`
    - 添加 `Run frontend build` step，执行 `bun run build`
    - 不声明 `needs: test`，使前端 job 与后端 job 可独立并行运行
    - 不添加缓存、artifact 上传、secrets 读取或 `NEXT_PUBLIC_API_BASE_URL` 环境变量
    - 不添加 `continue-on-error: true`
    - _需求: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9, 5.1, 5.3_
  - [x] 2.3 静态验证 CI workflow 的前后端门禁结构
    - 检查 `./.github/workflows/ci.yml` 中 `test` job 仍以 `epsilon-boot` 为默认工作目录
    - 检查 `./.github/workflows/ci.yml` 中后端命令仍为 `uv sync --frozen` 与 `uv run pytest -m "not benchmark"`
    - 检查 `./.github/workflows/ci.yml` 中 `frontend` job 包含 `ubuntu-latest`、`oven-sh/setup-bun@v2`、`bun install --frozen-lockfile`、`bun run lint`、`bun run typecheck`、`bun run build`
    - 检查 `./.github/workflows/ci.yml` 中不存在 `continue-on-error: true`
    - 检查 `./.github/workflows/ci.yml` 中不存在 `pip`、`poetry`、`pipenv`、`conda`、新增安全扫描器或前端测试框架命令
    - _需求: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.1, 3.2, 3.3, 3.4, 5.3_
  - [x] 2.4 检查 CI job 失败会保持为 PR 门禁失败信号
    - 确认 `frontend` job 的 `bun install --frozen-lockfile`、`bun run lint`、`bun run typecheck`、`bun run build` 均为普通 step，不被条件表达式或 `continue-on-error` 包裹
    - 确认 `test` job 的 `uv sync --frozen` 与 `uv run pytest -m "not benchmark"` 均为普通 step，不被条件表达式或 `continue-on-error` 包裹
    - 确认新增前端 job 不覆盖或删除原有 `pull_request` 触发器
    - _需求: 2.7, 2.8, 2.9, 3.5, 3.6_

- [x] 3. 同步开发文档中的本地前端验证命令
  - [x] 3.1 在开发指南前端命令块中补充类型检查命令
    - 修改 `docs/development.md`
    - 在“前端命令在 `epsilon-client/` 下执行”对应的 bash 命令块中新增一行：`bun run typecheck  # TypeScript 类型检查`
    - 保留已有 `bun install`、`bun run dev`、`bun run build`、`bun run start`、`bun run lint` 命令
    - 不把前端命令改写为 npm、yarn 或 pnpm
    - _需求: 4.1, 4.2, 5.1_
  - [x] 3.2 保留 Next/Turbopack 受限沙箱说明
    - 修改 `docs/development.md`
    - 保留现有关于 `Next/Turbopack build 在受限沙箱中可能因 helper 进程本地端口绑定失败，需要在具备本地端口权限的环境中重跑` 的说明
    - 如需调整文字，只能澄清本地验证失败的环境限制；不得建议在 CI 中跳过、降级或禁用 `bun run build`
    - 保留 Bun 作为本特性要求的前端 CI 与本地验证路径
    - _需求: 4.3, 4.4, 5.3_
  - [x] 3.3 验证开发文档与 CI 命令一致
    - 检查 `docs/development.md` 的前端命令块包含 `bun run lint`、`bun run typecheck  # TypeScript 类型检查`、`bun run build`
    - 检查 `docs/development.md` 仍包含受限沙箱构建限制说明
    - 对照 `./.github/workflows/ci.yml`，确认文档列出的前端验证命令与 CI 中的 `bun run lint`、`bun run typecheck`、`bun run build` 一致
    - _需求: 4.1, 4.2, 4.3, 4.4_

- [x] 4. 执行本地前端质量门禁验证
  - [x] 4.1 运行前端 lint、typecheck 和 build
    - 在 `epsilon-client/` 下依次执行 `bun run lint`、`bun run typecheck`、`bun run build`
    - 三条命令全部成功时，记录本地前端验证通过
    - 若 `bun run build` 在受限沙箱中因 Next/Turbopack helper 进程本地端口绑定权限不足失败，记录该环境限制，并要求在具备本地端口权限的开发机或 GitHub Actions CI 中重跑
    - 不因受限沙箱失败而改写 `next build`、禁用 Turbopack、删除 `bun run build` 或弱化 CI 门禁
    - _需求: 1.2, 2.4, 2.5, 2.6, 4.2, 4.3, 4.4_
  - [x] 4.2 验证冻结安装和 lockfile 约束未被破坏
    - 检查本地验证后 `epsilon-client/bun.lock` 没有变化
    - 检查没有新增或修改后端 `uv.lock`、`pyproject.toml`、前端依赖声明或其他 lockfile
    - 检查 CI 中前端依赖安装命令为 `bun install --frozen-lockfile`，后端依赖同步命令为 `uv sync --frozen`
    - _需求: 2.3, 3.2, 5.1, 5.2_

- [x] 5. 最终范围与集成检查点
  - [x] 5.1 执行最终 diff 范围验证
    - 执行 `git diff -- .github/workflows/ci.yml epsilon-client/package.json docs/development.md` 查看允许范围内的全部变更
    - 执行 `git diff --name-only`，确认修改文件集合仅包含 `.github/workflows/ci.yml`、`epsilon-client/package.json`、`docs/development.md`
    - 确认没有修改后端源码、后端测试、前端源码、依赖 lockfile、`epsilon-boot/config.properties`、`.env`、`next.config.ts` 或 `epsilon-boot/migrations/`
    - _需求: 5.1, 5.2, 5.3_
  - [x] 5.2 执行分层、配置源和包管理边界检查
    - 确认本特性没有新增或修改 `epsilon-boot/src/`、`epsilon-boot/test/`、`epsilon-client/src/` 下的业务代码或测试代码
    - 确认没有改变后端 DDD 分层依赖方向，且没有新增 Port、Adapter、DI 绑定或 FastAPI router
    - 确认没有修改 `epsilon-boot/config.properties`、`.env` 或任何运行时配置文件
    - 确认后端相关命令仍仅使用 `uv`，前端相关命令使用 Bun
    - _需求: 3.1, 3.2, 3.3, 3.4, 5.1, 5.2, 5.3_
  - [x] 5.3 在 GitHub Actions 中观察 PR / push 集成结果
    - 在 push 或 pull request 上观察 `ci` workflow
    - 确认后端 `test` job 的 `ubuntu-latest` 与 `windows-latest` 矩阵均执行 `uv sync --frozen` 和 `uv run pytest -m "not benchmark"`
    - 确认前端 `frontend` job 在 `ubuntu-latest` 上依次执行 `bun install --frozen-lockfile`、`bun run lint`、`bun run typecheck`、`bun run build`
    - 确认任一前端或后端门禁失败时 workflow 为失败状态，全部通过时 workflow 为成功状态
    - _需求: 2.7, 2.8, 2.9, 3.5, 3.6_

## 备注

- 本计划没有 DDL 前置任务，也没有数据回填收尾任务；`epsilon-boot/migrations/` 是仓库当前数据库迁移 SQL 目录，但本特性不得新增迁移脚本。
- 本计划不新增后端 ruff、DDD import guard、评估回归 CI、前端 runtime schema validation、Vitest、Playwright、Provider 健康策略、SLO、告警或运维手册能力。
- 受限沙箱中的 Next/Turbopack build 失败只能作为环境限制记录，不能作为弱化 `bun run build` CI 门禁的理由。
- 如果实现过程中发现必须修改上述三个允许文件以外的文件，应停止实现并先回到需求 / 设计阶段确认范围变更。
