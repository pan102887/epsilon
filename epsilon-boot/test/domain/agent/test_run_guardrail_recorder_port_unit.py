"""Run guardrail recorder 端口静态契约测试模块。"""

import inspect

from domain.agent.ports import RunGuardrailRecorderPort


def _annotation_text(annotation: object) -> str:
    """把签名注解转为稳定可断言的文本。"""
    return str(annotation).replace('"', "").replace("'", "")


def test_run_guardrail_recorder_port_signature() -> None:
    """验证 RunGuardrailRecorderPort.record_observation(...) 协议签名。"""
    signature = inspect.signature(RunGuardrailRecorderPort.record_observation)

    assert list(signature.parameters) == ["self", "observation"]
    assert _annotation_text(signature.parameters["observation"].annotation) == (
        "GuardrailObservation"
    )
    assert _annotation_text(signature.return_annotation) == "RunSnapshot | None"
