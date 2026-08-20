# TypeScript 严格模式规范

前端 `epsilon-client` 为 Next.js + TypeScript 项目，`tsconfig.json` 已启用 `strict: true`。所有前端代码必须在严格模式下通过类型检查与 ESLint。

## 类型安全

- 保持 `tsconfig.json` 的 `strict: true`，禁止关闭或降级严格选项
- 禁止使用 `any`；类型不确定时优先使用 `unknown` 并做收窄（narrowing）
- 禁止使用 `@ts-ignore`；确需忽略时使用 `@ts-expect-error` 并在同行注明原因
- 避免非空断言 `!` 滥用，优先通过类型守卫或显式判空处理

## 类型定义

- 后端 API 的请求/响应类型必须集中定义（如 `types/` 或对应 API 模块），禁止在调用处内联复杂类型
- API 类型应与后端 Pydantic 模型保持字段对齐，接口变更时同步更新
- 对象结构优先用 `interface`，联合/交叉/工具类型用 `type`
- 禁止导出未使用的类型；禁止 `enum` 之外的魔法字符串散落，抽为常量或联合字面量类型

## Lint 与格式化

- 统一遵循项目 `eslint.config.mjs` 的规则集，提交前必须通过 `lint`
- 禁止未使用的变量与 import；import 顺序遵循 lint 规则
- 禁止 `console.log` 入库，调试日志在提交前清理或改用受控 logger

## React / Next.js 约定

- 组件一律使用函数组件 + Hooks，遵守 Hooks 调用规则（不得在条件/循环中调用）
- 明确区分 Server Component 与 Client Component，仅在需要交互/浏览器 API 时使用 `"use client"`
- 禁止在渲染过程中执行副作用，副作用集中在 `useEffect` 或事件处理器中
- 流式（SSE）聊天等异步状态需有明确的加载/错误/完成态处理

## 常用命令

- 类型检查：`npx tsc --noEmit`
- Lint：`npm run lint`（命令须在 `epsilon-client/` 目录下执行）
