"""``_validate_local_persistence_root`` 启动期校验集成测试。

覆盖需求 5.4-5.10：空路径、不存在 + ``create_if_missing=false``、指向文件、
workspace 冲突（同路径 / 父子包含）均以 ``ConfigurationError`` 拒绝启动。

使用直接构造的 ``LocalPersistenceConfig`` 实例 + monkeypatch workspace 配置，
不启动 FastAPI、不修改全局容器。
"""

import importlib.util
import pathlib
from dataclasses import dataclass

import pytest

from common.configuration import ConfigurationError


def _load_container_config_module():
    """直接加载 ``container_config`` 模块，绕过 ``application`` 包的 ``__init__``。"""
    config_path = (
        pathlib.Path(__file__).resolve().parents[2] / "src" / "application" / "container_config.py"
    )
    spec = importlib.util.spec_from_file_location(
        "test_startup_validation_module", str(config_path)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_config_module = _load_container_config_module()


@dataclass
class _FakeConfig:
    """最小化的 ``LocalPersistenceConfig`` 替代品，便于各测试用例传入不同值。"""

    root: str
    create_if_missing: bool = True


@dataclass
class _FakeWsConfig:
    """最小化的 workspace 配置替代品。"""

    root: str


# ── 需求 5.4：空路径 ──


def test_empty_root_rejected():
    """``LOCAL_PERSISTENCE_ROOT`` 为空触发 ``ConfigurationError``。"""
    cfg = _FakeConfig(root="")
    with pytest.raises(ConfigurationError, match="LOCAL_PERSISTENCE_ROOT 为空"):
        _config_module._validate_local_persistence_root(cfg)


def test_whitespace_root_rejected():
    """仅含空白字符的 ``LOCAL_PERSISTENCE_ROOT`` 也触发拒绝。"""
    cfg = _FakeConfig(root="   ")
    with pytest.raises(ConfigurationError, match="LOCAL_PERSISTENCE_ROOT 为空"):
        _config_module._validate_local_persistence_root(cfg)


# ── 需求 5.5：不存在 + CREATE_IF_MISSING=false ──


def test_missing_and_no_create_rejected(tmp_path: pathlib.Path):
    """路径不存在且 ``create_if_missing=False`` 触发拒绝。"""
    missing = tmp_path / "absent"
    cfg = _FakeConfig(root=str(missing), create_if_missing=False)
    with pytest.raises(ConfigurationError, match="指向的目录不存在"):
        _config_module._validate_local_persistence_root(cfg)


def test_missing_but_create_succeeds(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    """路径不存在且 ``create_if_missing=True`` 自动创建后返回绝对路径。"""
    monkeypatch.setattr(_config_module, "workspace_config", _FakeWsConfig(root=""))
    missing = tmp_path / "will-be-created"
    cfg = _FakeConfig(root=str(missing), create_if_missing=True)
    resolved = _config_module._validate_local_persistence_root(cfg)
    assert resolved.is_dir()
    assert resolved == missing.resolve()


# ── 需求 5.7：指向文件（非目录） ──


def test_pointing_to_file_rejected(tmp_path: pathlib.Path):
    """路径指向文件（非目录）触发拒绝。"""
    file_path = tmp_path / "not-a-dir.txt"
    file_path.write_bytes(b"x")
    cfg = _FakeConfig(root=str(file_path))
    with pytest.raises(ConfigurationError, match="不是目录"):
        _config_module._validate_local_persistence_root(cfg)


# ── 需求 5.10：与 WORKSPACE_ROOT 冲突 ──


def test_conflict_when_equal_to_workspace_root(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    """``LOCAL_PERSISTENCE_ROOT == WORKSPACE_ROOT`` 触发拒绝。"""
    monkeypatch.setattr(_config_module, "workspace_config", _FakeWsConfig(root=str(tmp_path)))
    cfg = _FakeConfig(root=str(tmp_path))
    with pytest.raises(ConfigurationError, match="共用或相互包含"):
        _config_module._validate_local_persistence_root(cfg)


def test_conflict_when_inside_workspace_root(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    """``LOCAL_PERSISTENCE_ROOT`` 位于 ``WORKSPACE_ROOT`` 之下触发拒绝。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(_config_module, "workspace_config", _FakeWsConfig(root=str(workspace)))
    inside = workspace / "local_persistence"
    cfg = _FakeConfig(root=str(inside), create_if_missing=True)
    with pytest.raises(ConfigurationError, match="共用或相互包含"):
        _config_module._validate_local_persistence_root(cfg)
    assert not inside.exists()


def test_conflict_when_workspace_inside_lp(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    """``WORKSPACE_ROOT`` 位于 ``LOCAL_PERSISTENCE_ROOT`` 之下也触发拒绝。"""
    lp_root = tmp_path / "lp"
    lp_root.mkdir()
    workspace = lp_root / "sub"
    workspace.mkdir()
    monkeypatch.setattr(_config_module, "workspace_config", _FakeWsConfig(root=str(workspace)))
    cfg = _FakeConfig(root=str(lp_root))
    with pytest.raises(ConfigurationError, match="共用或相互包含"):
        _config_module._validate_local_persistence_root(cfg)


def test_disjoint_workspace_not_conflict(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    """``WORKSPACE_ROOT`` 与 ``LOCAL_PERSISTENCE_ROOT`` 同层不相关 → 通过。"""
    workspace = tmp_path / "workspace"
    lp_root = tmp_path / "lp"
    workspace.mkdir()
    lp_root.mkdir()
    monkeypatch.setattr(_config_module, "workspace_config", _FakeWsConfig(root=str(workspace)))
    cfg = _FakeConfig(root=str(lp_root))
    resolved = _config_module._validate_local_persistence_root(cfg)
    assert resolved == lp_root.resolve()


def test_empty_workspace_uses_cwd_for_conflict_check(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    """``WORKSPACE_ROOT`` 为空串时按进程 cwd 参与冲突校验。"""
    monkeypatch.setattr(_config_module, "workspace_config", _FakeWsConfig(root=""))
    monkeypatch.chdir(tmp_path)
    cfg = _FakeConfig(root=str(tmp_path))
    with pytest.raises(ConfigurationError, match="共用或相互包含"):
        _config_module._validate_local_persistence_root(cfg)


def test_empty_workspace_allows_disjoint_local_persistence(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    """默认 cwd workspace 与同层本地持久化目录不冲突。"""
    workspace = tmp_path / "workspace"
    lp_root = tmp_path / "lp"
    workspace.mkdir()
    lp_root.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(_config_module, "workspace_config", _FakeWsConfig(root=""))
    cfg = _FakeConfig(root=str(lp_root))
    resolved = _config_module._validate_local_persistence_root(cfg)
    assert resolved == lp_root.resolve()


def test_relative_workspace_skips_conflict_check(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    """``WORKSPACE_ROOT`` 是相对路径时跳过冲突校验（按 design.md 约定）。"""
    monkeypatch.setattr(_config_module, "workspace_config", _FakeWsConfig(root="./relative"))
    cfg = _FakeConfig(root=str(tmp_path))
    resolved = _config_module._validate_local_persistence_root(cfg)
    assert resolved == tmp_path.resolve()
