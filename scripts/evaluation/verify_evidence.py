"""证据存在性校验工具：批量校验 Evidence_Reference 对应路径与行号是否有效。

用法：
    uv run python -m scripts.evaluation.verify_evidence
    uv run python -m scripts.evaluation.verify_evidence --repo-root=..

退出码：
    0 — 全部证据有效。
    1 — 存在至少一条证据失败（路径不存在 / 行号越界 / 摘录不匹配）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：加载证据目录、逐条校验、打印表格、返回退出码。

    Args:
        argv: 命令行参数列表；``None`` 时使用 ``sys.argv[1:]``。

    Returns:
        退出码：0 全部通过 / 1 有失败。
    """

    parser = argparse.ArgumentParser(
        prog="verify_evidence",
        description="证据存在性校验工具",
    )
    parser.add_argument(
        "--catalog",
        default=None,
        help="证据目录模块路径（默认使用 tests.evaluation.evidence.catalog）",
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="仓库根目录（默认自动推断）",
    )

    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 1

    if args.repo_root:
        repo_root = Path(args.repo_root).resolve()
    else:
        repo_root = Path(__file__).resolve().parents[2]

    from tests.evaluation.evidence.catalog import load_catalog
    from tests.evaluation.evidence.verifier import verify_evidence

    catalog = load_catalog()

    all_refs = []
    ref_to_dim: dict[str, str] = {}
    for dim_id, refs in catalog.items():
        for ref in refs:
            all_refs.append(ref)
            ref_to_dim[ref.raw] = dim_id

    checks = verify_evidence(all_refs, repo_root)

    header = (
        f"{'维度':<18}{'路径':<72}{'行段':<16}{'状态':<10}{'失败原因'}"
    )
    print(header)
    print("-" * 118)

    failures = 0
    for check in checks:
        dim = ref_to_dim.get(check.reference.raw, "?")
        path_display = str(check.reference.path)
        if len(path_display) > 70:
            path_display = "..." + path_display[-67:]
        if check.reference.line_start and check.reference.line_end:
            line_seg = f"L{check.reference.line_start}-L{check.reference.line_end}"
        elif check.reference.line_start:
            line_seg = f"L{check.reference.line_start}"
        else:
            line_seg = "-"
        if check.error:
            status = "FAIL"
            failures += 1
        else:
            status = "OK"
        error_msg = check.error or ""
        print(f"{dim:<18}{path_display:<72}{line_seg:<16}{status:<10}{error_msg}")

    print("-" * 40)
    total = len(checks)
    print(f"汇总：共 {total} 条证据，{failures} 条失败。")

    return 1 if failures > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
