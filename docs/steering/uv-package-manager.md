# UV 包管理工具规范

本项目使用 [UV](https://docs.astral.sh/uv/) 作为 Python 项目管理和依赖管理工具。

## 核心规则

- 禁止使用 `pip`、`pip install`、`python -m pip` 等命令管理依赖
- 禁止使用 `poetry`、`pipenv`、`conda` 等其他包管理工具
- 所有依赖安装、移除、更新操作必须通过 `uv` 命令完成

## 常用命令

- 添加依赖：`uv add <package>`
- 移除依赖：`uv remove <package>`
- 同步依赖（根据 lockfile 安装）：`uv sync`
- 运行命令：`uv run <command>`
- 查看已安装包：`uv pip list`（仅查看，不用于安装）

## 项目结构

- 依赖声明在 `pyproject.toml` 的 `[project.dependencies]` 中
- 锁文件为 `uv.lock`，提交到版本控制
- 虚拟环境位于 `.venv/`

## 工作目录

- UV 命令需要在 `epsilon-boot/` 目录下执行（即 `pyproject.toml` 所在目录）
