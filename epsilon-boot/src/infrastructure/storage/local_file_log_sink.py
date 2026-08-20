"""TUI/CLI 本地文件日志装配模块。

经 ``LocalFileTierResolver`` 解析 USER tier 的 logs 目录
（``~/.epsilon/<project-hash>/logs/``），装配带脱敏 Filter 的
``RotatingFileHandler``。日志随用户走、不污染项目工作区（ADR-0005 决策 2b）；
禁止把凭证/密钥明文写入日志文件（需求 4.4）。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from logging.handlers import RotatingFileHandler

from domain.storage.storage_tier import StorageTier
from infrastructure.storage.local_file_tier_resolver import LocalFileTierResolver
from infrastructure.storage.log_sink_config import LogSinkConfig

_REDACTED = "****"
"""脱敏后用于替换敏感取值的占位串。"""


class SensitiveRedactionFilter(logging.Filter):
    """在写盘前对日志消息做敏感字段脱敏。

    复用既有敏感字段词表，对形如 ``key=value`` / ``"key": "value"`` 的片段将其
    取值替换为 ``****``，避免 API Key / token / cookie / authorization 明文落盘。
    脱敏就地作用于 ``record.msg`` 与 ``record.args``，恒返回 True 不丢弃记录；
    脱敏过程被完整兜底，任何异常都不影响日志写出。
    """

    def __init__(self, sensitive_keys: frozenset[str]) -> None:
        """构造脱敏 Filter，为每个敏感 key 预编译匹配正则。

        Args:
            sensitive_keys: 需脱敏的字段名集合（大小写不敏感匹配）。
        """
        super().__init__()
        if sensitive_keys:
            joined = "|".join(re.escape(key) for key in sorted(sensitive_keys))
            # 匹配 key 后跟 : 或 = 分隔符，再捕获取值：
            #   分组 prefix：可选引号包裹的 key 与分隔符；
            #   分组 quoted：双引号包裹的取值（可含空格，直到闭合引号）；
            #   分组 bare：无引号取值（到空白/逗号/&/双引号截止）。
            self._pattern: re.Pattern[str] | None = re.compile(
                rf'(?i)(?P<prefix>"?(?:{joined})"?\s*[:=]\s*)'
                r'(?:"(?P<quoted>[^"]*)"|(?P<bare>[^"\s,&]+))'
            )
        else:
            self._pattern = None

    def filter(self, record: logging.LogRecord) -> bool:
        """就地脱敏 record.msg / record.args，恒返回 True（不丢弃记录）。

        Args:
            record: 待处理的日志记录。

        Returns:
            恒为 True，确保记录继续沿 handler 链写出。
        """
        if self._pattern is None:
            return True
        try:
            if isinstance(record.msg, str):
                record.msg = self._redact(record.msg)
            record.args = self._redact_args(record.args)
        except Exception:  # 脱敏失败不得影响日志写出，兜底放行原始记录
            return True
        return True

    def _redact(self, text: str) -> str:
        """对单个字符串执行敏感取值替换。"""
        assert self._pattern is not None

        def _replace(match: re.Match[str]) -> str:
            prefix = match.group("prefix")
            if match.group("quoted") is not None:
                return f'{prefix}"{_REDACTED}"'
            return f"{prefix}{_REDACTED}"

        return self._pattern.sub(_replace, text)

    def _redact_args(
        self, args: tuple[object, ...] | Mapping[str, object] | None
    ) -> tuple[object, ...] | Mapping[str, object] | None:
        """对日志格式化参数逐项脱敏，仅处理字符串项，其余原样返回。"""
        if isinstance(args, tuple):
            return tuple(self._redact(a) if isinstance(a, str) else a for a in args)
        if isinstance(args, Mapping):
            return {
                k: (self._redact(v) if isinstance(v, str) else v)
                for k, v in args.items()
            }
        return args


def configure_local_file_logging(
    tier_resolver: LocalFileTierResolver,
    config: LogSinkConfig,
    sensitive_keys: frozenset[str],
    *,
    tier: StorageTier = StorageTier.USER,
) -> logging.Handler | None:
    """装配本地文件日志 handler 并挂到 root logger。

    经 ``tier_resolver`` 解析 USER tier 的 logs 目录
    （``~/.epsilon/<project-hash>/logs/``），创建带脱敏 Filter 的
    ``RotatingFileHandler`` 写入 ``epsilon.log``。``config.to_file`` 为 False 时
    不装配、直接返回 None。

    Args:
        tier_resolver: 本地文件 tier 解析器，用于定位日志目录。
        config: 本地文件日志配置（级别、轮转参数、开关）。
        sensitive_keys: 需脱敏的字段名集合，用于构造脱敏 Filter。
        tier: 日志落地的存储等级，默认 USER（ADR-0005 决策 2b，不落项目工作区）。

    Returns:
        已挂载到 root logger 的 ``RotatingFileHandler``；未启用时返回 None。
    """
    if not config.to_file:
        return None
    logs_dir = tier_resolver.resolve(tier).logs_dir(create=True)
    handler = RotatingFileHandler(
        filename=str(logs_dir / "epsilon.log"),
        maxBytes=config.rotation_max_bytes,
        backupCount=config.rotation_backup_count,
        encoding="utf-8",
    )
    handler.setLevel(config.level)
    handler.addFilter(SensitiveRedactionFilter(sensitive_keys))
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logging.getLogger().addHandler(handler)
    return handler
