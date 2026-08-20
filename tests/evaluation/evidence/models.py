"""证据引用领域模型（评测视角，与业务 domain/ 层无关）。

本模块实现 spec-ai-evaluation 报告中每条结论所附的证据引用的格式解析，
对齐需求 3.2 规定的正则 `^[^\\s:]+(:L?\\d+(-L?\\d+)?)?$`，支持以下四种
合法形式：

- `path/to/file.py:10-42`（代码行区间）
- `path/to/file.py:L10-L42`（带 L 前缀的代码行区间）
- `path/to/file.py:L10`（单行代码行，line_end == line_start）
- `path/to/file.py`（仅路径，不指定行号）
- `config.properties:<KEY>`（配置键；`<KEY>` 非纯数字时归入 CONFIG_KEY）

非法形式（空白、仅目录结尾、跨文件通配符、负行号、零行号、start > end
等）一律抛 `EvidenceFormatError`。

本模块不依赖 FastAPI / Redis / LLM 客户端等基础设施。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from tests.evaluation.errors import EvidenceFormatError


class EvidenceKind(str, Enum):
    """证据类型枚举。

    - CODE_LINES：证据指向源码文件的某行或某区间（如 `:L10-L42`）。
    - CONFIG_KEY：证据指向 `config.properties` 中的某个键（如
      `config.properties:MODEL_DEFAULT`）。
    - PATH_ONLY：仅指向文件路径，不含行号（允许用于 `docs/` 下的长文档
      或整体结构性引用）。
    """

    CODE_LINES = "code_lines"
    CONFIG_KEY = "config_key"
    PATH_ONLY = "path_only"


@dataclass(frozen=True)
class EvidenceReference:
    """单条证据引用，对齐需求 3.2 的格式约束。

    Attributes:
        raw: 原始字符串（去除首尾空白后的版本），例如
            "epsilon-boot/src/infrastructure/agent/react_agent_adapter.py:L10-L42"。
        kind: 证据类型，见 `EvidenceKind`。
        path: 仓库根相对路径（`pathlib.PurePath` 视图；不强制要求文件真实存在，
            存在性由 `verify_evidence` 单独校验）。
        line_start: 起始行号（从 1 开始）；`kind == PATH_ONLY` 或
            `kind == CONFIG_KEY` 时为 None。
        line_end: 结束行号；单行时与 `line_start` 相等；`kind != CODE_LINES`
            时为 None。
        description: 人工撰写的一句话证据描述，用于报告渲染与审阅。
    """

    raw: str
    kind: EvidenceKind
    path: Path
    line_start: int | None
    line_end: int | None
    description: str


# 证据整体格式正则：路径（不含空白与冒号）+ 可选 ":<suffix>"。
_REFERENCE_PATTERN = re.compile(r"^[^\s:]+(?::L?\d+(?:-L?\d+)?)?$")
# 提取 path 与 suffix 两部分：suffix 为 None 代表纯路径；否则为 "Lstart" / "Lstart-Lend" / "start" 形式。
_SPLIT_PATTERN = re.compile(
    r"^(?P<path>[^\s:]+?)(?::(?P<suffix>.*))?$"
)
# 行号部分：严格匹配 "L?N" 或 "L?N-L?M"，N/M 为 1 位及以上数字（无前导 + 号）。
_LINE_SUFFIX_PATTERN = re.compile(
    r"^L?(?P<start>\d+)(?:-L?(?P<end>\d+))?$"
)


def _is_config_key_target(path_part: str, suffix: str) -> bool:
    """判断冒号后的内容是否应解析为配置键。

    规则：
    - 路径末段必须是 `config.properties`（允许带任意目录前缀）；
    - suffix 非空且不是"纯数字/Lxxx/Lxxx-Lxxx"形式（避免误把
      `config.properties:42` 之类当成配置键）。
    """

    if not path_part.endswith("config.properties"):
        return False
    if _LINE_SUFFIX_PATTERN.fullmatch(suffix):
        return False
    return True


def parse_reference(raw: str, description: str) -> EvidenceReference:
    """按需求 3.2 定义的格式解析证据引用字符串。

    Args:
        raw: 原始证据字符串，形如 `path:Lstart-Lend` / `path:Lstart` /
            `path` / `config.properties:<KEY>`。
        description: 人工撰写的一句话证据描述，不参与格式校验。

    Returns:
        解析后的 `EvidenceReference` 实例。

    Raises:
        EvidenceFormatError: `raw` 为空白、含空格、包含非法字符、行号
            不合法（零、负、start > end）或未匹配合法形式时。
    """

    if not isinstance(raw, str):
        raise EvidenceFormatError(
            f"证据引用必须为字符串，实际类型 {type(raw).__name__}"
        )

    stripped = raw.strip()
    if not stripped:
        raise EvidenceFormatError("证据引用不能为空字符串")

    if stripped != raw:
        # 允许用户传入两端含空白，但规范化后再存
        raw = stripped

    # 先用 split 正则切分路径与 suffix，便于后续判定。
    split_match = _SPLIT_PATTERN.match(raw)
    if split_match is None:
        raise EvidenceFormatError(f"证据引用格式非法：{raw!r}")

    path_part = split_match.group("path")
    suffix = split_match.group("suffix")

    if not path_part:
        raise EvidenceFormatError(f"证据引用缺少路径部分：{raw!r}")

    # 禁止以 "/" 结尾（仅目录）
    if path_part.endswith("/"):
        raise EvidenceFormatError(
            f"证据引用不得仅指向目录：{raw!r}"
        )

    # 禁止路径中出现通配符等明显非法符号
    if any(ch in path_part for ch in ("*", "?", "[", "]")):
        raise EvidenceFormatError(
            f"证据引用路径不得包含通配符：{raw!r}"
        )

    if suffix is None:
        # 仅路径，无后缀 → PATH_ONLY。
        return EvidenceReference(
            raw=raw,
            kind=EvidenceKind.PATH_ONLY,
            path=Path(path_part),
            line_start=None,
            line_end=None,
            description=description,
        )

    # 冒号后内容不能为空
    if suffix == "":
        raise EvidenceFormatError(
            f"证据引用冒号后不得为空：{raw!r}"
        )

    # 若路径指向 config.properties，且 suffix 不是行号形式 → CONFIG_KEY。
    if _is_config_key_target(path_part, suffix):
        # config key 必须非空，且不得含空白
        if " " in suffix or "\t" in suffix:
            raise EvidenceFormatError(
                f"config.properties 键不得含空白：{raw!r}"
            )
        return EvidenceReference(
            raw=raw,
            kind=EvidenceKind.CONFIG_KEY,
            path=Path(path_part),
            line_start=None,
            line_end=None,
            description=description,
        )

    # 其他情况按行号形式解析：严格匹配 L?N / L?N-L?M
    line_match = _LINE_SUFFIX_PATTERN.fullmatch(suffix)
    if line_match is None:
        # 整体正则兜底：若整体正则匹配但不是行号也不是 config key，直接判非法
        if _REFERENCE_PATTERN.fullmatch(raw):
            raise EvidenceFormatError(
                f"证据引用的行号部分格式非法：{raw!r}"
            )
        raise EvidenceFormatError(f"证据引用格式非法：{raw!r}")

    start_raw = line_match.group("start")
    end_raw = line_match.group("end")

    # 前导零或长度为 0 视为非法（`_LINE_SUFFIX_PATTERN` 已保证 \d+，这里补检 0）
    line_start = int(start_raw)
    if line_start < 1:
        raise EvidenceFormatError(
            f"证据引用起始行号必须 >= 1：{raw!r}"
        )

    if end_raw is None:
        line_end = line_start
    else:
        line_end = int(end_raw)
        if line_end < 1:
            raise EvidenceFormatError(
                f"证据引用结束行号必须 >= 1：{raw!r}"
            )
        if line_end < line_start:
            raise EvidenceFormatError(
                f"证据引用结束行号不得小于起始行号：{raw!r}"
            )

    return EvidenceReference(
        raw=raw,
        kind=EvidenceKind.CODE_LINES,
        path=Path(path_part),
        line_start=line_start,
        line_end=line_end,
        description=description,
    )
