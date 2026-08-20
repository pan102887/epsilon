"""``FilesystemPromptRegistryAdapter`` 启动期校验与运行期查询单元测试。

每条错误分支通过 ``tmp_path`` 构造隔离的 Prompt 资产根目录，再以独立
:class:`PromptVersionConfig` 实例驱动 :class:`FilesystemPromptRegistryAdapter`
构造，避免污染真实 ``epsilon-boot/prompts/`` 资产（设计 §测试策略）。

覆盖的需求条款：

- 9.1：目录缺失 → :class:`PromptAssetDirectoryMissingError`；
- 9.2：目标 ``<name>/<version>.md`` 缺失 → :class:`PromptAssetFileMissingError`；
- 9.3：UTF-8 解码失败 → :class:`PromptAssetEncodingError`；
- 9.4：内容全空白 → :class:`EmptyPromptAssetError`；
- 9.5：未被配置引用的子目录 → 记录 ``已跳过加载`` 审计日志，不抛错；
- 9.6：配置引用但目录缺失 → :class:`PromptNotConfiguredError`；
- 3.3 / 3.4 / 3.5：成功路径下 :meth:`get` 返回 :class:`LoadedPrompt`
  且未命中时抛领域异常 :class:`PromptNotFoundError`。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from domain.prompt.exceptions import PromptNotFoundError
from domain.prompt.value_objects import LoadedPrompt
from infrastructure.prompt.exceptions import (
    EmptyPromptAssetError,
    PromptAssetDirectoryMissingError,
    PromptAssetEncodingError,
    PromptAssetFileMissingError,
    PromptNotConfiguredError,
)
from infrastructure.prompt.filesystem_prompt_registry_adapter import (
    FilesystemPromptRegistryAdapter,
)
from infrastructure.prompt.prompt_version_config import PromptVersionConfig


def _make_version_config(
    monkeypatch: pytest.MonkeyPatch,
    *,
    chat_default: str = "v1",
    task_template: str = "v1",
    context_summary: str = "v1",
) -> PromptVersionConfig:
    """构造隔离的 :class:`PromptVersionConfig` 实例（通过 env 注入）。

    通过 ``monkeypatch.setenv`` 注入 ``PROMPT_CHAT_DEFAULT_VERSION`` /
    ``PROMPT_TASK_TEMPLATE_VERSION`` 后立即构造实例；不污染模块级
    ``prompt_version_config`` 单例。
    """
    monkeypatch.setenv("PROMPT_CHAT_DEFAULT_VERSION", chat_default)
    monkeypatch.setenv("PROMPT_TASK_TEMPLATE_VERSION", task_template)
    monkeypatch.setenv("PROMPT_CONTEXT_SUMMARY_VERSION", context_summary)
    return PromptVersionConfig()


def _write_required_assets(root: Path) -> None:
    """写入本测试默认要求存在的 Prompt 资产。"""
    _write_asset(root, "chat-default", "v1", "chat default content")
    _write_asset(root, "task-template", "v1", "task template content")
    _write_asset(root, "context-summary", "v1", "context summary content")


def _write_asset(root: Path, name: str, version: str, content: str) -> Path:
    """在 ``root/<name>/<version>.md`` 写入 ``content`` 并返回路径。"""
    subdir = root / name
    subdir.mkdir(parents=True, exist_ok=True)
    path = subdir / f"{version}.md"
    path.write_text(content, encoding="utf-8")
    return path


# Validates: Requirement 9.1
def test_missing_root_directory_raises_directory_missing_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``root`` 不存在 → 抛 :class:`PromptAssetDirectoryMissingError`。"""
    missing_root = tmp_path / "prompts"  # 未创建
    version_config = _make_version_config(monkeypatch)

    with pytest.raises(PromptAssetDirectoryMissingError) as exc_info:
        FilesystemPromptRegistryAdapter(root=missing_root, version_config=version_config)

    assert str(missing_root) in str(exc_info.value)


# Validates: Requirement 9.1
def test_root_is_not_directory_raises_directory_missing_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``root`` 存在但是普通文件 → 抛 :class:`PromptAssetDirectoryMissingError`。"""
    file_as_root = tmp_path / "not_a_dir"
    file_as_root.write_text("not a directory", encoding="utf-8")
    version_config = _make_version_config(monkeypatch)

    with pytest.raises(PromptAssetDirectoryMissingError) as exc_info:
        FilesystemPromptRegistryAdapter(root=file_as_root, version_config=version_config)

    assert str(file_as_root) in str(exc_info.value)


# Validates: Requirement 9.2
def test_missing_version_file_raises_file_missing_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``<name>/<version>.md`` 缺失 → 抛 :class:`PromptAssetFileMissingError`。

    错误消息应包含文件绝对路径与对应 ``PROMPT_<NAME>_VERSION`` 键名。
    """
    root = tmp_path / "prompts"
    # 两个子目录都存在，但 chat-default 下缺少 v1.md
    (root / "chat-default").mkdir(parents=True)
    _write_asset(root, "task-template", "v1", "task template content")
    _write_asset(root, "context-summary", "v1", "context summary content")
    version_config = _make_version_config(monkeypatch)

    with pytest.raises(PromptAssetFileMissingError) as exc_info:
        FilesystemPromptRegistryAdapter(root=root, version_config=version_config)

    message = str(exc_info.value)
    expected_path = (root / "chat-default" / "v1.md").resolve()
    assert str(expected_path) in message
    assert "PROMPT_CHAT_DEFAULT_VERSION" in message


