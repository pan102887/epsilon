"""请求日志中间件配置模块。

基于 pydantic-settings，从 .env 文件和环境变量加载以 ``LOGGING_REQUEST_`` 为前缀的配置项。

模块级实例 ``request_logging_config`` 作为全局单例使用。
"""

from pydantic_settings import SettingsConfigDict

from common.configuration import PropertiesBaseSettings


class RequestLoggingConfig(PropertiesBaseSettings):
    """请求日志中间件配置，对应环境变量前缀 ``LOGGING_REQUEST_``。

    Attributes:
        max_body_log_size: 请求体/响应体日志截断阈值（字符数），
            对应 ``LOGGING_REQUEST_MAX_BODY_LOG_SIZE``，默认 ``2048``。
            超过该长度的报文体在日志中只保留前 N 个字符并附加截断提示。
        enabled: 是否启用请求日志中间件，
            对应 ``LOGGING_REQUEST_ENABLED``，默认 ``True``。
        body_enabled: 是否记录请求体，
            对应 ``LOGGING_REQUEST_BODY_ENABLED``，默认 ``False``。
        sensitive_headers: 需要脱敏的请求头名称，逗号分隔，全小写，
            对应 ``LOGGING_REQUEST_SENSITIVE_HEADERS``，
            默认 ``"authorization,cookie,set-cookie,x-api-key"``。
        sensitive_body_fields: 需要脱敏的报文字段名称，逗号分隔，全小写，
            对应 ``LOGGING_REQUEST_SENSITIVE_BODY_FIELDS``。
    """

    model_config = SettingsConfigDict(env_prefix="LOGGING_REQUEST_")

    max_body_log_size: int = 2048
    enabled: bool = True
    body_enabled: bool = False
    sensitive_headers: str = "authorization,cookie,set-cookie,x-api-key"
    sensitive_body_fields: str = (
        "password,api_key,token,access_token,refresh_token,secret,authorization,cookie"
    )

    def get_sensitive_headers_set(self) -> frozenset[str]:
        """将逗号分隔的敏感头配置解析为小写的 frozenset。

        Returns:
            敏感头名称集合，所有名称均为小写。
        """
        raw = self.sensitive_headers
        if not raw or not raw.strip():
            return frozenset()
        return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())

    def get_sensitive_body_fields_set(self) -> frozenset[str]:
        """将逗号分隔的敏感报文字段配置解析为小写的 frozenset。

        Returns:
            需要从请求体和响应体日志中脱敏的字段名集合。
        """
        raw = self.sensitive_body_fields
        if not raw or not raw.strip():
            return frozenset()
        return frozenset(item.strip().lower() for item in raw.split(",") if item.strip())


class ResponseLoggingConfig(PropertiesBaseSettings):
    """响应日志配置，对应环境变量前缀 ``LOGGING_RESPONSE_``。

    Attributes:
        body_enabled: 是否记录响应体，
            对应 ``LOGGING_RESPONSE_BODY_ENABLED``，默认 ``False``。
    """

    model_config = SettingsConfigDict(env_prefix="LOGGING_RESPONSE_")

    body_enabled: bool = False


request_logging_config = RequestLoggingConfig()
"""全局请求日志配置实例，模块级单例。"""

response_logging_config = ResponseLoggingConfig()
"""全局响应日志配置实例，模块级单例。"""
