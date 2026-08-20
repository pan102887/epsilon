# FastAPI API Adapter 目录迁移设计

## 目标结构

```text
epsilon-boot/src/application/
  api/
    __init__.py
    server_app.py
    server_config.py
    exception_handlers.py
    routers/
    middlewares/
  cli/
  container_config.py
```

`application/api/` 是 FastAPI HTTP adapter 的主实现目录；`application/cli/` 是 TUI/CLI adapter。二者平级复用 `domain` Port 与 `container_config`。

## 迁移策略

先复制主实现到 `application/api/`，再将旧路径替换为兼容 re-export：

- `application/server_app.py` -> `from application.api.server_app import app`
- `application/server_config.py` -> re-export `ServerConfig`、`service_config`
- `application/exception_handlers.py` -> re-export `register_exception_handlers`
- `application/routers/*.py` -> re-export 对应新 router 模块
- `application/middlewares/*.py` -> re-export 对应新 middleware 模块

这样外部仍可使用 `application.server_app:app`，新入口使用 `application.api.server_app:app`。

## 关键调整

- `application/api/server_app.py` 的相对导入改为：
  - `from application.container_config import configure_container`
  - `from .middlewares import RequestLoggingMiddleware`
  - `from .routers import ...`
- `application/api/routers/test_router.py` 的 `BASE_DIR` 需要按新目录深度调整为项目根。
- `application/cli/main.py` 的 `serve` 命令改为 `application.api.server_app:app`。

## 风险控制

- 不删除旧模块，降低部署命令和测试引用迁移风险。
- 不改任何 HTTP path、请求体、响应体和异常处理逻辑。
- 验证新旧 app 导入身份一致，确保兼容层有效。
