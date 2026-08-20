"""证据校验器自测。

覆盖点：
- 路径不存在 → `EvidenceCheck.path_exists=False` 且 `error` 含中文描述；
- 行号越界 → `line_range_valid=False` 且 `error` 含中文描述；
- 摘录不匹配 → `excerpt_matches=False` 且 `error` 含中文描述；
- 全部通过 → `error is None`；
- 本函数绝不抛异常。

对应需求：3.4。
"""

from __future__ import annotations

from pathlib import Path

from tests.evaluation.evidence import parse_reference
from tests.evaluation.evidence.verifier import verify_evidence


def _write_file(p: Path, content: str) -> None:
    """在 tmp_path 下创建一个 UTF-8 文件。"""

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_verify_evidence_all_pass(tmp_path: Path) -> None:
    """合法路径 + 合法行号 + 匹配摘录 → 全绿，error 为 None。"""

    target = tmp_path / "pkg" / "module.py"
    _write_file(target, "line1\nline2\nline3\nline4\n")

    ref_path_only = parse_reference("pkg/module.py", "a")
    ref_lines = parse_reference("pkg/module.py:2-3", "b")

    results = verify_evidence(
        [ref_path_only, ref_lines],
        repo_root=tmp_path,
        expected_excerpts={ref_lines.raw: "line2"},
    )

    assert [r.error for r in results] == [None, None]
    assert all(r.path_exists for r in results)
    assert all(r.line_range_valid for r in results)
    assert all(r.excerpt_matches for r in results)


def test_verify_evidence_path_missing(tmp_path: Path) -> None:
    """路径不存在 → path_exists=False 且 error 含中文说明，不抛异常。"""

    ref = parse_reference("does/not/exist.py", "a")
    [result] = verify_evidence([ref], repo_root=tmp_path)
    assert result.path_exists is False
    assert result.error is not None
    assert "证据路径不存在" in result.error


def test_verify_evidence_line_out_of_range(tmp_path: Path) -> None:
    """行号越界 → line_range_valid=False，error 中含实际总行数。"""

    target = tmp_path / "small.py"
    _write_file(target, "only-one-line\n")
    ref = parse_reference("small.py:5-7", "a")
    [result] = verify_evidence([ref], repo_root=tmp_path)
    assert result.path_exists is True
    assert result.line_range_valid is False
    assert result.error is not None
    assert "证据行号越界" in result.error


def test_verify_evidence_excerpt_mismatch(tmp_path: Path) -> None:
    """摘录不匹配 → excerpt_matches=False 且 error 含提示。"""

    target = tmp_path / "pkg" / "module.py"
    _write_file(target, "alpha\nbeta\ngamma\n")
    ref = parse_reference("pkg/module.py:1-2", "a")

    [result] = verify_evidence(
        [ref],
        repo_root=tmp_path,
        expected_excerpts={ref.raw: "gamma"},
    )
    assert result.path_exists is True
    assert result.line_range_valid is True
    assert result.excerpt_matches is False
    assert result.error is not None
    assert "摘录不匹配" in result.error


def test_verify_evidence_does_not_raise_for_any_failure(tmp_path: Path) -> None:
    """多条失败样本混合在一批里调用不抛异常，全部以 EvidenceCheck 形式返回。"""

    target = tmp_path / "f.py"
    _write_file(target, "a\nb\n")

    refs = [
        parse_reference("not/exist.py", "n1"),
        parse_reference("f.py:100", "n2"),
        parse_reference("f.py:1", "n3"),
    ]

    results = verify_evidence(refs, repo_root=tmp_path)
    # 三条全部返回；第三条应 error is None
    assert len(results) == 3
    assert results[0].error is not None
    assert results[1].error is not None
    assert results[2].error is None
