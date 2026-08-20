"""长任务阶段二前端契约静态测试。

本模块验证阶段二实现前的前端静态验证准入记录，以及前端 lint 脚本
是否使用当前 ESLint flat config 可识别的项目扫描命令。
"""

from __future__ import annotations

import json
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_phase2_tasks_record_frontend_static_verification_gate() -> None:
    """阶段二任务清单必须记录 ESLint 与 TypeScript 准入验证。"""
    tasks = (_REPO_ROOT / "docs/spec/long-task-continuation-phase2/tasks.md").read_text(
        encoding="utf-8"
    )

    assert "bun run lint" in tasks or "npm run lint" in tasks
    assert "tsc --noEmit --pretty false" in tasks
    assert "前端静态验证准入" in tasks


def test_phase1_summary_records_frontend_static_verification_gap() -> None:
    """阶段一 summary 必须保留前端静态验证缺口作为阶段二准入背景。"""
    summary = (_REPO_ROOT / "docs/spec/long-task-continuation-phase1/summary.md").read_text(
        encoding="utf-8"
    )

    assert "npm run lint" in summary
    assert "tsc --noEmit" in summary
    assert "未获得有效 ESLint / TypeScript 诊断输出" in summary


def test_config_properties_declares_segment_defaults() -> None:
    """config.properties 必须声明 Chat 与 Task 分段执行默认键。"""
    config_properties = (_REPO_ROOT / "epsilon-boot/config.properties").read_text(
        encoding="utf-8"
    )
    expected_keys = [
        "CHAT_SEGMENT_AUTO_CONTINUE_ENABLED=false",
        "CHAT_SEGMENT_MAX_CONTINUATIONS=3",
        "CHAT_SEGMENT_MAX_TOTAL_TOKENS=0",
        "CHAT_SEGMENT_MAX_DURATION_SECONDS=0",
        "CHAT_SEGMENT_MAX_CONSECUTIVE_PAUSED=2",
        "CHAT_SEGMENT_MAX_NO_PROGRESS_SEGMENTS=2",
        "CHAT_SEGMENT_MAX_REPEATED_TOOL_CALLS=2",
        "TASK_AGENT_SEGMENT_AUTO_CONTINUE_ENABLED=false",
        "TASK_AGENT_SEGMENT_MAX_CONTINUATIONS=3",
        "TASK_AGENT_SEGMENT_MAX_TOTAL_TOKENS=0",
        "TASK_AGENT_SEGMENT_MAX_DURATION_SECONDS=0",
        "TASK_AGENT_SEGMENT_MAX_CONSECUTIVE_PAUSED=2",
        "TASK_AGENT_SEGMENT_MAX_NO_PROGRESS_SEGMENTS=2",
        "TASK_AGENT_SEGMENT_MAX_REPEATED_TOOL_CALLS=2",
    ]

    for key in expected_keys:
        assert key in config_properties


def test_frontend_lint_script_scans_project() -> None:
    """前端 lint 脚本必须显式扫描项目，而不是裸调用 eslint。"""
    package_json = json.loads(
        (_REPO_ROOT / "epsilon-client/package.json").read_text(encoding="utf-8")
    )

    assert package_json["scripts"]["lint"] == "eslint ."


def test_phase2_specs_keep_request_scoped_runtime_boundary() -> None:
    """阶段二 spec 必须声明不引入后台 run/checkpoint/workflow runtime。"""
    spec_dir = _REPO_ROOT / "docs/spec/long-task-continuation-phase2"
    combined = "\n".join(
        (spec_dir / name).read_text(encoding="utf-8")
        for name in ("requirement.md", "design.md", "tasks.md")
    )

    assert "不新增后台 `run_id`" in combined or "不新增后台 run" in combined
    assert "不引入持久化检查点" in combined or "checkpoint" in combined
    assert "不引入新工作流运行时" in combined or "workflow runtime" in combined


def test_frontend_segment_stop_reason_matches_backend_design() -> None:
    """前端 SegmentStopReason 必须包含后端设计全部停止原因。"""
    chat_api = (_REPO_ROOT / "epsilon-client/src/lib/chat-api.ts").read_text(encoding="utf-8")
    expected_reasons = [
        "completed",
        "auto_disabled",
        "approval_required",
        "max_continuations_reached",
        "total_token_budget_reached",
        "total_duration_budget_reached",
        "consecutive_paused_limit",
        "no_progress",
        "repeated_tool_call",
        "tool_boundary_unavailable",
        "continue_precondition_failed",
        "risk_gate_required",
    ]

    assert "export type SegmentStopReason" in chat_api
    for reason in expected_reasons:
        assert f'"{reason}"' in chat_api
