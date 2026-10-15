from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TenantMixin


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)  # super_admin / admin / readonly
    name: Mapped[str] = mapped_column(String(50))  # 平台管理员 / 租户管理员 / 只读成员


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # 成员关系：users.tenant_id 外键即"一个租户对应多个用户"的成员关系
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    role: Mapped[Role] = relationship("Role", lazy="joined")
    tenant: Mapped[Tenant] = relationship("Tenant", lazy="joined")

    @property
    def role_code(self) -> str:
        return self.role.code


class KnowledgeBase(TenantMixin, Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (Index("ix_kb_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))  # 知识库名称
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # 简介
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Document(TenantMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (Index("ix_doc_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)  # 所属知识库
    title: Mapped[str] = mapped_column(String(200))  # 文档标题
    content: Mapped[str] = mapped_column(Text)  # 正文(问答知识库语料)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UsageRecord(TenantMixin, Base):
    """请求用量流水(模块 B,FR-B4)。

    继承 TenantMixin 即自动获得读写隔离:租户 B 的看板/明细查不到租户 A 的数据。
    api_key_id 预留给模块 C 的 API Key 体系;为 NULL 表示用 JWT 登录态访问。
    """

    __tablename__ = "usage_records"
    __table_args__ = (Index("ix_usage_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    api_key_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 预留:发起调用的 Key
    method: Mapped[str] = mapped_column(String(10))  # HTTP 方法
    path: Mapped[str] = mapped_column(String(200), index=True)  # 请求路径
    status_code: Mapped[int] = mapped_column(Integer)  # 响应状态码
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)  # 耗时(毫秒)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
