"""write_schema_meta 单元测试。

覆盖 meta.json 写入内容正确、重复调用幂等（版本一致时不重写），以及
mkdir / 写入抛错时 write_schema_meta 故障隔离不抛出（需求 6.3、Property 7）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from infrastructure.storage import schema_meta
from infrastructure.storage.schema_meta import SCHEMA_VERSION, write_schema_meta


def test_write_schema_meta_creates_meta_json(tmp_path: Path) -> None:
    """写入后 meta.json 存在且含 {"schema_version": SCHEMA_VERSION}。"""
    home = tmp_path / ".epsilon"
    write_schema_meta(home)

    meta_path = home / "meta.json"
    assert meta_path.exists()
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCHEMA_VERSION
    assert SCHEMA_VERSION == 1


def test_write_schema_meta_creates_missing_home(tmp_path: Path) -> None:
    """home 目录不存在时被幂等创建（mkdir parents=True）。"""
    home = tmp_path / "nested" / "deep" / ".epsilon"
    assert not home.exists()
    write_schema_meta(home)
    assert home.is_dir()
    assert (home / "meta.json").exists()


def test_write_schema_meta_is_idempotent_no_rewrite(tmp_path: Path) -> None:
    """版本一致时重复调用不重写文件（通过 mtime 断言未改动）。"""
    home = tmp_path / ".epsilon"
    write_schema_meta(home)
    meta_path = home / "meta.json"
    first_mtime = meta_path.stat().st_mtime_ns

    # 再次调用：版本一致应跳过重写，mtime 不变。
    write_schema_meta(home)
    assert meta_path.stat().st_mtime_ns == first_mtime


def test_write_schema_meta_rewrites_on_version_mismatch(tmp_path: Path) -> None:
    """已有 meta.json 版本不一致时重写为当前版本。"""
    home = tmp_path / ".epsilon"
    home.mkdir(parents=True)
    meta_path = home / "meta.json"
    meta_path.write_text(json.dumps({"schema_version": 0}), encoding="utf-8")

    write_schema_meta(home)
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCHEMA_VERSION


def test_write_schema_meta_isolates_mkdir_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mkdir 抛错时 write_schema_meta 不抛出（故障隔离，Property 7）。"""
    home = tmp_path / ".epsilon"

    def _raise_mkdir(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("mkdir denied")

    monkeypatch.setattr(Path, "mkdir", _raise_mkdir)
    # 不应抛出任何异常。
    write_schema_meta(home)
    # 目录未创建，meta.json 亦未写入。
    assert not (home / "meta.json").exists()


def test_write_schema_meta_isolates_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """write_text 抛错时 write_schema_meta 不抛出（故障隔离，Property 7）。"""
    home = tmp_path / ".epsilon"

    def _raise_write(*_args: object, **_kwargs: object) -> int:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "write_text", _raise_write)
    # 不应抛出任何异常。
    write_schema_meta(home)


def test_write_schema_meta_isolates_corrupt_existing_meta(tmp_path: Path) -> None:
    """已有 meta.json 内容损坏（非 JSON）时不抛出（故障隔离，Property 7）。"""
    home = tmp_path / ".epsilon"
    home.mkdir(parents=True)
    (home / "meta.json").write_text("not-json", encoding="utf-8")

    # json.loads 解析失败被隔离，不向调用方抛出。
    write_schema_meta(home)


def test_module_exposes_schema_version() -> None:
    """模块级 SCHEMA_VERSION 常量存在且为 1。"""
    assert schema_meta.SCHEMA_VERSION == 1
