# FastAPI API Adapter 目录迁移需求

## 背景

项目已经新增 `application/cli/` 作为 TUI/CLI adapter。原 FastAPI adapter 仍散落在 `application/server_app.py`、`application/routers/`、`application/middlewares/` 等顶层路径下，和 CLI adapter 的同级关系不够清晰。

本期按用户确认的推荐方案迁移：新增 `application/api/`，将 FastAPI 相关代码收拢到该目录，并保留旧路径兼容层。

## 需求

### 1. 新目录边界

- 新增 `epsilon-boot/src/application/api/`。
- FastAPI app 创建、server config、exception handlers、routers、middlewares 均迁移到 `application/api/` 下。
- `application/api/` 与 `application/cli/` 同级，二者都是 application 层 adapter。

### 2. 行为不变

- HTTP 路由路径、响应结构、异常处理、中间件行为不得改变。
- FastAPI app 仍复用 `configure_container()` 与 `container.lifespan`。
- API adapter 不得引入对 CLI adapter 的依赖。

### 3. 兼容旧导入

- 保留旧路径兼容：
  - `application.server_app`
  - `application.server_config`
  - `application.exception_handlers`
  - `application.routers.*`
  - `application.middlewares.*`
- 旧路径模块只做 re-export，不再承载主实现。

### 4. CLI serve 更新

- `epsilon serve` 改为启动 `application.api.server_app:app`。

### 5. 验证

- 新增或更新测试，确保新旧 app 导入路径都可用。
- 运行 API router 相关测试与 CLI 相关测试。
