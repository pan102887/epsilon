# CLAUDE.md — epsilon-boot

后端服务（FastAPI + DDD 六边形架构）。根目录 `../CLAUDE.md` 已被继承，本文件只写**子项目特有、且缺失会犯错**的约束。

## 命令（在本目录下执行）

```bash
uv run python main.py                    # 启动，默认 0.0.0.0:7777
PYTHONPATH=src uv run --frozen pytest    # 全量测试，必须带 PYTHONPATH=src
```

- 依赖管理仅用 `uv`，禁止 `pip`/`poetry`/`pipenv`/`conda`（见 @../docs/steering/uv-package-manager.md）。
- 源码在 `src/`，导入根为 `src`，故测试/脚本须 `PYTHONPATH=src`。

## 关键约束

- DDD 分层依赖方向、Port/Adapter 归属：改动前必读 @../docs/steering/ddd-architecture.md。
- 配置优先写 `config.properties`，`.env` 仅本地覆盖：@../docs/steering/config-source.md。
- 类型注解与 lint 基线（`ruff`/`pyright`、禁裸 `Any`）：@../docs/steering/python-typing-lint.md。

## 深入了解

架构、领域模型、DI、模型路由、工具等主题文档索引见 [../docs/](../docs/) 及 [README.md](README.md)。
