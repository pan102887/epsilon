"""``Workspace`` Port ``context`` 参数签名静态契约测试。

本测试用 ``inspect.signature`` 遍历 ``Workspace`` Port 的方法：

- 7 个 I/O 方法（``exists`` / ``stat`` / ``read`` / ``write`` / ``edit`` /
  ``list_dir`` / ``delete``）**必须**声明一个名为 ``context`` 的
  ``KEYWORD_ONLY`` 参数，默认值为 ``None``，注解允许 ``dict | None``
  （PEP 604 风格）或 ``Optional[dict]``。
- 3 个非 I/O 方法（``resolve_path`` / ``capabilities`` /
  ``display_root_hint``）**不得**出现 ``context`` 参数。

失败消息显式包含漂移的方法名，便于维护期快速定位。
"""

from __future__ import annotations

import inspect
import typing

from domain.workspace.ports import Workspace

# ── 契约常量 ──
_IO_METHODS: tuple[str, ...] = (
    "exists",
    "stat",
    "read",
    "write",
    "edit",
    "list_dir",
    "delete",
)
_NON_IO_METHODS: tuple[str, ...] = (
    "resolve_path",
    "capabilities",
    "display_root_hint",
)


def _is_dict_or_optional_dict(annotation: object) -> bool:
    """判定注解是否等价于 ``dict | None`` / ``Optional[dict]``。

    覆盖三种等价写法：

    1. PEP 604 联合 ``dict | None``（运行期类型为 ``types.UnionType``）；
    2. ``typing.Optional[dict]``（等价于 ``Union[dict, None]``）；
    3. ``typing.Union[dict, None]``。

    容忍 ``dict`` 以及带泛型参数的 ``dict[str, Any]`` 等形态。
    """
    origin = typing.get_origin(annotation)
    if origin is None:
        return False

    # PEP 604 `dict | None` 与 `Optional[dict]` 的 origin 均是 Union 家族。
    # typing.Union 的 origin 是 typing.Union；types.UnionType 的 origin 是
    # types.UnionType 自身。二者都可用 get_args 取到成员。
    import types as _types  # 局部 import，保留测试顶部 import 白名单

    if origin is typing.Union or origin is _types.UnionType:
        args = typing.get_args(annotation)
        if type(None) not in args:
            return False
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) != 1:
            return False
        # 成员可能是裸 ``dict`` 或 ``dict[str, Any]`` 之类的泛型。
        member = non_none[0]
        member_origin = typing.get_origin(member)
        return member is dict or member_origin is dict

    return False


class TestIoMethodsHaveContextKeyword:
    """7 个 I/O 方法必须含 ``context: dict | None = None`` keyword-only 参数。"""

    def test_all_io_methods_declare_context(self) -> None:
        missing: list[str] = []
        for name in _IO_METHODS:
            method = getattr(Workspace, name)
            sig = inspect.signature(method)
            if "context" not in sig.parameters:
                missing.append(name)
        assert not missing, f"以下 I/O 方法未声明 context 参数（签名漂移）：{missing}"

    def test_context_is_keyword_only(self) -> None:
        offenders: list[str] = []
        for name in _IO_METHODS:
            method = getattr(Workspace, name)
            sig = inspect.signature(method)
            param = sig.parameters.get("context")
            assert param is not None, f"{name} 未声明 context 参数"
            if param.kind is not inspect.Parameter.KEYWORD_ONLY:
                offenders.append(f"{name}(context.kind={param.kind.name})")
        assert not offenders, f"以下 I/O 方法的 context 参数不是 KEYWORD_ONLY：{offenders}"

    def test_context_default_is_none(self) -> None:
        offenders: list[str] = []
        for name in _IO_METHODS:
            method = getattr(Workspace, name)
            sig = inspect.signature(method)
            param = sig.parameters["context"]
            if param.default is not None:
                offenders.append(f"{name}(default={param.default!r})")
        assert not offenders, f"以下 I/O 方法的 context 默认值不是 None：{offenders}"

    def test_context_annotation_is_dict_or_optional_dict(self) -> None:
        offenders: list[str] = []
        for name in _IO_METHODS:
            method = getattr(Workspace, name)
            # get_type_hints 会把 ``from __future__ import annotations`` 的
            # 字符串化注解解析为真正的类型对象，适合做语义等价判定。
            hints = typing.get_type_hints(method)
            annotation = hints.get("context", inspect.Parameter.empty)
            if annotation is inspect.Parameter.empty:
                offenders.append(f"{name}（缺失 context 注解）")
                continue
            if not _is_dict_or_optional_dict(annotation):
                offenders.append(f"{name}(annotation={annotation!r})")
        assert not offenders, (
            f"以下 I/O 方法的 context 注解非 `dict | None` / `Optional[dict]`：{offenders}"
        )

    def test_context_is_last_parameter(self) -> None:
        """``context`` 在方法签名中应位于末位（可读性与调用方约定）。"""
        offenders: list[str] = []
        for name in _IO_METHODS:
            method = getattr(Workspace, name)
            sig = inspect.signature(method)
            # 参数名顺序（去掉 self / cls）
            param_names = [p.name for p in sig.parameters.values() if p.name not in ("self", "cls")]
            if not param_names or param_names[-1] != "context":
                offenders.append(f"{name}({param_names})")
        assert not offenders, (
            f"以下 I/O 方法的 context 不在末位（与 docstring 承诺不一致）：{offenders}"
        )


class TestNonIoMethodsHaveNoContext:
    """3 个非 I/O 方法**不得**声明 ``context`` 参数。"""

    def test_non_io_methods_have_no_context(self) -> None:
        offenders: list[str] = []
        for name in _NON_IO_METHODS:
            method = getattr(Workspace, name)
            sig = inspect.signature(method)
            if "context" in sig.parameters:
                offenders.append(name)
        assert not offenders, (
            f"以下非 I/O 方法不应声明 context 参数（纯函数或元数据查询）：{offenders}"
        )
