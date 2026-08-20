"""证据路径与行号的存在性校验，用于报告生成前预检与回归校验。

本模块实现需求 3.4 所要求的"证据存在性验证"：批量接受
`EvidenceReference` 列表，逐条检查：

1. 路径是否在仓库中存在（相对 `repo_root` 解析后 `Path.exists()`）。
2. 行号是否合法：`line_start <= line_end <= 文件总行数`。
3. 若调用方提供 `expected_excerpts[raw]`，则按行号切片并做子串匹配。

**本模块不抛异常**：任一校验失败都会被捕获并写入
`EvidenceCheck.error` 字段（中文可读），以便 CLI 批量展示。调用方可
依据 `path_exists / line_range_valid / excerpt_matches` 三个布尔字段
决定返回退出码。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tests.evaluation.evidence.models import EvidenceKind, EvidenceReference


@dataclass(frozen=True)
class EvidenceCheck:
    """单条证据的校验结果。

    Attributes:
        reference: 原始证据引用，便于错误展示时回显。
        path_exists: 绝对路径 `repo_root / reference.path` 是否存在。
        line_range_valid: 行号范围是否合法（文件存在且 line_start ≤
            line_end ≤ 文件总行数）；`reference.kind != CODE_LINES` 时恒
            为 True。
        excerpt_matches: 若提供 `expected_excerpts[raw]`，则按行号切片后
            检查 expected 是否为切片的子串；否则恒为 True。
        error: 任一校验失败时的人类可读中文说明；全部通过时为 None。
    """

    reference: EvidenceReference
    path_exists: bool
    line_range_valid: bool
    excerpt_matches: bool
    error: str | None


def _read_total_lines(target: Path) -> int | None:
    """读取目标文件总行数。

    Args:
        target: 已确认存在的普通文件路径。

    Returns:
        文件总行数；读取失败（OSError / UnicodeDecodeError）时返回 None。
    """

    try:
        with target.open("r", encoding="utf-8", errors="replace") as fp:
            return sum(1 for _ in fp)
    except OSError:
        return None


def _read_line_slice(
    target: Path, line_start: int, line_end: int
) -> str | None:
    """按 1-based 行号区间读取文件切片内容。

    Args:
        target: 已确认存在的普通文件路径。
        line_start: 起始行号（含）。
        line_end: 结束行号（含）。

    Returns:
        切片拼接后的字符串（保留原换行）；读取失败返回 None。
    """

    try:
        with target.open("r", encoding="utf-8", errors="replace") as fp:
            collected: list[str] = []
            for idx, line in enumerate(fp, start=1):
                if idx < line_start:
                    continue
                if idx > line_end:
                    break
                collected.append(line)
            return "".join(collected)
    except OSError:
        return None


def _resolve_target(reference: EvidenceReference, repo_root: Path) -> Path:
    """把 `reference.path` 解析为仓库根下的绝对路径。

    路径若已经是绝对的则原样返回；否则拼接到 `repo_root` 下。
    不做 resolve() / realpath 调用，避免 symlink 语义带来的意外。
    """

    candidate = reference.path
    if candidate.is_absolute():
        return candidate
    return repo_root / candidate


def _check_single(
    reference: EvidenceReference,
    repo_root: Path,
    expected_excerpts: dict[str, str] | None,
) -> EvidenceCheck:
    """校验单条证据引用。

    该函数内部不抛异常；任何失败都转写为 `EvidenceCheck.error`。
    """

    error_parts: list[str] = []

    target = _resolve_target(reference, repo_root)
    path_exists = target.exists()
    if not path_exists:
        error_parts.append(
            f"证据路径不存在：{reference.path}（解析为 {target}）"
        )

    line_range_valid = True
    if reference.kind == EvidenceKind.CODE_LINES and path_exists:
        if not target.is_file():
            line_range_valid = False
            error_parts.append(
                f"证据路径 {reference.path} 不是文件，无法按行号校验"
            )
        else:
            total_lines = _read_total_lines(target)
            if total_lines is None:
                line_range_valid = False
                error_parts.append(
                    f"证据文件 {reference.path} 读取失败，行号无法校验"
                )
            else:
                ls = reference.line_start or 0
                le = reference.line_end or 0
                if ls < 1 or le < 1 or ls > le or le > total_lines:
                    line_range_valid = False
                    error_parts.append(
                        f"证据行号越界：{reference.path}:{ls}-{le}，"
                        f"实际文件总行数 {total_lines}"
                    )
    elif reference.kind == EvidenceKind.CODE_LINES and not path_exists:
        # 路径不存在时，行号自然无法校验，但不再重复报错
        line_range_valid = False

    excerpt_matches = True
    expected = (expected_excerpts or {}).get(reference.raw)
    if expected is not None:
        if (
            reference.kind != EvidenceKind.CODE_LINES
            or not path_exists
            or not line_range_valid
        ):
            excerpt_matches = False
            error_parts.append(
                f"证据摘录校验跳过但被要求：{reference.raw}"
            )
        else:
            slice_content = _read_line_slice(
                target,
                reference.line_start or 1,
                reference.line_end or 1,
            )
            if slice_content is None:
                excerpt_matches = False
                error_parts.append(
                    f"证据文件 {reference.path} 切片读取失败，摘录无法校验"
                )
            elif expected not in slice_content:
                excerpt_matches = False
                error_parts.append(
                    f"证据摘录不匹配：{reference.raw}，"
                    f"期望片段未出现在行号区间内"
                )

    error_message: str | None
    if error_parts:
        error_message = "；".join(error_parts)
    else:
        error_message = None

    return EvidenceCheck(
        reference=reference,
        path_exists=path_exists,
        line_range_valid=line_range_valid,
        excerpt_matches=excerpt_matches,
        error=error_message,
    )


def verify_evidence(
    references: list[EvidenceReference],
    repo_root: Path,
    expected_excerpts: dict[str, str] | None = None,
) -> list[EvidenceCheck]:
    """批量校验证据列表，返回每条的校验结果。

    Args:
        references: 待校验的证据引用列表（通常由 `catalog.load_catalog()`
            汇总得到）。
        repo_root: 仓库根目录；`reference.path` 若为相对路径则相对此目录
            解析。
        expected_excerpts: 可选的 "raw → 期望摘录" 映射；仅当某条证据
            的 `raw` 存在于该映射时，才对其做摘录子串匹配。

    Returns:
        与 `references` 等长、等序的 `EvidenceCheck` 列表；任一条失败时
        其 `error` 字段含中文说明，**本函数不抛异常**。
    """

    results: list[EvidenceCheck] = []
    for ref in references:
        results.append(_check_single(ref, repo_root, expected_excerpts))
    return results
