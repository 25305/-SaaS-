from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50, description="用户名，3-50 个字符，全局唯一")
    password: str = Field(min_length=8, max_length=64, description="密码，8-64 个字符")
    email: Optional[str] = Field(default=None, description="邮箱（可选）")
    tenant_code: str = Field(description="租户编码，租户的唯一标识，如 academic")

class LoginRequest(BaseModel):
    username: str = Field(description="用户名")
    password: str = Field(description="密码")
    tenant_code: Optional[str] = Field(default=None, description="租户编码（可选，用户名全局唯一时可不填）")

class TokenResponse(BaseModel):
    access_token: str = Field(description="JWT 访问令牌")
    token_type: str = Field(default="bearer", description="令牌类型")
    expires_in: int = Field(description="有效期（秒）")

class UserInfoResponse(BaseModel):
    id: int = Field(description="用户 ID")
    username: str = Field(description="用户名")
    email: Optional[str] = Field(default=None, description="邮箱")
    role: str = Field(description="角色编码，如 admin / readonly / super_admin")
    is_super_admin: bool = Field(description="是否平台管理员")
    tenant_id: int = Field(description="所属租户 ID")
    tenant_code: Optional[str] = Field(default=None, description="所属租户编码")

class MemberCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50, description="用户名，3-50 个字符，全局唯一")
    password: str = Field(min_length=8, max_length=64, description="密码，8-64 个字符")
    email: Optional[str] = Field(default=None, description="邮箱（可选）")
    role: Literal["admin", "readonly"] = Field(default="readonly", description="角色：admin 租户管理员 / readonly 只读成员")

class MemberUpdate(BaseModel):
    role: Optional[Literal["admin", "readonly"]] = Field(default=None, description="新角色（可选）")
    is_active: Optional[bool] = Field(default=None, description="是否启用（可选）")

class MemberInfo(BaseModel):
    id: int = Field(description="用户 ID")
    username: str = Field(description="用户名")
    email: Optional[str] = Field(default=None, description="邮箱")
    role: str = Field(description="角色编码")
    is_active: bool = Field(description="是否启用")

class TenantCreate(BaseModel):
    code: str = Field(min_length=2, max_length=50, description="租户编码，唯一")
    name: str = Field(min_length=1, max_length=100, description="租户名称")
    admin_username: str = Field(min_length=3, max_length=50, description="首个管理员用户名（必填，否则新租户无人能登录）")
    admin_password: str = Field(min_length=8, max_length=64, description="首个管理员密码")
    admin_email: Optional[str] = Field(default=None, description="管理员邮箱（可选）")

class TenantInfo(BaseModel):
    id: int = Field(description="租户 ID")
    code: str = Field(description="租户编码")
    name: str = Field(description="租户名称")
    is_active: bool = Field(description="是否启用")
    user_count: int = Field(description="成员数量")
    # 配额与超额策略(FR-H2:租户管理台可查看/设置;FR-D6 策略可选)
    quota_monthly_tokens: Optional[int] = Field(default=None, description="月度 Token 上限;null 为不限制")
    quota_monthly_requests: Optional[int] = Field(default=None, description="月度请求次数上限;null 为不限制")
    overage_policy: str = Field(default="reject", description="超额行为:reject 直接拒绝 / soft_warn 软超额告警")


class TenantQuotaUpdate(BaseModel):
    """平台管理员设置租户配额与超额策略(FR-D1/FR-D6)。PATCH 语义:只更新传入字段。"""

    quota_monthly_tokens: Optional[int] = Field(
        default=None, ge=0, description="月度 Token 上限;显式传 null 表示不限制"
    )
    quota_monthly_requests: Optional[int] = Field(
        default=None, ge=0, description="月度请求次数上限;显式传 null 表示不限制"
    )
    overage_policy: Optional[Literal["reject", "soft_warn"]] = Field(
        default=None, description="超额行为:reject 直接拒绝 / soft_warn 软超额告警(FR-D6)"
    )


class TenantQuotaUpdate(BaseModel):
    quota_monthly_tokens: Optional[int] = Field(default=None, ge=0)
    quota_monthly_requests: Optional[int] = Field(default=None, ge=0)
    overage_policy: Literal["reject", "soft_warn"] = Field(default="reject")


class KBCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="知识库名称")
    description: Optional[str] = Field(default=None, max_length=500, description="简介（可选）")


class KBUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100, description="新名称（可选）")
    description: Optional[str] = Field(default=None, max_length=500, description="新简介（可选）")


class KBInfo(BaseModel):
    id: int = Field(description="知识库 ID")
    name: str = Field(description="知识库名称")
    description: Optional[str] = Field(default=None, description="简介")
    doc_count: int = Field(description="文档数量")
    created_at: datetime = Field(description="创建时间")


class DocCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200, description="文档标题")
    content: str = Field(min_length=1, max_length=10000, description="正文内容")


class DocUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200, description="新标题（可选）")
    content: Optional[str] = Field(default=None, min_length=1, max_length=10000, description="新正文（可选）")


class DocInfo(BaseModel):
    id: int = Field(description="文档 ID")
    kb_id: int = Field(description="所属知识库 ID")
    title: str = Field(description="文档标题")
    content: str = Field(description="正文内容")
    created_at: datetime = Field(description="创建时间")


# ---------------- 模块 C:API Key ----------------
class KeyCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=60, description="用途描述,如「官网问答机器人」")
    quota_monthly_tokens: Optional[int] = Field(
        default=None, ge=0, description="该 Key 的月度 Token 上限;不填则继承租户配额"
    )
    quota_monthly_requests: Optional[int] = Field(
        default=None, ge=0, description="该 Key 的月度请求次数上限;不填则继承租户配额"
    )
    rate_limit_per_minute: Optional[int] = Field(
        default=None, ge=1, description="每分钟请求数上限(供限流模块读取)"
    )
    rate_limit_per_hour: Optional[int] = Field(
        default=None, ge=1, description="每小时请求数上限(供限流模块读取)"
    )


class KeyCreatedOut(BaseModel):
    id: int = Field(description="Key ID")
    name: str = Field(description="用途描述")
    key: str = Field(description="完整 Key;**仅此一次返回**,请立即保存")
    masked: str = Field(description="掩码展示,如 sk-abc12345...xyz9")
    warning: str = Field(description="安全提示")
    expire_at: Optional[datetime] = Field(default=None, description="过期时间(轮换时才有)")


class KeyInfo(BaseModel):
    id: int = Field(description="Key ID")
    name: str = Field(description="用途描述")
    masked: str = Field(description="掩码,列表接口绝不返回完整 Key")
    status: str = Field(description="active / expiring / revoked")
    expire_at: Optional[datetime] = Field(default=None, description="过期时间")
    quota_monthly_tokens: Optional[int] = Field(default=None, description="该 Key 的月度 Token 上限")
    quota_monthly_requests: Optional[int] = Field(default=None, description="该 Key 的月度请求次数上限")
    rate_limit_per_minute: Optional[int] = Field(default=None, description="每分钟请求数上限")
    rate_limit_per_hour: Optional[int] = Field(default=None, description="每小时请求数上限")
    last_used_at: Optional[datetime] = Field(default=None, description="最后使用时间(泄漏排查用)")
    last_used_ip: Optional[str] = Field(default=None, description="最后使用来源 IP")
    created_at: datetime = Field(description="创建时间")


# ---------------- 模块 D:问答与配额 ----------------
class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000, description="用户问题")
    model: Optional[str] = Field(
        default=None, description="指定模型名,不填用服务端默认模型"
    )


class QuotaStatusOut(BaseModel):
    allowed: bool = Field(description="当前是否还有配额")
    used_tokens: int = Field(description="本周期已用 Token")
    limit_tokens: Optional[int] = Field(default=None, description="本周期 Token 上限;null 为不限制")
    used_requests: int = Field(description="本周期已用请求次数")
    limit_requests: Optional[int] = Field(default=None, description="本周期请求次数上限;null 为不限制")
    reset_at: str = Field(description="配额重置时间(下个周期起始日)")
    policy: str = Field(description="超额行为:reject / soft_warn")
    key_id: Optional[int] = Field(default=None, description="按 Key 调用时的 Key ID")
