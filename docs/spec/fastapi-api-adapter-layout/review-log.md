# FastAPI API Adapter 目录迁移 Review Log

## 2026-05-27

- Coordinator 自评：本期只做 application adapter 目录迁移，不改变 HTTP 行为，不触碰 domain/infrastructure。
- 验证通过：`env PYTHONPATH=src uv run --frozen pytest -q test/application/test_api_adapter_layout.py test/application/routers test/application/cli` 共 23 个测试通过。
- 验证通过：`uv run --frozen epsilon --help` 可正常显示 CLI 帮助。
