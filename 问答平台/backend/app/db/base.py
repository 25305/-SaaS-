from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TenantMixin:
    """所有租户业务数据的公共父类(模块 B 行级隔离,FR-B1)。

    - 继承它的表自动带上 tenant_id 外键列;
    - app/db/tenant_hook.py 的 ORM 事件会自动为这些表注入/校验租户条件,
      新模块(如模块 D 的用量流水)只要继承它就自动获得隔离能力(FR-B4);
    - User/Role/Tenant 不继承本类:登录、注册、用户名全局查重等必须全局查询,
      属"身份层"而非"业务数据层",详见 docs/隔离验证报告.md。
    """

    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
