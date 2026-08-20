# FastAPI API Adapter 目录迁移任务

- [x] 1. 创建 spec 工件并确认迁移范围。
- [x] 2. 新增 `application/api/` 主实现目录。
- [x] 3. 迁移 FastAPI app、config、exception handlers、routers、middlewares。
- [x] 4. 将旧路径替换为兼容 re-export 层。
- [x] 5. 更新 `epsilon serve` 指向 `application.api.server_app:app`。
- [x] 6. 增加新旧导入路径兼容测试。
- [x] 7. 运行验证命令并记录结果。
