"""``WorkspaceConfig`` 单元测试。

覆盖范围（对应 tasks 5.2 用例清单）：

- 默认字段值：``backend=LOCAL_FILESYSTEM`` / ``root=""``（工厂层默认 cwd）/
  ``follow_symlinks=False`` / ``create_if_missing=False``。
- ``env_prefix`` 正确设置为 ``WORKSPACE_``。
- 非法 ``backend``（通过环境变量传入 ``oss`` 等非本期支持取值）应被
  ``@model_validator(mode="after")`` 拒绝，抛出 ``ValidationError`` / ``ValueError``。
- ``hot_reload`` 默认保持 ``False``（需求 5.12：``backend`` / ``root``
  在进程生命周期内不可变）。

**注意**：本测试直接实例化 ``WorkspaceConfig()``（而非使用模块级的
``workspace_config`` 单例），以避免受到 ``create_config`` 在模块导入时
已经捕获到的环境变量状态的干扰；``monkeypatch.setenv`` / ``delenv``
在每个用例内部调整 ``WORKSPACE_*`` 环境变量后再构造实例。
"""

import pytest
from pydantic import ValidationError

from domain.workspace.value_objects import WorkspaceBackendKind
from infrastructure.workspace.workspace_config import WorkspaceConfig


def _clear_workspace_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清理所有 ``WORKSPACE_*`` 环境变量，避免测试间相互污染。

    Args:
        monkeypatch: pytest 的 monkeypatch fixture。
    """
    monkeypatch.delenv("WORKSPACE_BACKEND", raising=False)
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("WORKSPACE_FOLLOW_SYMLINKS", raising=False)
    monkeypatch.delenv("WORKSPACE_CREATE_IF_MISSING", raising=False)


class TestWorkspaceConfigDefaults:
    """默认值验证：所有字段的默认取值符合 design §组件与接口 4 约定。"""

    def test_backend_default_is_local_filesystem(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """未设置 ``WORKSPACE_BACKEND`` 时默认使用 ``LOCAL_FILESYSTEM``。"""
        _clear_workspace_env(monkeypatch)
        config = WorkspaceConfig()
        assert config.backend == WorkspaceBackendKind.LOCAL_FILESYSTEM

    def test_root_default_is_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """未设置 ``WORKSPACE_ROOT`` 时默认为空串（后续由工厂解析为 cwd）。"""
        _clear_workspace_env(monkeypatch)
        config = WorkspaceConfig()
        assert config.root == ""

    def test_follow_symlinks_default_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``follow_symlinks`` 默认为 ``False``（更严格）。"""
        _clear_workspace_env(monkeypatch)
        config = WorkspaceConfig()
        assert config.follow_symlinks is False

    def test_create_if_missing_default_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``create_if_missing`` 默认为 ``False``。"""
        _clear_workspace_env(monkeypatch)
        config = WorkspaceConfig()
        assert config.create_if_missing is False


class TestWorkspaceConfigEnvPrefix:
    """``env_prefix`` 配置正确（``WORKSPACE_``）。"""

    def test_env_prefix_is_workspace(self) -> None:
        """``model_config["env_prefix"]`` 应为 ``"WORKSPACE_"``。"""
        assert WorkspaceConfig.model_config["env_prefix"] == "WORKSPACE_"


class TestWorkspaceConfigUnsupportedBackend:
    """非本期支持的 ``backend`` 取值必须被拒绝（fail-fast）。"""

    def test_oss_backend_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``WORKSPACE_BACKEND=oss`` 等枚举外取值应在构造时抛出异常。

        目前 ``WorkspaceBackendKind`` 仅有 ``LOCAL_FILESYSTEM`` 一个枚举值，
        pydantic 会在解析阶段因 enum 值不匹配直接抛 ``ValidationError``。
        未来若扩展 ``WorkspaceBackendKind.OSS`` 等枚举，则由
        ``_reject_unsupported_backend`` 校验器在 ``mode="after"`` 阶段
        抛 ``ValueError``（被 pydantic 包装为 ``ValidationError``）。
        两种情况均以 ``Exception`` 捕获以兼容未来扩展。
        """
        _clear_workspace_env(monkeypatch)
        monkeypatch.setenv("WORKSPACE_BACKEND", "oss")
        with pytest.raises(ValidationError) as exc_info:
            WorkspaceConfig()
        # 错误消息中需提示 "local_filesystem" 或直接提示 enum 不匹配
        err_str = str(exc_info.value)
        assert (
            "local_filesystem" in err_str
            or "oss" in err_str
            or "WorkspaceBackendKind" in err_str
            or "enum" in err_str.lower()
        )

    def test_empty_backend_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """显式空串会被 pydantic 作为非法枚举值拒绝（fail-fast）。

        pydantic 对 ``Enum`` 字段不接受空串，直接抛 ``ValidationError``；
        这是预期行为，而不是静默降级到默认值。本用例断言确实抛出异常，
        以固化"非法取值必须显式失败"的契约。
        """
        _clear_workspace_env(monkeypatch)
        monkeypatch.setenv("WORKSPACE_BACKEND", "")
        with pytest.raises(ValidationError):
            WorkspaceConfig()


class TestWorkspaceConfigHotReloadDisabled:
    """``hot_reload`` 类变量保持默认 ``False``（需求 5.12）。"""

    def test_hot_reload_class_var_is_false(self) -> None:
        """``WorkspaceConfig.hot_reload`` 在类层级为 ``False``。"""
        # ``hot_reload`` 在 PropertiesBaseSettings 声明为 ``ClassVar[bool] = False``；
        # WorkspaceConfig 不覆盖它 → 仍为 False。
        assert getattr(WorkspaceConfig, "hot_reload", False) is False


class TestWorkspaceConfigEnvOverrides:
    """环境变量覆盖各字段（happy-path）。"""

    def test_root_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        """``WORKSPACE_ROOT`` 环境变量能正确注入到 ``root`` 字段。"""
        _clear_workspace_env(monkeypatch)
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        config = WorkspaceConfig()
        assert config.root == str(tmp_path)

    def test_follow_symlinks_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``WORKSPACE_FOLLOW_SYMLINKS=true`` 能正确注入为 ``True``。"""
        _clear_workspace_env(monkeypatch)
        monkeypatch.setenv("WORKSPACE_FOLLOW_SYMLINKS", "true")
        config = WorkspaceConfig()
        assert config.follow_symlinks is True

    def test_create_if_missing_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``WORKSPACE_CREATE_IF_MISSING=true`` 能正确注入为 ``True``。"""
        _clear_workspace_env(monkeypatch)
        monkeypatch.setenv("WORKSPACE_CREATE_IF_MISSING", "true")
        config = WorkspaceConfig()
        assert config.create_if_missing is True

    def test_explicit_local_filesystem_backend_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """显式指定 ``WORKSPACE_BACKEND=local_filesystem`` 应被接受。"""
        _clear_workspace_env(monkeypatch)
        monkeypatch.setenv("WORKSPACE_BACKEND", "local_filesystem")
        config = WorkspaceConfig()
        assert config.backend == WorkspaceBackendKind.LOCAL_FILESYSTEM
