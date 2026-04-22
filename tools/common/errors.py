from __future__ import annotations


class ProviderError(Exception):
    """第三方数据源通用异常。"""


class ParseError(ProviderError):
    """第三方返回结构异常或字段无法解析。"""


class RateLimitError(ProviderError):
    """第三方触发限频。"""
