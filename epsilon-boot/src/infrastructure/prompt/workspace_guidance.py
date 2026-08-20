"""Workspace 路径规范追加纯函数模块。

本模块把原 :meth:`infrastructure.chat.chat_config.ChatConfig._append_workspace_path_guidance`
的幂等追加逻辑抽取为不依赖 ``pydantic`` 的纯函数，由 Prompt 消费方
（:class:`infrastructure.chat.chat_service_adapter.ChatServiceAdapter`）
在把 :attr:`domain.prompt.value_objects.LoadedPrompt.content` 组装进
``AgentConfig.system_prompt`` 时调用。

常量来源与唯一性：:data:`_WORKSPACE_PATH_GUIDANCE` 保留在
:mod:`infrastructure.chat.chat_config` 中作为单一常量源；本模块仅通过
re-export 暴露，**不复制**其文案定义。两个模块对同一常量保持单一出处，
避免未来运维者改了一处忘改另一处导致不一致。

幂等语义（Property 3）：对任意字符串 ``s``，调用
``append_workspace_path_guidance(append_workspace_path_guidance(s))``
的结果必然等于 ``append_workspace_path_guidance(s)``；幂等判断基于
``rstrip().endswith(_WORKSPACE_PATH_GUIDANCE.strip())``，即若 ``s`` 末尾
（忽略尾随空白）已含相同规范文案即跳过追加。
"""

from __future__ import annotations

from infrastructure.chat.chat_config import _WORKSPACE_PATH_GUIDANCE

__all__ = ["_WORKSPACE_PATH_GUIDANCE", "append_workspace_path_guidance"]


def append_workspace_path_guidance(content: str) -> str:
    """把工作区路径规范文案幂等追加到 ``content`` 末尾。

    幂等判断：若 ``content.rstrip().endswith(_WORKSPACE_PATH_GUIDANCE.strip())``
    为真，说明末尾（忽略尾随空白）已含规范文案，直接原样返回，避免文案
    堆叠。否则把 :data:`_WORKSPACE_PATH_GUIDANCE` 拼接在 ``content`` 之后
    返回。

    本函数**不会**修改 ``prompt_id``（需求 6.4）：``prompt_id`` 反映 Prompt
    资产文件版本，而路径规范文案属于"进程级运行期注入"，不参与版本化。

    Args:
        content: 原始 Prompt 文本（通常来自 ``LoadedPrompt.content`` 或
            其他需要追加工作区路径规范的 Prompt 字符串）。

    Returns:
        追加规范后的文本；若已追加则原样返回。返回值必然以
        :data:`_WORKSPACE_PATH_GUIDANCE` 的 ``strip()`` 形式结尾
        （满足 ``rstrip().endswith(...)``）。
    """
    if content.rstrip().endswith(_WORKSPACE_PATH_GUIDANCE.strip()):
        return content
    return content + _WORKSPACE_PATH_GUIDANCE