# Validates: Requirement 9.3
def test_non_utf8_file_raises_encoding_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """含 0xFF / 0xFE 等非法 UTF-8 字节 → 抛 :class:`PromptAssetEncodingError`。"""
    root = tmp_path / "prompts"
    (root / "chat-default").mkdir(parents=True)
    # 0xFF / 0xFE / 0xFC 不在 UTF-8 合法起始字节集合内，触发 UnicodeDecodeError。
    (root / "chat-default" / "v1.md").write_bytes(b"\xff\xfe\xfc")
    _write_asset(root, "task-template", "v1", "task template content")
    _write_asset(root, "context-summary", "v1", "context summary content")
    version_config = _make_version_config(monkeypatch)

    with pytest.raises(PromptAssetEncodingError) as exc_info:
        FilesystemPromptRegistryAdapter(root=root, version_config=version_config)

    assert "chat-default" in str(exc_info.value)


# Validates: Requirement 9.4
def test_blank_content_raises_empty_asset_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """仅空白字符的内容 → 抛 :class:`EmptyPromptAssetError`。"""
    root = tmp_path / "prompts"
    _write_asset(root, "chat-default", "v1", "   \n\t  \n")
    _write_asset(root, "task-template", "v1", "task template content")
    _write_asset(root, "context-summary", "v1", "context summary content")
    version_config = _make_version_config(monkeypatch)

    with pytest.raises(EmptyPromptAssetError) as exc_info:
        FilesystemPromptRegistryAdapter(root=root, version_config=version_config)

    assert "chat-default" in str(exc_info.value)


# Validates: Requirement 9.6
def test_config_references_missing_subdir_raises_not_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """配置引用 ``chat-default`` 但目录下无该子目录 → 抛 :class:`PromptNotConfiguredError`。"""
    root = tmp_path / "prompts"
    # 只建 task-template，不建 chat-default
    _write_asset(root, "task-template", "v1", "task template content")
    _write_asset(root, "context-summary", "v1", "context summary content")
    version_config = _make_version_config(monkeypatch)

    with pytest.raises(PromptNotConfiguredError) as exc_info:
        FilesystemPromptRegistryAdapter(root=root, version_config=version_config)

    message = str(exc_info.value)
    assert "chat-default" in message
    assert str((root / "chat-default").resolve()) in message


# Validates: Requirement 9.5
def test_unconfigured_subdir_is_skipped_with_audit_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """目录下存在未被配置引用的子目录 → 不抛错，仅记录 INFO 审计日志。"""
    root = tmp_path / "prompts"
    _write_required_assets(root)
    # 额外未配置的 unused/
    (root / "unused").mkdir()
    version_config = _make_version_config(monkeypatch)

    with caplog.at_level(
        logging.INFO,
        logger="infrastructure.prompt.filesystem_prompt_registry_adapter",
    ):
        adapter = FilesystemPromptRegistryAdapter(root=root, version_config=version_config)

    assert isinstance(adapter, FilesystemPromptRegistryAdapter)
    assert any(
        "已跳过加载" in record.getMessage() and "unused" in record.getMessage()
        for record in caplog.records
    ), f"期望在启动日志中出现 '已跳过加载 ... unused' 条目；实际：{caplog.text}"


# Validates: Requirement 3.3, 3.4 (happy path + zero I/O on get)
def test_happy_path_loads_prompts_and_get_returns_expected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """正常路径：两份资产均存在 → 适配器成功加载；:meth:`get` 返回预期。"""
    root = tmp_path / "prompts"
    _write_required_assets(root)
    version_config = _make_version_config(monkeypatch)

    adapter = FilesystemPromptRegistryAdapter(root=root, version_config=version_config)

    lp = adapter.get("chat-default")
    assert isinstance(lp, LoadedPrompt)
    assert lp.prompt_id == "chat-default@v1"
    assert lp.name == "chat-default"
    assert lp.version == "v1"
    assert lp.content == "chat default content"

    names = adapter.list_names()
    assert "chat-default" in names
    assert "task-template" in names
    assert "context-summary" in names


def test_context_summary_prompt_v1_can_be_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``context-summary@v1`` 可通过 PromptRegistry 加载。"""
    root = tmp_path / "prompts"
    _write_required_assets(root)
    version_config = _make_version_config(monkeypatch)
    adapter = FilesystemPromptRegistryAdapter(root=root, version_config=version_config)

    lp = adapter.get("context-summary")

    assert lp.prompt_id == "context-summary@v1"
    assert lp.name == "context-summary"
    assert lp.version == "v1"
    assert lp.content == "context summary content"


# Validates: Requirement 3.5
def test_get_unknown_name_raises_prompt_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """运行期 :meth:`get` 被传入未注册名称 → 抛领域异常 :class:`PromptNotFoundError`。"""
    root = tmp_path / "prompts"
    _write_required_assets(root)
    version_config = _make_version_config(monkeypatch)
    adapter = FilesystemPromptRegistryAdapter(root=root, version_config=version_config)

    with pytest.raises(PromptNotFoundError) as exc_info:
        adapter.get("unknown")

    assert exc_info.value.name == "unknown"
    assert "chat-default" in exc_info.value.registered
    assert "task-template" in exc_info.value.registered
    assert "context-summary" in exc_info.value.registered


# Validates: Requirement 9.2 (version override points to missing file)
def test_config_version_override_misses_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``PROMPT_CHAT_DEFAULT_VERSION=v3`` 但目录下仅 v1.md。
    应抛 :class:`PromptAssetFileMissingError`。
    """
    root = tmp_path / "prompts"
    _write_required_assets(root)
    version_config = _make_version_config(monkeypatch, chat_default="v3")

    with pytest.raises(PromptAssetFileMissingError) as exc_info:
        FilesystemPromptRegistryAdapter(root=root, version_config=version_config)

    message = str(exc_info.value)
    expected_path = (root / "chat-default" / "v3.md").resolve()
    assert str(expected_path) in message
    assert "PROMPT_CHAT_DEFAULT_VERSION" in message
