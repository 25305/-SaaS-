# -*- coding: utf-8 -*-
"""统一错误码与业务异常(模块 G,FR-G3)。

五类错误码(验收要求):
  1. 配额      QUOTA_EXCEEDED       用量超出租户配额
  2. 限流      RATE_LIMITED         单位时间请求过多
  3. 鉴权      UNAUTHORIZED / FORBIDDEN / INVALID_TOKEN
  4. Key 失效  API_KEY_INVALID      调用密钥不存在/已吊销/已过期
  5. 模型异常  MODEL_SERVICE_ERROR  上游模型服务不可用或返回异常

响应体统一为: {"code": <错误码>, "message": <面向用户的提示>, "data": <可选附加信息>}
"""

from typing import Optional


class ErrorCode:
    # 鉴权
    UNAUTHORIZED = "UNAUTHORIZED"
    INVALID_TOKEN = "INVALID_TOKEN"
    FORBIDDEN = "FORBIDDEN"
    ACCOUNT_DISABLED = "ACCOUNT_DISABLED"
    TENANT_DISABLED = "TENANT_DISABLED"
    # Key 失效
    API_KEY_INVALID = "API_KEY_INVALID"
    API_KEY_REVOKED = "API_KEY_REVOKED"
    # 配额
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    # 限流
    RATE_LIMITED = "RATE_LIMITED"
    # 模型
    MODEL_SERVICE_ERROR = "MODEL_SERVICE_ERROR"
    # 其它通用
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# 错误码 -> (HTTP 状态码, 面向用户的默认提示)
# 提示语刻意不包含"无权限/属于其他租户"等字样,避免泄露资源存在性(FR-B3)
ERROR_SPEC: dict[str, tuple[int, str]] = {
    ErrorCode.UNAUTHORIZED: (401, "未提供认证信息"),
    ErrorCode.INVALID_TOKEN: (401, "登录已过期或无效"),
    ErrorCode.FORBIDDEN: (403, "无权限执行该操作"),
    ErrorCode.ACCOUNT_DISABLED: (403, "账号已被禁用"),
    ErrorCode.TENANT_DISABLED: (403, "租户已停用"),
    ErrorCode.API_KEY_INVALID: (401, "调用密钥无效"),
    ErrorCode.API_KEY_REVOKED: (401, "调用密钥已失效"),
    ErrorCode.QUOTA_EXCEEDED: (429, "本周期用量已超出配额"),
    ErrorCode.RATE_LIMITED: (429, "请求过于频繁，请稍后再试"),
    ErrorCode.MODEL_SERVICE_ERROR: (502, "模型服务暂时不可用，请稍后再试"),
    ErrorCode.VALIDATION_ERROR: (422, "请求参数校验失败"),
    ErrorCode.NOT_FOUND: (404, "资源不存在"),
    ErrorCode.INTERNAL_ERROR: (500, "服务器内部错误"),
    "RATE_LIMITED": (429, "请求频率超过限制，请稍后再试"),
}


class BizError(Exception):
    """业务异常。可只传错误码,状态码与提示自动取自 ERROR_SPEC。

    data:随响应体返回的附加信息(如配额超限时的 used/limit/reset_at,FR-D3 要求)。
    """

    def __init__(
        self,
        code: str,
        message: Optional[str] = None,
        status_code: Optional[int] = None,
        data: Optional[dict] = None,
    ):
        spec = ERROR_SPEC.get(code)
        if spec is not None:
            default_status, default_message = spec
            self.status_code = status_code or default_status
            self.message = message or default_message
        else:
            # 未登记的码:沿用调用方显式传入的值,保证向后兼容
            self.status_code = status_code or 400
            self.message = message or code
        self.code = code
        self.data = data
        super().__init__(self.message)

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "data": self.data}


class QuotaExceededError(BizError):
    """配额超限(FR-D3)。

    响应体固定包含已用量与重置时间,前端据此提示"何时恢复":
        {"code": "QUOTA_EXCEEDED", "message": "...", "data": {
            "used_tokens": int, "limit_tokens": int | None,
            "used_requests": int, "limit_requests": int | None,
            "reset_at": "YYYY-MM-DD"}}
    """

    def __init__(
        self,
        *,
        used_tokens: int,
        limit_tokens: Optional[int],
        used_requests: int,
        limit_requests: Optional[int],
        reset_at: str,
        message: Optional[str] = None,
    ):
        super().__init__(
            ErrorCode.QUOTA_EXCEEDED,
            message,
            data={
                "used_tokens": used_tokens,
                "limit_tokens": limit_tokens,
                "used_requests": used_requests,
                "limit_requests": limit_requests,
                "reset_at": reset_at,
            },
        )
        # 便于调用方直接读属性(不必解析 self.data)
        self.used_tokens = used_tokens
        self.limit_tokens = limit_tokens
        self.used_requests = used_requests
        self.limit_requests = limit_requests
        self.reset_at = reset_at


class ApiKeyInvalidError(BizError):
    """API Key 不存在/已吊销/已过期(FR-C3:吊销后调用立即 401)。"""

    def __init__(self, message: Optional[str] = None):
        super().__init__(ErrorCode.API_KEY_INVALID, message)


def error(code: str, message: Optional[str] = None, status_code: Optional[int] = None) -> BizError:
    """便捷构造:raise error(ErrorCode.QUOTA_EXCEEDED)"""
    return BizError(code, message, status_code)
