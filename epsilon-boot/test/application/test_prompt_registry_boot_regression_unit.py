"""Prompt 注册表启动期端到端冒烟测试（启动期 fail-fast 与正向解析）。

# Validates: Property 1 / Requirement 9.1, 9.2, 9.6, 11.1

本测试模块覆盖以下三条启动期路径：

1. **正向**：``tmp_path/prompts/`` 下完整布置 ``chat-default/v1.md``、
   ``task-template/v1.md`` 与 ``context-summary/v1.md`` 后，``_create_prompt_registry()`` 返回的
   ``PromptRegistryPort`` 实例可解析 ``chat-default``，且 ``LoadedPrompt``
   字段与文件内容一一对应（需求 11.1 的 happy path）；
2. **负向 9.2**：删除 ``chat-default/v1.md`` 后，工厂构造必抛
   ``PromptAssetFileMissingError``，错误消息包含期望路径与配置键名；
3. **负向 9.6**：将 ``tmp_path/prompts`` 替换为空目录后，工厂构造必抛
   ``PromptNotConfiguredError``。

测试通过 ``importlib.util`` 直接加载 ``container_config`` 模块，避免
``application/__init__.py`` 的导入副作用污染全局容器；通过
``monkeypatch.setattr`` 覆盖 ``_PROMPT_ASSET_ROOT`` 与
``prompt_version_config``，使工厂指向用例隔离的 ``tmp_path``。
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

from infrastructure.prompt.exceptions import (
    PromptAssetFileMissingError,
    PromptNotConfiguredError,
)


def _load_container_config_module():
    """直接加载 ``container_config``，绕过 ``application`` 包的 ``__init__``。"""
    config_path = (
        pathlib.Path(__file__).resolve().parents[2] / "src" / "application" / "container_config.py"
    )
    spec = importlib.util.spec_from_file_location("test_prompt_boot_module", str(config_path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_config_module = _load_container_config_module()


def _seed_prompts(prompts_root: pathlib.Path) -> None:
    """在 ``prompts_root`` 下铺设全部已配置的合法 v1 资产文件。"""
    chat_dir = prompts_root / "chat-default"
    task_dir = prompts_root / "task-template"
    context_summary_dir = prompts_root / "context-summary"
    chat_dir.mkdir(parents=True)
    task_dir.mkdir(parents=True)
    context_summary_dir.mkdir(parents=True)
    (chat_dir / "v1.md").write_text("你是一个测试助手。", encoding="utf-8")
    (task_dir / "v1.md").write_text("任务骨架内容。", encoding="utf-8")
    (context_summary_dir / "v1.md").write_text("上下文摘要内容。", encoding="utf-8")


def _patch_prompt_root(
    monkeypatch: pytest.MonkeyPatch,
    prompts_root: pathlib.Path,
) -> None:
    """将 ``_PROMPT_ASSET_ROOT`` 指向 ``prompts_root``。

    同步重置 ``prompt_version_config`` 的版本字段为 ``v1``，避免环境变量
    覆盖污染本测试的预期版本。
    """
    monkeypatch.setattr(_config_module, "_PROMPT_ASSET_ROOT", prompts_root)

    from infrastructure.prompt import prompt_version_config as cfg_module

    fresh = cfg_module.PromptVersionConfig(
        chat_default_version="v1",
        task_template_version="v1",
        context_summary_version="v1",
    )
    monkeypatch.setattr(cfg_module, "prompt_version_config", fresh)


def test_create_prompt_registry_succeeds_with_seeded_assets(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正向：完整资产布局下 ``_create_prompt_registry`` 解析成功（需求 11.1）。"""
    prompts_root = tmp_path / "prompts"
    _seed_prompts(prompts_root)
    _patch_prompt_root(monkeypatch, prompts_root)

    registry = _config_module._create_prompt_registry()

    loaded = registry.get("chat-default")
    assert loaded.prompt_id == "chat-default@v1"
    assert loaded.name == "chat-default"
    assert loaded.version == "v1"
    assert loaded.content == "你是一个测试助手。"

    assert set(registry.list_names()) == {
        "chat-default",
        "task-template",
        "context-summary",
    }


def test_create_prompt_registry_raises_when_chat_default_file_missing(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """负向 9.2：``chat-default/v1.md`` 缺失时抛 ``PromptAssetFileMissingError``。"""
    prompts_root = tmp_path / "prompts"
    _seed_prompts(prompts_root)
    (prompts_root / "chat-default" / "v1.md").unlink()
    _patch_prompt_root(monkeypatch, prompts_root)

    with pytest.raises(PromptAssetFileMissingError) as exc_info:
        _config_module._create_prompt_registry()

    message = str(exc_info.value)
    assert "chat-default" in message
    assert "v1.md" in message


def test_create_prompt_registry_raises_when_root_is_empty_directory(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """负向 9.6：资产目录存在但配置引用的子目录全部缺失 → ``PromptNotConfiguredError``。"""
    prompts_root = tmp_path / "prompts"
    prompts_root.mkdir()
    _patch_prompt_root(monkeypatch, prompts_root)

    with pytest.raises(PromptNotConfiguredError):
        _config_module._create_prompt_registry()
