# 实施计划：npm 到 bun 迁移

## 概述

将前端项目 `epsilon-client/` 的包管理器从 npm 迁移到 bun。按照设计文档的迁移步骤顺序执行：删除旧锁文件 → 生成新锁文件 → 更新文档 → 更新 .gitignore → 属性测试 → 清理。属性测试使用后端已有的 hypothesis 框架，放在 `epsilon-boot/test/integration/` 目录下。

## Tasks

- [x] 1. 替换锁文件并安装依赖
  - [x] 1.1 删除 `epsilon-client/package-lock.json`
    - 删除 npm 锁文件
    - _需求：1.2, 5.1_
  - [x] 1.2 在 `epsilon-client/` 目录下执行 `bun install` 生成 `bun.lock`
    - 运行 `bun install`，确认 `bun.lock` 文件生成且 `node_modules/` 正常安装
    - 确认 `package.json` 内容未被修改
    - _需求：1.1, 1.3, 4.1_

- [x] 2. 更新根目录 AGENTS.md 文档
  - [x] 2.1 将 AGENTS.md 中前端命令从 npm 替换为 bun
    - 将 `cd epsilon-client && npm install` 替换为 `cd epsilon-client && bun install`
    - 将 `cd epsilon-client && npm run dev` 替换为 `cd epsilon-client && bun run dev`
    - 将 `cd epsilon-client && npm run lint` 替换为 `cd epsilon-client && bun run lint`
    - 确保后端（uv）相关命令保持不变
    - _需求：2.1, 2.2, 2.3, 2.4_

- [x] 3. 更新 .gitignore 规则
  - [x] 3.1 在 `epsilon-client/.gitignore` 中添加 bun 调试日志忽略规则
    - 在 debug 区域添加 `bun-debug.log*`
    - 保留现有的 `npm-debug.log*` 作为历史兼容项
    - 确认 `bun.lock` 不在忽略列表中
    - _需求：3.1, 3.2, 3.3_

- [x] 4. 检查点 - 验证基本迁移完成
  - 确认 `bun.lock` 存在、`package-lock.json` 已删除、AGENTS.md 已更新、.gitignore 已更新
  - Ensure all changes are correct, ask the user if questions arise.

- [x] 5. 属性测试
  - [x] 5.1 编写属性测试：package.json 内容不变性
    - 在 `epsilon-boot/test/integration/` 下创建 `test_npm_to_bun_migration_property.py`
    - **属性 1：package.json 内容不变性**
    - 使用 hypothesis 生成随机合法的 package.json 内容，验证 bun install 不修改 package.json
    - 配置 `@settings(max_examples=100)`
    - 标签：Feature: npm-to-bun-migration, Property 1: package.json 内容不变性
    - **验证需求：1.3**
  - [x] 5.2 编写属性测试：后端命令不变性
    - 在同一文件 `test_npm_to_bun_migration_property.py` 中添加
    - **属性 2：后端命令不变性**
    - 使用 hypothesis 生成包含随机后端命令（含 `uv` 的行）的 AGENTS.md 内容，执行前端命令替换后验证后端命令行不变
    - 配置 `@settings(max_examples=100)`
    - 标签：Feature: npm-to-bun-migration, Property 2: 后端命令不变性
    - **验证需求：2.4**

- [x] 6. 清理 npm 遗留产物
  - [x] 6.1 确认并删除 `.npmrc` 文件（如果存在）
    - 检查 `epsilon-client/.npmrc` 是否存在，如存在则删除
    - _需求：5.2_

- [x] 7. 最终检查点 - 确认迁移完成
  - 确认所有文件变更正确，属性测试通过（如已编写）
  - 提醒用户手动验证：`bun run build`、`bun run lint`、`bun run dev`
  - Ensure all tests pass, ask the user if questions arise.

## 备注

- 标记 `*` 的子任务为可选项，可跳过以加速 MVP
- 需求 4（Next.js 兼容性验证）需要用户手动执行 `bun run build`、`bun run lint`、`bun run dev` 验证
- 需求 5.3（误用 npm 的兼容性）通过文档提醒实现，无需额外代码
- 属性测试使用后端已有的 hypothesis 框架，遵循 `*_property.py` 命名规范
