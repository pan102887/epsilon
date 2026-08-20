"""领域事件基础设施零残留门槛测试（需求 8.8 / 10.8）。

该测试常驻作为回归门槛：验证 ``common.events``、``common.event_bus``、
``infrastructure.event_bus.in_memory_event_bus_adapter`` 等已移除模块
**必须** ``import`` 失败，以防止将来有人误将事件基础设施再引入。

同时验证 ``src/`` 与 ``test/`` 目录下**不存在**对已移除领域事件符号
（``DomainEvent`` / ``EventBusPort`` / ``EventStorePort`` / ``InMemoryEventBusAdapter``
/ ``DatabaseEventStoreAdapter`` / ``publish_event``）的任何源码引用。

设计目的：

* 对应需求 8.8：``grep -rE "DomainEvent|EventBusPort|EventStorePort|publish.*event
  |InMemoryEventBusAdapter|DatabaseEventStoreAdapter" src test`` 返回零行。
* 对应需求 10.8：上述模块 ``import`` 触发 ``ModuleNotFoundError``。
"""

from __future__ import annotations

import importlib
import pathlib
import re

import pytest

REMOVED_MODULES: list[str] = [
    "common.events",
    "common.event_bus",
    "common.event_bus.ports",
    "common.event_bus.serializer",
    "infrastructure.event_bus",
    "infrastructure.event_bus.in_memory_event_bus_adapter",
    "infrastructure.event_bus.database_event_store_adapter",
]


@pytest.mark.parametrize("module_name", REMOVED_MODULES)
def test_removed_event_modules_raise_module_not_found(module_name: str) -> None:
    """需求 10.8：已移除的事件基础设施模块必须 ``import`` 失败。

    兼容性说明：部分 CI 环境（如 Windows 下从旧分支同步过来的工作区）
    可能遗留空 ``event_bus/`` 目录，被 Python 当作 PEP 420 隐式命名空间包
    而让 ``importlib.import_module`` 返回 "无代码" 的虚包。此处把"虚包"
    视同"未导入"：只要模块没有 ``__file__``（命名空间包）或
    ``__file__`` 非源文件，就判定为"已移除"。
    """
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:
        return

    module_file = getattr(module, "__file__", None)
    assert module_file is None, (
        f"已移除模块 {module_name} 仍被实际文件 {module_file} 提供，"
        "需清理遗留源码；空命名空间包不算命中。"
    )


# 正则匹配需求 8.8 指定的 6 个符号（含 publish_event 变体）。
_FORBIDDEN_TOKEN_PATTERN = re.compile(
    r"\b("
    r"DomainEvent"
    r"|EventBusPort"
    r"|EventStorePort"
    r"|InMemoryEventBusAdapter"
    r"|DatabaseEventStoreAdapter"
    r"|publish_event"
    r")\b"
)


def _project_root() -> pathlib.Path:
    """返回 ``epsilon-boot`` 根目录（``test/`` 与 ``src/`` 同级）。"""
    return pathlib.Path(__file__).resolve().parents[2]


def _iter_python_files(root: pathlib.Path) -> list[pathlib.Path]:
    """递归遍历根下 ``.py`` 文件，跳过缓存与虚拟环境目录。"""
    return [
        p
        for p in root.rglob("*.py")
        if "__pycache__" not in p.parts and ".venv" not in p.parts and ".test_deps" not in p.parts
    ]


def test_src_directory_has_zero_event_symbol_residue() -> None:
    """需求 8.8：``src/`` 下不得出现已移除符号。"""
    src_root = _project_root() / "src"
    offenders: list[tuple[pathlib.Path, int, str]] = []
    for path in _iter_python_files(src_root):
        for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _FORBIDDEN_TOKEN_PATTERN.search(line):
                offenders.append((path, idx, line.strip()))
    assert not offenders, (
        f"src/ 下检测到已移除事件符号残留: {offenders[:5]}（共 {len(offenders)} 处）"
    )


def test_test_directory_has_zero_event_symbol_residue() -> None:
    """需求 8.8：``test/`` 下除本门槛文件外不得出现已移除符号。

    本测试文件自身因列出被禁符号名而会命中正则，故在遍历时跳过。
    """
    test_root = _project_root() / "test"
    self_path = pathlib.Path(__file__).resolve()
    offenders: list[tuple[pathlib.Path, int, str]] = []
    for path in _iter_python_files(test_root):
        if path.resolve() == self_path:
            continue
        for idx, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _FORBIDDEN_TOKEN_PATTERN.search(line):
                offenders.append((path, idx, line.strip()))
    assert not offenders, (
        f"test/ 下检测到已移除事件符号残留: {offenders[:5]}（共 {len(offenders)} 处）"
    )
