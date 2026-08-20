"""统一业务异常定义模块。

提供 BizException 作为业务异常基类，所有业务层抛出的异常
应继承自此类，以便统一异常处理器识别并返回规范化的响应。
"""


class BizException(Exception):
    """业务异常基类。

    Args:
        code: 业务错误码。
        message: 错误描述信息。

    Usage::

        raise BizException(code=40001, message="资源不存在")
    """

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(message)
