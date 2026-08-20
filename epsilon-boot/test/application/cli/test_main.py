"""epsilon CLI 入口参数与 exec 输出测试。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from application.cli import main as cli_main


@dataclass(frozen=True)
class FakeJsonResult:
    """测试用 exec JSON 结果。"""

    status: str = "success"

    def to_dict(self) -> dict[str, Any]:
        """返回脚本输出需要的结构化字典。"""
        return {
            "status": self.status,
            "content": "done",
            "model": "glm-4.7",
            "prompt_id": "task-template@v1",
            "usage": {},
            "latency_ms": 0.0,
            "terminated_reason": "completed",
            "can_continue": False,
            "approval_id": None,
            "trace_ref": {"available": False, "step_count": 0},
            "artifact_ref": {"available": True},
        }


class FakeRuntime:
    """测试用 CLI Runtime 异步上下文管理器。"""

    async def __aenter__(self) -> FakeRuntime:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        return None

    async def execute_once_json(self, goal: str, *, model: str | None = None) -> FakeJsonResult:
        """记录调用参数并返回结构化结果。"""
        self.goal = goal
        self.model = model
        return FakeJsonResult()


def test_exec_parser_accepts_json_flag() -> None:
    parser = cli_main.build_parser()

    args = parser.parse_args(["exec", "summarize", "--json", "--model", "glm-4.7"])

    assert args.command == "exec"
    assert args.goal == "summarize"
    assert args.json is True
    assert args.model == "glm-4.7"


def test_exec_json_outputs_structured_payload(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli_main, "CliRuntime", FakeRuntime)
    monkeypatch.setattr(cli_main, "_configure_cli_file_logging", lambda: None)

    exit_code = cli_main.main(["exec", "summarize", "--json", "--model", "glm-4.7"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"status": "success"' in captured.out
    assert '"trace_ref": {"available": false, "step_count": 0}' in captured.out
