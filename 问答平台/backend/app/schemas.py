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
