# 需求文档

## 简介

将前端项目 `epsilon-client` 的包管理器从 npm 迁移到 bun。迁移范围包括：替换锁文件、更新所有文档和脚本中的 npm 命令引用、确保 Next.js 16 与 bun 的兼容性，以及清理 npm 相关的遗留产物。

## 术语表

- **Bun**: 高性能 JavaScript 运行时和包管理器，兼容 npm 生态，使用 `bun.lock` 作为锁文件
- **Migration_Tool**: 本次迁移过程中涉及的所有脚本、命令和文件变更的统称
- **Frontend_Project**: 位于 `epsilon-client/` 目录下的 Next.js 16 前端应用
- **Root_AGENTS_MD**: 仓库根目录下的 `AGENTS.md` 文件，包含前端构建和开发命令说明
- **Frontend_Gitignore**: 位于 `epsilon-client/.gitignore` 的前端忽略规则文件

## 需求

### 需求 1：替换锁文件

**用户故事：** 作为开发者，我希望用 bun 的锁文件替换 npm 的锁文件，以便项目使用 bun 进行依赖管理。

#### 验收标准

1. WHEN 迁移完成后，THE Frontend_Project SHALL 包含由 `bun install` 生成的 `bun.lock` 锁文件
2. WHEN 迁移完成后，THE Frontend_Project SHALL 不再包含 `package-lock.json` 文件
3. THE Frontend_Project SHALL 保留现有的 `package.json` 文件内容不变（bun 复用 npm 的 package.json 格式）

### 需求 2：更新根目录文档中的命令引用

**用户故事：** 作为开发者，我希望仓库文档中的前端命令引用与实际使用的包管理器一致，以避免混淆。

#### 验收标准

1. WHEN 迁移完成后，THE Root_AGENTS_MD SHALL 将前端依赖安装命令从 `npm install` 更新为 `bun install`
2. WHEN 迁移完成后，THE Root_AGENTS_MD SHALL 将前端开发服务器启动命令从 `npm run dev` 更新为 `bun run dev`
3. WHEN 迁移完成后，THE Root_AGENTS_MD SHALL 将前端 lint 命令从 `npm run lint` 更新为 `bun run lint`
4. THE Root_AGENTS_MD SHALL 仅修改前端相关的命令引用，后端（uv）相关命令保持不变

### 需求 3：更新 .gitignore 规则

**用户故事：** 作为开发者，我希望版本控制的忽略规则覆盖 bun 相关的产物文件，以保持仓库整洁。

#### 验收标准

1. WHEN 迁移完成后，THE Frontend_Gitignore SHALL 包含 bun 的调试日志忽略规则（`bun-debug.log*`）
2. WHEN 迁移完成后，THE Frontend_Gitignore SHALL 移除或保留 npm 调试日志忽略规则（`npm-debug.log*`），作为历史兼容项可保留
3. THE Frontend_Gitignore SHALL 不忽略 `bun.lock` 文件（锁文件需要提交到版本控制）

### 需求 4：验证 Next.js 16 与 bun 的兼容性

**用户故事：** 作为开发者，我希望确认 Next.js 16 在 bun 包管理器下能正常构建和运行，以保证迁移不会破坏现有功能。

#### 验收标准

1. WHEN 使用 `bun install` 安装依赖后，THE Frontend_Project SHALL 成功安装所有 `package.json` 中声明的依赖项（包括 next 16.2.0、react 19.2.4、lightningcss、tailwindcss v4、typescript 5、eslint 9、babel-plugin-react-compiler）
2. WHEN 使用 `bun run build` 执行构建后，THE Frontend_Project SHALL 成功完成 Next.js 生产构建，无错误退出
3. WHEN 使用 `bun run lint` 执行代码检查后，THE Frontend_Project SHALL 成功运行 ESLint 检查
4. WHEN 使用 `bun run dev` 启动开发服务器后，THE Frontend_Project SHALL 成功启动 Next.js 开发服务器，且 `next.config.ts` 中的 rewrites 代理规则正常生效

### 需求 5：清理 npm 遗留产物

**用户故事：** 作为开发者，我希望移除 npm 特有的遗留文件，以避免团队成员误用 npm。

#### 验收标准

1. WHEN 迁移完成后，THE Frontend_Project SHALL 不包含 `package-lock.json` 文件
2. WHEN 迁移完成后，THE Frontend_Project SHALL 不包含 `.npmrc` 文件（如果存在的话）
3. IF 迁移后开发者误执行 `npm install`，THEN THE Frontend_Project SHALL 不会因此产生冲突（bun.lock 与 package-lock.json 共存不影响 bun 正常工作，但建议通过文档提醒团队使用 bun）
