# FastAPI API Adapter 目录迁移 Summary

## 完成内容

- 已新增 `application/api/`，作为 FastAPI HTTP adapter 主实现目录，与 `application/cli/` 同级。
- 已迁移以下主实现到 `application/api/`：
  - `server_app.py`
  - `server_config.py`
  - `exception_handlers.py`
  - `routers/`
  - `middlewares/`
- 已将旧路径改为兼容 re-export 层：
  - `application.server_app`
  - `application.server_config`
  - `application.exception_handlers`
  - `application.routers.*`
  - `application.middlewares.*`
- 已更新 `epsilon serve`，新入口为 `application.api.server_app:app`。
- 已新增 `test/application/test_api_adapter_layout.py`，覆盖新旧 app、router、middleware 导入兼容性。

## 验证

- `env PYTHONPATH=src uv run --frozen pytest -q test/application/test_api_adapter_layout.py test/application/routers test/application/cli`：23 passed。
- `uv run --frozen epsilon --help`：通过。

## 残余风险

- 文档中仍保留少量历史 spec 对 `application.server_app:app` 的引用；旧路径兼容层仍支持该入口，本期未批量重写历史 spec。
