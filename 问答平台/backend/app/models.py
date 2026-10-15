from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


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


class TenantMixin:
    """所有租户业务数据的公共父类(模块 B 行级隔离,FR-B1)。

    - 继承它的表自动带上 tenant_id 外键列;
    - app/tenant_scope.py 的 ORM 事件会自动为这些表注入/校验租户条件,
      新模块(如模块 D 的用量流水)只要继承它就自动获得隔离能力(FR-B4);
    - User/Role/Tenant 不继承本类:登录、注册、用户名全局查重等必须全局查询,
      属"身份层"而非"业务数据层",详见 docs/隔离验证报告.md。
    """

    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)


class KnowledgeBase(TenantMixin, Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))  # 知识库名称
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # 简介
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Document(TenantMixin, Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)  # 所属知识库
    title: Mapped[str] = mapped_column(String(200))  # 文档标题
    content: Mapped[str] = mapped_column(Text)  # 正文(问答知识库语料)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
